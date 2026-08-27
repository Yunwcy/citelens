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


def ingest(
    path: str | Path,
    strategy: str | None = None,
    use_tables: bool = True,
) -> IngestResult:
    """use_tables=False 供評估的對照組使用。

    關閉後表格區域不再單獨抽取，其文字會以一般段落的形式留在切塊中 ——
    這正是未做表格處理的系統會有的行為，對照才有意義。
    """
    with ParsedPdf(path) as pdf:
        prof = profiler.profile(pdf)
        sections, source = section_detector.detect(pdf, prof)
        prof.section_source = source

        tables = table_extractor.extract(pdf, prof) if use_tables else []
        chunks = section_chunker.chunk_sections(
            sections, strategy, exclude=_in_table(tables)
        )
        chunks.extend(_table_chunks(tables, sections))
        info = _doc_info_chunk(prof, sections)
        if info is not None:
            chunks.insert(0, info)

        return IngestResult(prof, sections, tables, chunks)


def _doc_info_chunk(prof: DocumentProfile, sections: list[Section]) -> Chunk | None:
    """把標題、作者、來源整理成一段可被檢索的敘述。

    這些資訊原本以「一行一個人名」的形式散落在首頁，語意訊號極弱 ——
    問「這篇論文的作者是誰」時完全檢索不到。
    寫成「標題是…、作者為…」的句子後才有得比對，
    與表格逐列線性化是同一個原理：讓資料自己說明自己是什麼。
    """
    front = next((s for s in sections if s.title in ("Abstract", "Front matter")), None)
    if front is None and not prof.title:
        return None

    parts = []
    if prof.title:
        parts.append(f"文件標題（Title）：{prof.title}")
    if front is not None:
        head = " ".join(front.text.split())[:600]
        parts.append(f"文件開頭（作者、單位、摘要 / authors, affiliations, abstract）：{head}")
    parts.append(f"共 {prof.n_pages} 頁，{len(sections)} 個章節。")

    text = "\n".join(parts)
    return Chunk(
        chunk_id="doc-info",
        text=text,
        page=1,
        section_id=front.id if front else "",
        section_title=front.title if front else "Front matter",
        kind="text",
        n_tokens=tokens.count(text),
        meta={"doc_info": True},
    )


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

        # 數值型表格用逐列的「欄名 = 值」呈現，而非 markdown 表格。
        # markdown 的欄位對應要靠位置推斷：多區塊表格中，各區塊的欄名
        # 由分隔列宣告，模型讀到幾列之後就追蹤不到，會把某一欄的值
        # 掛到另一欄的標題下。逐格自我描述則沒有這個問題。
        body = "\n".join(table_extractor.linearize(t)) if t.rows else t.markdown
        if t.rows and table_extractor.is_pairwise(t):
            body = f"{table_extractor.PAIRWISE_WARNING}\n{body}"

        out.append(
            Chunk(
                chunk_id=f"{t.table_id}-full",
                text=f"{head}\n{body}",
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
    """表格所屬章節，用於引用時顯示「p.8 · Ablation Studies」。

    以閱讀順序落在哪個章節區間判定。先前用頁碼比較會出錯 —— 同一頁可能
    橫跨兩個章節，取「最後一個 page_start <= 表格頁」會抓到下一節。
    """
    return next(
        (s for s in sections if s.start_order <= t.order < s.end_order),
        None,
    )
