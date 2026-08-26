"""文件特徵偵測。

上傳時先跑一次，結果決定章節偵測與表格抽取要走哪一條策略。
每一項判斷都會回報依據，供除錯面板與 metrics 使用 ——
系統必須能說明「我為什麼選了這條路」，而不是默默地選。
"""
from __future__ import annotations

from collections import Counter

from app.models import DocumentProfile
from app.parser.pdf_parser import ParsedPdf

_MIN_TEXT_CHARS = 500        # 低於此值視為無文字層（掃描件）
_MID_BAND = 0.04             # 中線兩側各 4% 頁寬視為分欄帶
_MIN_BLOCK_WIDTH = 0.15      # 過窄的區塊（圖說、公式、儲存格）不列入欄數投票


def profile(pdf: ParsedPdf) -> DocumentProfile:
    has_text = len(pdf.full_text_sample(3).strip()) > _MIN_TEXT_CHARS
    columns = _detect_columns(pdf) if has_text else 1
    pdf.set_columns(columns)

    rules = pdf.rules()
    n_h = sum(1 for r in rules if r.horizontal)
    n_v = sum(1 for r in rules if r.vertical)

    body, headings = _font_levels(pdf)

    prof = DocumentProfile(
        title=pdf.title(),
        n_pages=pdf.n_pages,
        has_text_layer=has_text,
        columns=columns,
        body_font_size=body,
        heading_font_sizes=headings,
        n_toc_entries=len(pdf.toc()),
        n_h_rules=n_h,
        n_v_rules=n_v,
    )
    prof.table_strategy = _table_strategy(prof)
    return prof


def _detect_columns(pdf: ParsedPdf) -> int:
    """以「有多少區塊橫跨頁面中線」判斷單欄或雙欄。

    首頁排除（標題與摘要常橫跨整頁），過窄的區塊也排除
    （圖說與公式在雙欄版面同樣不跨線，會稀釋訊號）。
    """
    votes: list[int] = []
    for pno in range(1, min(pdf.n_pages, 10)):        # 跳過第 1 頁
        page = pdf.doc[pno]
        width = page.rect.width
        mid, band = width / 2, width * _MID_BAND
        min_w = width * _MIN_BLOCK_WIDTH

        spans = [
            (b[0], b[2])
            for b in page.get_text("blocks")
            if b[6] == 0 and b[4].strip() and (b[2] - b[0]) > min_w
        ]
        if len(spans) < 3:
            continue
        crossing = sum(1 for x0, x1 in spans if x0 < mid - band and x1 > mid + band)
        votes.append(1 if crossing > len(spans) * 0.3 else 2)

    if not votes:
        return 1
    return Counter(votes).most_common(1)[0][0]


def _font_levels(pdf: ParsedPdf, sample_pages: int = 6) -> tuple[float, list[float]]:
    """內文字級取字元數最多者；標題候選為明顯大於內文的字級。"""
    sizes: Counter = Counter()
    for pno in range(min(sample_pages, pdf.n_pages)):
        for block in pdf.doc[pno].get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    sizes[round(span["size"], 1)] += len(span["text"])
    if not sizes:
        return 0.0, []
    body = sizes.most_common(1)[0][0]
    headings = sorted((s for s in sizes if s > body * 1.08), reverse=True)[:4]
    return body, headings


def _table_strategy(p: DocumentProfile) -> str:
    """僅為提示，實際策略在抽取時按每個表格區域各自判定。"""
    if not p.has_text_layer:
        return "unsupported"
    if p.n_h_rules < 3:
        return "whitespace"
    if p.n_v_rules >= p.n_h_rules:
        return "lattice"
    return "booktabs"
