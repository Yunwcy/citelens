"""FastAPI 進入點。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import Health, router
from app.config import settings

# Pydantic 的原始驗證錯誤是一整包內部結構（type / loc / ctx），
# 直接回給前端等於把內部細節噴到畫面上。改成一句話。
_VALIDATION_MESSAGE = {
    "string_too_short": "不能是空的",
    "string_too_long": "太長了",
    "missing": "這個欄位是必填的",
    "greater_than_equal": "數值太小",
    "less_than_equal": "數值太大",
    "int_parsing": "必須是整數",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    # 啟動時先載入向量化模型：延後到第一個請求會讓該次多等約一秒，
    # 看起來像是查詢很慢，實際上是模型初始化。
    from app.retrieval.embedding import get_embedder

    get_embedder()

    # 重放既有的指標紀錄：Counter 與 Histogram 都在行程記憶體裡，
    # 重啟後累計成本會變回 $0、引用率變成 No data，但紀錄其實還在。
    from app.observability import metrics as _metrics
    from app.observability import prom as _prom

    try:
        n = _prom.replay(_metrics.read_all())
        if n:
            log.info("已重放 %d 筆既有指標", n)
    except Exception:  # noqa: BLE001
        log.warning("指標重放失敗", exc_info=True)

    # 重新載入既有的評估結果：gauge 不會自行持久化，
    # 重啟後儀表板的準確度面板會變空白。
    eval_path = settings.storage_dir / "eval.json"
    if eval_path.exists():
        import json

        from app.observability import prom

        try:
            prom.publish_eval(json.loads(eval_path.read_text(encoding="utf-8")))
            log.info("已載入既有的評估結果")
        except Exception:  # noqa: BLE001
            log.warning("評估結果載入失敗", exc_info=True)
    log.info("啟動完成 · 檢索預算 %d tokens · 向量化後端 %s",
             settings.retrieval_budget, settings.embedding_backend)
    yield


app = FastAPI(title="CiteLens", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """把驗證錯誤轉成一句話。

    預設回的是 Pydantic 的內部結構（type / loc / ctx），
    前端只會原樣顯示 —— 使用者看到的是一串看不懂的英文。
    """
    err = exc.errors()[0]
    field = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query"))
    reason = _VALIDATION_MESSAGE.get(err.get("type", ""), err.get("msg", "格式不正確"))
    return JSONResponse(status_code=422,
                        content={"detail": f"{field}{reason}" if field else reason})


app.include_router(router)


@app.get("/health")
async def health() -> Health:
    return {"status": "ok", "retrieval_budget": settings.retrieval_budget}
