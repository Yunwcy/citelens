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
from app.models import Block, Chunk, Section, Table
from app.retrieval.bm25 import Bm25Index
from app.retrieval.embedding import get_embedder
from app.retrieval.hybrid import Hit, rrf
from app.observability import metrics
from app.retrieval.vector import VectorIndex
from app.services.ingest import IngestResult

log = logging.getLogger(__name__)


_MAX_ROWS_PER_TABLE = 2


def _embed_text(chunk: Chunk) -> str:
    """向量化時把章節標題併入內容。

    片段本身不含標題時，向量只反映段落文字，模型無從得知它屬於哪一節。
    問「雙層檢索是什麼意思」這種以章節主題發問的問題，
    正確段落因此排不上前面 —— 尤其中文查詢，BM25 對英文文件完全無法貢獻，
    等於只靠向量一半的力量。
    """
    return f"{chunk.section_title}\n{chunk.text}" if chunk.section_title else chunk.text


def _cap_per_table(hits: list[Hit], chunks: list[Chunk]) -> list[Hit]:
    """同一張表最多保留兩列。

    一張 16 列的表格會產生 16 個片段，語意相近，命中時容易連續佔滿前十名，
    把其他章節整個擠掉 —— 實測「圖索引是怎麼建立的」這類問題，
    正確章節因此由第 1 名掉到第 5 名。

    這麼做不會損失資訊：脈絡組裝階段本來就會在命中任一列時把整張表帶入，
    保留兩列已足以觸發，其餘名額留給不同章節的內容。
    """
    seen: dict[str, int] = {}
    out: list[Hit] = []
    for h in hits:
        tid = chunks[h.index].meta.get("table_id")
        if chunks[h.index].kind == "table_row" and tid:
            seen[tid] = seen.get(tid, 0) + 1
            if seen[tid] > _MAX_ROWS_PER_TABLE:
                continue
        out.append(h)
    return out


class DocumentIndex:
    def __init__(self, doc_id: str, chunks: list[Chunk], vectors: np.ndarray,
                 tables: list[Table], meta: dict, sections: list[Section] | None = None):
        self.doc_id = doc_id
        # 索引必須記住是哪個後端建的。查詢時若改用預設後端，兩邊的向量空間
        # 不同，結果會是錯的 —— 維度剛好相同時甚至不會報錯，只會靜默地爛掉。
        self.backend = meta.get("embedding_backend")
        # 章節必須持久化：伺服器重啟後從磁碟載入索引時，若章節為空，
        # 摘要重建會拿到空清單而靜默產出空摘要。
        self.sections = sections or []
        self.chunks = chunks
        self.tables = {t.table_id: t for t in tables}
        self.meta = meta
        self.vector = VectorIndex(vectors)
        self.bm25 = Bm25Index([_embed_text(c) for c in chunks])

    # --- 建立與持久化 -----------------------------------------------------

    @classmethod
    def build(cls, doc_id: str, res: IngestResult, backend: str | None = None) -> "DocumentIndex":
        embedder = get_embedder(backend, purpose="index")
        started = time.perf_counter()
        vectors = embedder.embed_passages([_embed_text(c) for c in res.chunks])
        elapsed = time.perf_counter() - started
        # 建索引後再預熱查詢通道：實測 passage 與 query 各有獨立的初始化成本，
        # 不在這裡付掉，就會由使用者的第一次查詢承擔約一秒的延遲。
        embedder.embed_query("warmup")

        meta = {
            "doc_id": doc_id,
            "profile": asdict(res.profile),
            "n_chunks": len(res.chunks),
            "n_tables": len(res.tables),
            "embedding_backend": backend or settings.embedding_backend,
            "embedding_model": embedder.name,
            "embedding_dim": embedder.dim,
            "index_seconds": round(elapsed, 2),
            "chunks_per_second": round(len(res.chunks) / max(elapsed, 1e-6), 1),
        }
        log.info("索引完成 %s：%d 片段，%.1fs", doc_id, len(res.chunks), elapsed)
        metrics.record(
            "index",
            doc_id=doc_id,
            pages=res.profile.n_pages,
            columns=res.profile.columns,
            section_source=res.profile.section_source,
            sections=len(res.sections),
            chunks=len(res.chunks),
            text_chunks=len(res.text_chunks),
            table_chunks=len(res.table_chunks),
            tables=len(res.tables),
            tables_validated=sum(1 for t in res.tables if t.validated),
            embedding_backend=meta["embedding_backend"],
            embedding_model=embedder.name,
            index_seconds=meta["index_seconds"],
            chunks_per_second=meta["chunks_per_second"],
            api_calls=0,   # 向量化全程本地，見 retrieval/embedding.py
        )
        return cls(doc_id, res.chunks, vectors, res.tables, meta, res.sections)

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
        (self.dir / "sections.json").write_text(
            json.dumps([asdict(s) for s in self.sections], ensure_ascii=False), encoding="utf-8"
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

        sections: list[Section] = []
        sec_path = d / "sections.json"
        if sec_path.exists():
            for raw in json.loads(sec_path.read_text(encoding="utf-8")):
                blocks = [Block(**b) for b in raw.pop("blocks", [])]
                sections.append(Section(**raw, blocks=blocks))

        return cls(doc_id, chunks, vectors, tables, meta, sections)

    # --- 檢索 -------------------------------------------------------------

    def search(self, query: str, top_k: int | None = None, mode: str | None = None) -> list[Hit]:
        """回傳融合後的命中，附上各檢索器的名次供除錯顯示。"""
        top_k = top_k or settings.top_k
        mode = mode or settings.retrieval_mode
        pool = max(top_k * 3, 20)          # 融合前各取較多候選，讓名次有意義

        qv = get_embedder(self.backend).embed_query(query)
        rankings = {"vector": self.vector.search(qv, pool)}
        if mode == "hybrid":
            rankings["bm25"] = self.bm25.search(query, pool)

        return _cap_per_table(rrf(rankings), self.chunks)[:top_k]

    def chunk(self, index: int) -> Chunk:
        return self.chunks[index]

    def cite(self, index: int) -> str:
        c = self.chunks[index]
        return f"p.{c.page}" + (f" · {c.section_title}" if c.section_title else "")
