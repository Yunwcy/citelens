"""文件註冊表、背景索引任務與索引快取。

三個併發要點：
1. 解析與向量化是同步的 CPU 工作，一律以 asyncio.to_thread 移出事件迴圈。
   直接在 async handler 裡跑，整個服務會停住 —— 不是變慢，是所有請求一起等。
2. 重路徑（建索引）與輕路徑（查詢）各自限流。單一上傳若不設限，
   會把 CPU 吃光，導致查詢連一句話的向量化都排不進去。
3. 磁碟是唯一真相來源，記憶體只作 LRU 快取。這樣多工作行程之間不會出現
   「A 上傳的文件 B 找不到」，重啟也不需要重新解析。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from app.config import settings
from app.retrieval.index import DocumentIndex
from app.services import ingest as ingest_mod
from app.summarization import hierarchical

log = logging.getLogger(__name__)

from app.services.limits import INDEX as INDEX_SEM      # noqa: E402  （對外沿用舊名）

_CACHE_SIZE = 8
_cache: OrderedDict[str, DocumentIndex] = OrderedDict()
_cache_lock = asyncio.Lock()

STAGES = ("parsing", "indexing", "summarizing", "ready")


@dataclass(slots=True)
class Job:
    job_id: str
    doc_id: str
    filename: str
    stage: str = "queued"
    error: str | None = None
    detail: dict = field(default_factory=dict)
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.stage == "ready" or self.error is not None

    def emit(self, stage: str, **detail) -> None:
        self.stage = stage
        self.detail.update(detail)
        payload = {"job_id": self.job_id, "doc_id": self.doc_id,
                   "stage": stage, "error": self.error, **self.detail}
        for q in self._subscribers:
            q.put_nowait(payload)

    async def events(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        try:
            # 補送目前狀態，訂閱者晚到也不會錯過已發生的階段
            yield {"job_id": self.job_id, "doc_id": self.doc_id,
                   "stage": self.stage, "error": self.error, **self.detail}
            while not self.done:
                yield await q.get()
        finally:
            self._subscribers.remove(q)


_jobs: dict[str, Job] = {}


# --- 建立 -------------------------------------------------------------------

def doc_id_for(data: bytes) -> str:
    """以內容雜湊作為文件識別。同一份檔案重複上傳會命中既有索引。"""
    return hashlib.sha256(data).hexdigest()[:16]


async def submit_url(url: str) -> Job:
    """由網址匯入。安全檢查在 fetcher，此處只負責接上既有的索引流程。"""
    from app.services.fetcher import fetch

    fetched = await fetch(url)
    return await submit(fetched.title or fetched.filename, fetched.data, source_url=fetched.url)


async def submit(filename: str, data: bytes, source_url: str | None = None) -> Job:
    doc_id = doc_id_for(data)
    job = Job(job_id=f"j{len(_jobs) + 1:05d}", doc_id=doc_id, filename=filename)
    _jobs[job.job_id] = job

    if (settings.storage_dir / doc_id / "meta.json").exists():
        job.emit("ready", cached=True)
        return job

    path = settings.storage_dir / doc_id / "source.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, data)
    (path.parent / "source.json").write_text(
        json.dumps({"filename": filename, "url": source_url,
                    "uploaded": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    asyncio.create_task(_run(job, path))
    return job


async def _run(job: Job, path: Path) -> None:
    """背景索引任務。以 semaphore 序列化，避免多份上傳互相搶 CPU。"""
    try:
        async with INDEX_SEM:
            job.emit("parsing")
            res = await asyncio.to_thread(ingest_mod.ingest, path)
            job.emit(
                "indexing",
                pages=res.profile.n_pages,
                sections=len(res.sections),
                chunks=len(res.chunks),
                tables=len(res.tables),
            )
            idx = await asyncio.to_thread(DocumentIndex.build, job.doc_id, res)
            await asyncio.to_thread(idx.save)
            async with _cache_lock:
                _put(job.doc_id, idx)

        # 摘要在鎖外執行：它是網路等待而非 CPU，不該擋住下一份文件的索引。
        #
        # 而且它是唯一需要外部服務的一步 —— 失敗不得讓整份文件被判定為失敗。
        # 索引到這裡已經完成，文件可以提問；摘要在使用者真的要摘要時會重試。
        # 實測拔掉網路上傳：解析、切塊、向量化全部成功，只有這一步失敗，
        # 但介面顯示「連線錯誤」，看起來像整個上傳都沒成功。
        job.emit("summarizing")
        summary_ok = True
        try:
            await hierarchical.build(job.doc_id, res.sections)
        except Exception as exc:                          # noqa: BLE001
            summary_ok = False
            log.warning("摘要建立失敗，文件仍可提問：%s", exc)
        job.emit("ready", chunks=len(res.chunks), tables=len(res.tables),
                 summary_ready=summary_ok)

    except Exception as exc:                              # noqa: BLE001
        log.exception("索引失敗 %s", job.doc_id)
        job.error = str(exc)
        job.emit("failed")


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


# --- 讀取 -------------------------------------------------------------------

def _put(doc_id: str, idx: DocumentIndex) -> None:
    _cache[doc_id] = idx
    _cache.move_to_end(doc_id)
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)


async def get_index(doc_id: str) -> DocumentIndex:
    async with _cache_lock:
        if doc_id in _cache:
            _cache.move_to_end(doc_id)
            return _cache[doc_id]

    if not (settings.storage_dir / doc_id / "meta.json").exists():
        raise KeyError(doc_id)

    idx = await asyncio.to_thread(DocumentIndex.load, doc_id)
    async with _cache_lock:
        _put(doc_id, idx)
    return idx


def list_documents() -> list[dict]:
    out = []
    for d in sorted(settings.storage_dir.glob("*/meta.json")):
        meta = json.loads(d.read_text(encoding="utf-8"))
        source = d.parent / "source.json"
        info = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {}
        prof = meta.get("profile", {})
        out.append({
            "doc_id": meta["doc_id"],
            "filename": prof.get("title") or info.get("filename", meta["doc_id"]),
            "source_name": info.get("filename", meta["doc_id"]),
            "uploaded": info.get("uploaded"),
            "pages": prof.get("n_pages"),
            "chunks": meta.get("n_chunks"),
            "tables": meta.get("n_tables"),
            "url": info.get("url"),
            "section_source": prof.get("section_source"),
            "has_summary": (d.parent / "summary.json").exists(),
        })
    return sorted(out, key=lambda r: r.get("uploaded") or "", reverse=True)
