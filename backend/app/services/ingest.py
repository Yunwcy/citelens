"""文件匯入管線：解析 → 特徵偵測 → 章節 → 表格 → 切塊。

這一層負責把各個模組串起來，並確保表格內容只出現一次：
表格區域的文字區塊會從一般文字切塊中排除，改由表格抽取器產出的
專屬片段代表，避免同一份數據以兩種品質不一的形式同時進入索引。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.chunking import section_chunker, section_detector
from app.models import Block, Chunk, DocumentProfile, Section, Table
from app.parser import profiler, table_extractor
from app.parser.pdf_parser import ParsedPdf
from app.util import tokens

_CAPTION_MARGIN = 90.0      # 表格上方的標號一併排除，避免重複入索引


@dataclass(slots=True)
class IngestResult:
    profile: DocumentProfile
    sections: list[Section]
    tables: list[Table]
    chunks: list[Chunk]

    @property
    def text_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if c.kind == "text"]

    @property
    def table_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if c.kind != "text"]


def ingest(path: str | Path, strategy: str | None = None) -> IngestResult:
    with ParsedPdf(path) as pdf:
        prof = profiler.profile(pdf)
        sections, source = section_detector.detect(pdf, prof)
        prof.section_source = source

        tables = table_extractor.extract(pdf, prof)
        chunks = section_chunker.chunk_sections(
            sections, strategy, exclude=_in_table(tables)
        )
        chunks.extend(_table_chunks(tables, sections))

        return IngestResult(prof, sections, tables, chunks)


def _in_table(tables: list[Table]):
    """判斷一個文字區塊是否落在某個表格區域內。"""
    regions = [(t.page, t.y0 - _CAPTION_MARGIN, t.y1) for t in tables]

    def predicate(b: Block) -> bool:
        return any(
            page == b.page and lo <= b.y0 <= hi and b.y1 <= hi + 20
            for page, lo, hi in regions
        )

    return predicate


def _table_chunks(tables: list[Table], sections: list[Section]) -> list[Chunk]:
    """每張表產出一個整表片段；數值表另外逐列線性化。

    兩種表徵並存是刻意的：整表適合「比較」類問題閱讀全貌，
    逐列則讓「某個數值是多少」這種問題能精準命中。
    """
    out: list[Chunk] = []
    for t in tables:
        sec = _owning_section(t, sections)
        head = f"{t.caption or t.table_id}（第 {t.page} 頁）"

        out.append(
            Chunk(
                chunk_id=f"{t.table_id}-full",
                text=f"{head}\n{t.markdown}",
                page=t.page,
                section_id=sec.id if sec else "",
                section_title=sec.title if sec else "",
                kind="table_full",
                n_tokens=tokens.count(t.markdown),
                meta={"table_id": t.table_id, "validated": t.validated,
                      "note": t.validation_note, "strategy": t.strategy},
            )
        )

        for i, line in enumerate(table_extractor.linearize(t)):
            out.append(
                Chunk(
                    chunk_id=f"{t.table_id}-r{i:02d}",
                    text=line,
                    page=t.page,
                    section_id=sec.id if sec else "",
                    section_title=sec.title if sec else "",
                    kind="table_row",
                    n_tokens=tokens.count(line),
                    meta={"table_id": t.table_id, "row": t.rows[i][0]},
                )
            )
    return out


def _owning_section(t: Table, sections: list[Section]) -> Section | None:
    """表格所屬章節，用於引用時顯示「p.8 · 4.3 Ablation Studies」。"""
    best = None
    for s in sections:
        if any(b.page == t.page and b.y0 <= t.y0 for b in s.blocks) or s.page_start <= t.page:
            best = s
    return best
