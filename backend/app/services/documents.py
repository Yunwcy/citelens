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
import shutil
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
        """索引完成即可提問。摘要在背景繼續，另以 finished 判定全部結束。"""
        return self.stage == "ready" or self.error is not None

    @property
    def finished(self) -> bool:
        """含背景摘要在內全部結束。串流要等到這裡才關 ——
        `ready` 之後還有一則補上 summary_ready 的事件。"""
        return self.error is not None or "summary_ready" in self.detail

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
            while not self.finished:
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

        # 索引完成即可提問 —— 摘要不該擋住其他問題。
        # 先前是等摘要做完才發 ready，使用者要等 32 秒才能問任何問題，
        # 即使那個問題根本不需要摘要。
        job.emit("ready", chunks=len(res.chunks), tables=len(res.tables))
        asyncio.create_task(_prebuild_summaries(job, res.sections))

    except Exception as exc:                              # noqa: BLE001
        log.exception("索引失敗 %s", job.doc_id)
        job.error = str(exc)
        job.emit("failed")


async def _prebuild_summaries(job: Job, sections: list) -> None:
    """在背景預建兩種語言的摘要。

    摘要是整條管線唯一需要外部服務的一步，失敗不得讓文件被判定為失敗 ——
    索引已經完成，文件可以提問。實測拔掉網路上傳：解析、切塊、向量化
    全部成功，只有這一步失敗。

    兩種語言都建：章節摘要只做一次（與語言無關），第二種語言只多一次
    整合呼叫。介面是雙語的，只預建一種等於使用者切換語言後要現場等。
    建立中途若有人要求摘要，hierarchical 的鎖會讓它等這次完成，
    而不是另起一次。
    """
    job.emit("summarizing")
    ok = True
    try:
        for lang in ("zh", "en"):
            await hierarchical.build(job.doc_id, sections, lang)
    except Exception as exc:                              # noqa: BLE001
        ok = False
        log.warning("摘要建立失敗，文件仍可提問：%s", exc)
    # 沿用 ready 這個 stage，只補上細節 —— 換成別的 stage 會讓 job.done
    # 由 True 變回 False，等待這個工作的呼叫端會就此空等。
    job.emit("ready", summary_ready=ok)


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


# --- 讀取 -------------------------------------------------------------------

async def delete(doc_id: str) -> bool:
    """刪除一份文件：磁碟與記憶體快取都要清掉。

    只清其中一邊會留下不一致的狀態 —— 清了磁碟但快取還在，
    這份文件仍然答得出問題，卻已經不在清單裡。
    """
    path = settings.storage_dir / doc_id
    if not path.is_dir():
        return False
    async with _cache_lock:
        _cache.pop(doc_id, None)
    await asyncio.to_thread(shutil.rmtree, path)
    log.info("已刪除文件 %s", doc_id)
    return True


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
