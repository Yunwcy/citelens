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


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    async for e in events:
        yield _sse(e)


@router.post("/eval")
async def publish_eval(report: dict) -> dict:
    """接收 scripts/eval.py 產生的評估結果並轉成 Prometheus gauge。

    檢索準確度需要事先標註每個查詢的目標章節，無法在每次查詢時即時計算，
    因此由離線評估產生後發布到這裡，與執行指標呈現在同一張儀表板上。
    """
    path = settings.storage_dir / "eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prom.publish_eval(report)
    return {"ok": True, "configs": list(report.get("retrieval", {}))}


@router.get("/eval")
async def get_eval() -> dict:
    path = settings.storage_dir / "eval.json"
    if not path.exists():
        raise HTTPException(404, "尚未發布評估結果")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus 抓取端點。與 metrics.jsonl 為同一組數字的不同輸出格式。"""
    return Response(prom.render(), media_type="text/plain; version=0.0.4")


# --- 文件 -------------------------------------------------------------------

@router.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...)) -> dict:
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
async def upload_from_url(req: UrlRequest) -> dict:
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
async def list_docs() -> list[dict]:
    return documents.list_documents()


@router.get("/documents/{doc_id}")
async def get_doc(doc_id: str) -> dict:
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
        "quick_questions": _quick_questions(idx),
    }


@router.get("/documents/{doc_id}/summary")
async def get_summary(doc_id: str) -> dict:
    data = hierarchical.load(doc_id)
    if data is None:
        raise HTTPException(404, "摘要尚未產生")
    return data


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = documents.get_job(job_id)
    if job is None:
        raise HTTPException(404, "找不到這個工作")
    return StreamingResponse(_stream(job.events()), media_type="text/event-stream")


# --- 查詢 -------------------------------------------------------------------

@router.post("/query")
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
async def query_sync(req: QueryRequest) -> dict:
    """非串流版本，供評估腳本與測試使用。"""
    try:
        idx = await documents.get_index(req.doc_id)
    except KeyError:
        raise HTTPException(404, "找不到這份文件") from None
    res = await qa.answer(idx, req.question, top_k=req.top_k)
    return {"answer": res.text, "sources": [asdict(s) for s in res.sources], "debug": res.debug}


def _quick_questions(idx) -> list[str]:
    """快速提問：三個固定項，再依偵測到的章節自動長出最多兩個。

    這是介面上「不懂這個系統的人也能完成一次操作」的主要機制，
    同時也在示範章節偵測確實有效。
    """
    out = ["摘要這份文件", "比較文中提到的方法", "找出表格中的數據"]
    titles = [c.section_title for c in idx.chunks if c.section_title]
    seen: list[str] = []
    for kw, question in (
        ("ablation", "消融實驗的結果如何？"),
        ("evaluation", "實驗是怎麼設計的？"),
        ("cost", "成本與效率的比較結果是什麼？"),
        ("related work", "與既有方法的差異在哪裡？"),
    ):
        if any(kw in t.lower() for t in titles) and len(seen) < 2:
            seen.append(question)
    return out + seen
