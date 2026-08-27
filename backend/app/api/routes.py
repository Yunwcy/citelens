"""HTTP 介面。

所有可能阻塞的工作都已在服務層以 to_thread 移出事件迴圈；
這一層只負責驗證輸入、串接服務，以及把事件轉成 SSE。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.observability import prom
from app.services import documents, qa
from app.summarization import hierarchical

router = APIRouter(prefix="/api")


class UrlRequest(BaseModel):
    url: str = Field(min_length=4, max_length=500)


class QueryRequest(BaseModel):
    doc_id: str
    question: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=30)


# 回應也要有型別。只標 -> dict 的話，自動產生的 API 文件只會顯示
# additionalProp1 這個「內容不明的物件」佔位符 —— 等於沒有文件。
class JobAccepted(BaseModel):
    """上傳已受理。索引在背景進行，用 job_id 訂閱進度。"""
    job_id: str
    doc_id: str
    stage: str


class DocSummary(BaseModel):
    """文件清單的單筆。filename 優先取解析出的論文標題，取不到才用檔名。"""
    doc_id: str
    filename: str
    source_name: str
    uploaded: str | None = None
    pages: int | None = None
    chunks: int | None = None
    tables: int | None = None
    url: str | None = None
    section_source: str | None = None
    has_summary: bool


class TableInfo(BaseModel):
    """validated 為 False 者已清空儲存格、退回整表原文，note 說明原因。"""
    table_id: str
    page: int
    caption: str = ""
    kind: str
    rows: int
    columns: int
    validated: bool
    note: str = ""


class DocDetail(BaseModel):
    doc_id: str
    meta: dict
    tables: list[TableInfo]
    quick_questions: list[str]


class SectionSummary(BaseModel):
    section: str
    page: int = 0
    summary: str


class SummaryResponse(BaseModel):
    """map-reduce 的兩層產物都保留：section_summaries 是逐節的（與語言無關），
    summaries 是各語言的整合結果，summary 是本次要求的那個語言。"""
    doc_id: str
    summary: str
    lang: str
    section_summaries: list[SectionSummary]
    summaries: dict[str, str]
    n_llm_calls: int = 0
    build_seconds: float = 0.0


class SourceOut(BaseModel):
    """cited 為 False 表示這段有送進模型，但答案沒有引用它。"""
    n: int
    page: int
    section: str
    kind: str
    chunk_id: str
    score: float
    text: str
    cited: bool


class AnswerResponse(BaseModel):
    """非串流版本的回應。debug 內容隨路由而異，故不再細分。"""
    answer: str
    sources: list[SourceOut]
    debug: dict


class Health(BaseModel):
    status: str
    retrieval_budget: int


class RetrievalResult(BaseModel):
    """rank 為目標章節的名次，None 表示未進入結果；
    covered / expected 用於一題有多個期望章節的情況。"""
    rank: int | None = None
    covered: int = 0
    expected: int = 1


class EvalReport(BaseModel):
    """離線評估的結果。

    外層的鍵是動態的（設定名、查詢字串），但葉節點的形狀是固定的 ——
    因此標成 dict[str, Model] 而不是裸 dict：鍵不可知不代表值也不可知。
    """
    retrieval: dict[str, dict[str, RetrievalResult]] = {}
    tables: dict[str, int] = {}


class EvalPublished(BaseModel):
    ok: bool
    configs: list[str]


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    async for e in events:
        yield _sse(e)


@router.post("/eval")
async def publish_eval(report: EvalReport) -> EvalPublished:
    """接收 scripts/eval.py 產生的評估結果並轉成 Prometheus gauge。

    檢索準確度需要事先標註每個查詢的目標章節，無法在每次查詢時即時計算，
    因此由離線評估產生後發布到這裡，與執行指標呈現在同一張儀表板上。
    """
    data = report.model_dump()
    path = settings.storage_dir / "eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    prom.publish_eval(data)
    return EvalPublished(ok=True, configs=list(data["retrieval"]))


@router.get("/eval")
async def get_eval() -> EvalReport:
    path = settings.storage_dir / "eval.json"
    if not path.exists():
        raise HTTPException(404, "尚未發布評估結果")
    return EvalReport(**json.loads(path.read_text(encoding="utf-8")))


@router.get("/metrics", response_class=Response,
            responses={200: {"content": {"text/plain": {}},
                             "description": "Prometheus 文字格式"}})
async def metrics() -> Response:
    """Prometheus 抓取端點。與 metrics.jsonl 為同一組數字的不同輸出格式。"""
    return Response(prom.render(), media_type="text/plain; version=0.0.4")


# --- 文件 -------------------------------------------------------------------

@router.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...)) -> JobAccepted:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只接受 PDF 檔")

    data = await file.read()
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(413, f"檔案超過 {settings.max_upload_mb}MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "這個檔案不是 PDF")

    job = await documents.submit(file.filename, data)
    return {"job_id": job.job_id, "doc_id": job.doc_id, "stage": job.stage}


@router.post("/documents/from-url", status_code=202)
async def upload_from_url(req: UrlRequest) -> JobAccepted:
    """由網址匯入。作業指定的文件本身就是一個網址，貼上比開檔案視窗自然。"""
    from app.services.fetcher import UnsafeUrl

    try:
        job = await documents.submit_url(req.url)
    except UnsafeUrl as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:                              # noqa: BLE001
        raise HTTPException(502, f"無法取得檔案：{exc}") from None
    return {"job_id": job.job_id, "doc_id": job.doc_id, "stage": job.stage}


@router.get("/documents")
async def list_docs() -> list[DocSummary]:
    return documents.list_documents()


@router.get("/documents/{doc_id}")
async def get_doc(doc_id: str, lang: str = "zh") -> DocDetail:
    try:
        idx = await documents.get_index(doc_id)
    except KeyError:
        raise HTTPException(404, "找不到這份文件") from None
    return {
        "doc_id": doc_id,
        "meta": idx.meta,
        "tables": [
            {"table_id": t.table_id, "page": t.page, "caption": t.caption,
             "kind": t.kind, "rows": len(t.rows), "columns": len(t.columns),
             "validated": t.validated, "note": t.validation_note}
            for t in idx.tables.values()
        ],
        "quick_questions": _quick_questions(idx, lang),
    }


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_doc(doc_id: str) -> Response:
    """刪除文件及其索引、摘要快取。"""
    if not await documents.delete(doc_id):
        raise HTTPException(404, "找不到這份文件")
    return Response(status_code=204)


@router.get("/documents/{doc_id}/summary")
async def get_summary(doc_id: str, lang: str = "zh") -> SummaryResponse:
    data = hierarchical.load(doc_id, "en" if lang.startswith("en") else "zh")
    if data is None:
        raise HTTPException(404, "摘要尚未產生")
    return data


@router.get("/jobs/{job_id}/events", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}},
                             "description": "索引進度事件流"}})
async def job_events(job_id: str) -> StreamingResponse:
    job = documents.get_job(job_id)
    if job is None:
        raise HTTPException(404, "找不到這個工作")
    return StreamingResponse(_stream(job.events()), media_type="text/event-stream")


# --- 查詢 -------------------------------------------------------------------

@router.post("/query", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}},
                             "description": "SSE：route → stage → token… → done"}})
async def query(req: QueryRequest) -> StreamingResponse:
    try:
        idx = await documents.get_index(req.doc_id)
    except KeyError:
        raise HTTPException(404, "找不到這份文件") from None
    return StreamingResponse(
        _stream(qa.answer_stream(idx, req.question, req.top_k)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/query/sync")
async def query_sync(req: QueryRequest) -> AnswerResponse:
    """非串流版本，供評估腳本與測試使用。"""
    try:
        idx = await documents.get_index(req.doc_id)
    except KeyError:
        raise HTTPException(404, "找不到這份文件") from None
    res = await qa.answer(idx, req.question, top_k=req.top_k)
    return {"answer": res.text, "sources": [asdict(s) for s in res.sources], "debug": res.debug}


_QUICK_FIXED = {
    "zh": ["摘要這份文件", "比較文中提到的方法", "找出表格中的數據"],
    "en": ["Summarize this document", "Compare the methods discussed",
           "Find the numbers in the tables"],
}

# 依偵測到的章節自動生成。問題以人工撰寫，非逐字翻譯 ——
# 兩種語言各自要像該語言的使用者會問的方式。
_QUICK_BY_SECTION = [
    ("ablation", {"zh": "消融實驗的結果如何？", "en": "What do the ablation studies show?"}),
    ("evaluation", {"zh": "實驗是怎麼設計的？", "en": "How were the experiments set up?"}),
    ("cost", {"zh": "成本與效率的比較結果是什麼？", "en": "How do cost and efficiency compare?"}),
    ("related work", {"zh": "與既有方法的差異在哪裡？", "en": "How does it differ from prior work?"}),
]


def _quick_questions(idx, lang: str = "zh") -> list[str]:
    """快速提問：三個固定項，再依偵測到的章節自動長出最多兩個。

    這是介面上「不懂這個系統的人也能完成一次操作」的主要機制，
    同時也在示範章節偵測確實有效。
    """
    lang = "en" if lang.startswith("en") else "zh"
    out = list(_QUICK_FIXED[lang])
    titles = [c.section_title for c in idx.chunks if c.section_title]
    extra: list[str] = []
    for kw, question in _QUICK_BY_SECTION:
        if any(kw in t.lower() for t in titles) and len(extra) < 2:
            extra.append(question[lang])
    return out + extra
