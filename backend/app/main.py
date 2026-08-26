"""FastAPI 進入點。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    # 啟動時先載入向量化模型：延後到第一個請求會讓該次多等約一秒，
    # 看起來像是查詢很慢，實際上是模型初始化。
    from app.retrieval.embedding import get_embedder

    get_embedder()
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
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "retrieval_budget": settings.retrieval_budget}
