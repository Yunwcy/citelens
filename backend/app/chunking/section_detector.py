"""章節偵測級聯。

實測發現：單一編號正規表達式在目標論文上命中數為 0（章節編號與標題在
文字抽取後被拆成不同行），因此不採用單一規則，改為四級級聯，
並回報實際採用的是哪一級。
"""
from __future__ import annotations

import re
from collections import Counter

from app.models import Block, DocumentProfile, Section
from app.parser.pdf_parser import ParsedPdf

_MIN_SECTIONS = 3          # 少於此數視為該級失敗，往下一級
_MAX_HEADING_CHARS = 90

# 編號標題的多種寫法。順序無關，全部試過取聯集。
_HEADING_PATTERNS = [
    re.compile(r"^(\d+(?:\.\d+)*)\.?\s*\n?\s*(\S.{2,80})$", re.S),      # 1.2 標題（可跨行）
    re.compile(r"^第\s*[一二三四五六七八九十百千\d]+\s*[章節篇]\s*(.{0,80})$", re.S),
    re.compile(r"^(?:Chapter|Section|Part)\s+(?:\d+|[IVXLC]+)\b(.{0,80})$", re.S | re.I),
    re.compile(r"^([IVXLC]+)\.\s+(\S.{2,80})$", re.S),                   # IV. 標題
]

# 學術文獻常見的無編號標題
_BARE_HEADINGS = {
    "abstract", "introduction", "background", "related work", "method", "methods",
    "methodology", "approach", "experiments", "experimental setup", "evaluation",
    "results", "discussion", "ablation study", "ablation studies", "limitations",
    "conclusion", "conclusions", "references", "acknowledgments", "acknowledgements",
    "appendix",
}


def detect(pdf: ParsedPdf, prof: DocumentProfile) -> tuple[list[Section], str]:
    """回傳（章節清單, 採用的級別）。全部失敗時回傳空清單與 "none"。"""
    for fn, name in ((_from_toc, "toc"), (_from_regex, "regex"), (_from_font, "font")):
        sections = fn(pdf, prof)
        if len(sections) >= _MIN_SECTIONS:
            _assign_blocks(pdf, sections)
            return sections, name
    return [], "none"


# --- 第一級：PDF 內建大綱 ---------------------------------------------------

def _from_toc(pdf: ParsedPdf, _prof: DocumentProfile) -> list[Section]:
    """最精確也最便宜的一級。實測四篇論文中三篇可直接取得完整章節樹。"""
    entries = pdf.toc()
    if len(entries) < _MIN_SECTIONS:
        return []

    # 不排序：大綱本身的順序就是權威順序，逐項以 min_order 強制單調遞增。
    located: list[tuple[float, str, int]] = []
    prev = -1.0
    for level, title, page in entries:
        order = pdf.locate(title, page, min_order=prev)
        if order is None:
            order = max(prev + 1, (max(page, 1) - 1) * 10_000)   # 定位失敗時退回頁首
        prev = order
        located.append((order, title.strip(), level))

    return _build(located)


# --- 第二級：編號與常見標題 -------------------------------------------------

def _from_regex(pdf: ParsedPdf, _prof: DocumentProfile) -> list[Section]:
    found: list[tuple[float, str, int]] = []
    for b in pdf.blocks():
        text = b.text.strip()
        if len(text) > _MAX_HEADING_CHARS:
            continue
        if text.lower().rstrip(" .:：") in _BARE_HEADINGS:
            found.append((b.order, " ".join(text.split()), 1))
            continue
        for pat in _HEADING_PATTERNS:
            m = pat.match(text)
            if m:
                num = m.group(1) if m.lastindex else ""
                level = num.count(".") + 1 if re.match(r"^\d", num or "") else 1
                found.append((b.order, " ".join(text.split()), level))
                break
    return _build(found)


# --- 第三級：字型分群 -------------------------------------------------------

def _from_font(pdf: ParsedPdf, prof: DocumentProfile) -> list[Section]:
    """標題通常比內文大。字級排名即階層。"""
    if not prof.heading_font_sizes:
        return []
    rank = {size: i + 1 for i, size in enumerate(sorted(prof.heading_font_sizes, reverse=True))}
    found = [
        (b.order, " ".join(b.text.split()), rank[b.size])
        for b in pdf.blocks()
        if b.size in rank and len(b.text.strip()) <= _MAX_HEADING_CHARS
    ]
    return _build(found)


# --- 共用 -------------------------------------------------------------------

def _build(found: list[tuple[float, str, int]]) -> list[Section]:
    """把（位置, 標題, 層級）轉成有起訖範圍的章節，並強制位置單調遞增。"""
    sections: list[Section] = []
    last = -1.0
    for order, title, level in found:
        if order <= last:          # 定位失敗導致的亂序，跳過而非讓範圍錯亂
            continue
        last = order
        sections.append(
            Section(id=f"s{len(sections):03d}", title=title, level=level,
                    start_order=order, end_order=float("inf"))
        )
    for i in range(len(sections) - 1):
        sections[i].end_order = sections[i + 1].start_order
    return sections


def _assign_blocks(pdf: ParsedPdf, sections: list[Section]) -> None:
    """把文字區塊分配到所屬章節，並濾掉頁首頁尾。"""
    blocks = pdf.blocks()
    noise = _running_headers(blocks, pdf.n_pages)

    i = 0
    for b in blocks:
        if b.text in noise:
            continue
        while i + 1 < len(sections) and b.order >= sections[i + 1].start_order:
            i += 1
        if b.order >= sections[i].start_order:
            sections[i].blocks.append((b.page, b.text))


def _running_headers(blocks: list[Block], n_pages: int, ratio: float = 0.3) -> set[str]:
    """在多數頁面重複出現的短文字視為頁首頁尾。"""
    if n_pages < 4:
        return set()
    counts = Counter(b.text for b in blocks if len(b.text) < 80)
    threshold = max(2, int(n_pages * ratio))
    return {t for t, c in counts.items() if c >= threshold}
