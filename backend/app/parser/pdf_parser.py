"""PDF 解析：抽出文字區塊、字級、版面線條，並建立跨欄的閱讀順序。

不使用任何外部 API。座標資訊是後續章節偵測與表格抽取的基礎，
因此這一層刻意保留原始幾何資料，而非只回傳純文字。
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from app.models import Block, Rule

# 判定線條的門檻。橫線用於 booktabs 表格錨定，直線用於格線表格。
_H_MIN_WIDTH = 100
_V_MIN_HEIGHT = 40
_LINE_MAX_THICKNESS = 2


class ParsedPdf:
    """開啟中的 PDF。使用後須呼叫 close()，或以 with 陳述式管理。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.doc = pymupdf.open(self.path)
        self._columns = 1          # 由 profiler 回填，影響閱讀順序計算
        self._blocks: list[Block] | None = None

    def __enter__(self) -> "ParsedPdf":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        self.doc.close()

    # --- 基本資訊 ---------------------------------------------------------

    @property
    def n_pages(self) -> int:
        return len(self.doc)

    def toc(self) -> list[tuple[int, str, int]]:
        """PDF 內建大綱。實測多數 LaTeX 產出的論文都有，且品質極佳。"""
        return self.doc.get_toc()

    def page_text(self, page_no: int) -> str:
        """page_no 為 1-based。"""
        return self.doc[page_no - 1].get_text()

    def full_text_sample(self, n_pages: int = 3) -> str:
        return "".join(self.doc[i].get_text() for i in range(min(n_pages, self.n_pages)))

    # --- 線條 -------------------------------------------------------------

    def rules(self) -> list[Rule]:
        out: list[Rule] = []
        for pno in range(self.n_pages):
            for drawing in self.doc[pno].get_drawings():
                r = drawing["rect"]
                thin_h = r.height < _LINE_MAX_THICKNESS and r.width > _H_MIN_WIDTH
                thin_v = r.width < _LINE_MAX_THICKNESS and r.height > _V_MIN_HEIGHT
                if thin_h or thin_v:
                    out.append(Rule(pno + 1, r.x0, r.y0, r.x1, r.y1))
        return out

    # --- 閱讀順序 ---------------------------------------------------------

    def set_columns(self, columns: int) -> None:
        """由 profiler 設定欄數；改變欄數會使既有的區塊快取失效。"""
        if columns != self._columns:
            self._columns = columns
            self._blocks = None

    def blocks(self) -> list[Block]:
        """全文件的文字區塊，依閱讀順序排序。

        order 是一個跨頁單調遞增的浮點數，雙欄文件已將左欄排在右欄之前。
        章節邊界的比較全部以 order 進行，避免直接比較頁碼與 y 座標的複雜度。
        """
        if self._blocks is not None:
            return self._blocks

        out: list[Block] = []
        for pno in range(self.n_pages):
            page = self.doc[pno]
            height = page.rect.height
            mid_x = page.rect.width / 2
            sizes = self._block_sizes(page)

            for i, b in enumerate(page.get_text("blocks")):
                x0, y0, x1, y1, text, block_no, block_type = b
                if block_type != 0 or not text.strip():
                    continue
                # 雙欄：右欄整體排在左欄之後，故加上一個頁高的位移
                column = 1 if (self._columns == 2 and x0 >= mid_x) else 0
                order = pno * 10_000 + column * (height + 1) + y0
                out.append(
                    Block(
                        page=pno + 1,
                        x0=x0, y0=y0, x1=x1, y1=y1,
                        text=text.strip(),
                        size=sizes.get(block_no, 0.0),
                        order=order,
                    )
                )

        out.sort(key=lambda b: b.order)
        self._blocks = out
        return out

    @staticmethod
    def _block_sizes(page) -> dict[int, float]:
        """每個區塊的主要字級，取字元數最多的那一級。"""
        from collections import Counter

        counters: dict[int, Counter] = {}
        for bi, b in enumerate(page.get_text("dict")["blocks"]):
            if b.get("type") != 0:
                continue
            c: Counter = Counter()
            for line in b.get("lines", []):
                for span in line["spans"]:
                    c[round(span["size"], 1)] += len(span["text"])
            if c:
                counters[bi] = c
        return {bi: c.most_common(1)[0][0] for bi, c in counters.items()}

    # --- 定位 -------------------------------------------------------------

    def locate(self, text: str, page_no: int, min_order: float = -1.0) -> float | None:
        """在指定頁尋找一段文字，回傳其閱讀順序索引。

        用於把 PDF 大綱的標題對應回內文位置。

        min_order 用來排除「同一片語先出現在內文中」的誤配：章節標題必然
        出現在前一個章節之後，因此只接受位置更後面的命中。沒有這道限制時，
        3.1 的標題會被內文裡提前出現的同名片語搶走，導致章節順序錯亂。
        """
        if not (1 <= page_no <= self.n_pages):
            return None
        page = self.doc[page_no - 1]
        needle = " ".join(text.split())

        hits = page.search_for(needle)
        if not hits:
            # 標題可能跨行或含特殊字元，退而求其次用前段文字比對
            head = needle[:40]
            if len(head) < 6:
                return None
            hits = page.search_for(head)
        if not hits:
            return None

        height = page.rect.height
        mid_x = page.rect.width / 2
        orders = sorted(
            (page_no - 1) * 10_000
            + (1 if (self._columns == 2 and r.x0 >= mid_x) else 0) * (height + 1)
            + r.y0
            for r in hits
        )
        return next((o for o in orders if o > min_order), None)
