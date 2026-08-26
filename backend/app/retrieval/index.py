"""文件索引：片段、向量、關鍵字三者的組合，以及磁碟持久化。

磁碟是唯一真相來源。記憶體只作為快取，這樣多個工作行程之間不會出現
「A 上傳的文件 B 找不到」的情形，重啟也不需要重新解析。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from app.config import settings
from app.models import Chunk, Table
from app.retrieval.bm25 import Bm25Index
from app.retrieval.embedding import get_embedder
from app.retrieval.hybrid import Hit, rrf
from app.retrieval.vector import VectorIndex
from app.services.ingest import IngestResult

log = logging.getLogger(__name__)


class DocumentIndex:
    def __init__(self, doc_id: str, chunks: list[Chunk], vectors: np.ndarray,
                 tables: list[Table], meta: dict):
        self.doc_id = doc_id
        self.chunks = chunks
        self.tables = {t.table_id: t for t in tables}
        self.meta = meta
        self.vector = VectorIndex(vectors)
        self.bm25 = Bm25Index([c.text for c in chunks])

    # --- 建立與持久化 -----------------------------------------------------

    @classmethod
    def build(cls, doc_id: str, res: IngestResult, backend: str | None = None) -> "DocumentIndex":
        embedder = get_embedder(backend)
        started = time.perf_counter()
        vectors = embedder.embed_passages([c.text for c in res.chunks])
        elapsed = time.perf_counter() - started
        # 建索引後再預熱查詢通道：實測 passage 與 query 各有獨立的初始化成本，
        # 不在這裡付掉，就會由使用者的第一次查詢承擔約一秒的延遲。
        embedder.embed_query("warmup")

        meta = {
            "doc_id": doc_id,
            "profile": asdict(res.profile),
            "n_chunks": len(res.chunks),
            "n_tables": len(res.tables),
            "embedding_model": embedder.name,
            "embedding_dim": embedder.dim,
            "index_seconds": round(elapsed, 2),
            "chunks_per_second": round(len(res.chunks) / max(elapsed, 1e-6), 1),
        }
        log.info("索引完成 %s：%d 片段，%.1fs", doc_id, len(res.chunks), elapsed)
        return cls(doc_id, res.chunks, vectors, res.tables, meta)

    @property
    def dir(self) -> Path:
        return settings.storage_dir / self.doc_id

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "chunks.json").write_text(
            json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False), encoding="utf-8"
        )
        (self.dir / "tables.json").write_text(
            json.dumps([asdict(t) for t in self.tables.values()], ensure_ascii=False),
            encoding="utf-8",
        )
        (self.dir / "meta.json").write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.vector.save(self.dir / "vectors.npy")

    @classmethod
    def load(cls, doc_id: str) -> "DocumentIndex":
        d = settings.storage_dir / doc_id
        chunks = [Chunk(**c) for c in json.loads((d / "chunks.json").read_text(encoding="utf-8"))]
        tables = [Table(**t) for t in json.loads((d / "tables.json").read_text(encoding="utf-8"))]
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        vectors = np.load(d / "vectors.npy")
        return cls(doc_id, chunks, vectors, tables, meta)

    # --- 檢索 -------------------------------------------------------------

    def search(self, query: str, top_k: int | None = None, mode: str | None = None) -> list[Hit]:
        """回傳融合後的命中，附上各檢索器的名次供除錯顯示。"""
        top_k = top_k or settings.top_k
        mode = mode or settings.retrieval_mode
        pool = max(top_k * 3, 20)          # 融合前各取較多候選，讓名次有意義

        qv = get_embedder().embed_query(query)
        rankings = {"vector": self.vector.search(qv, pool)}
        if mode == "hybrid":
            rankings["bm25"] = self.bm25.search(query, pool)

        return rrf(rankings)[:top_k]

    def chunk(self, index: int) -> Chunk:
        return self.chunks[index]

    def cite(self, index: int) -> str:
        c = self.chunks[index]
        return f"p.{c.page}" + (f" · {c.section_title}" if c.section_title else "")
