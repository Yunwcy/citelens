"""跨模組共用的資料結構。

刻意使用 dataclass 而非 pydantic：這些物件在解析與檢索的熱路徑上大量建立，
不需要驗證開銷；對外的 API schema 另以 pydantic 定義。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True)
class Rule:
    """版面線條。表格偵測以此為幾何錨點。"""
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) < 2 and (self.x1 - self.x0) > 100

    @property
    def vertical(self) -> bool:
        return abs(self.x1 - self.x0) < 2 and (self.y1 - self.y0) > 40


@dataclass(slots=True)
class Block:
    """一段文字區塊，附版面座標與閱讀順序索引。"""
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float          # 該區塊的主要字級
    order: float         # 全文件單調遞增的閱讀順序索引（跨欄已處理）


@dataclass(slots=True)
class DocumentProfile:
    """上傳時偵測到的文件特徵，決定後續採用哪一組策略。"""
    n_pages: int
    has_text_layer: bool
    columns: int
    body_font_size: float
    heading_font_sizes: list[float]
    n_toc_entries: int
    n_h_rules: int
    n_v_rules: int
    section_source: str = "unknown"      # toc | regex | font | none
    table_strategy: str = "unknown"      # lattice | booktabs | whitespace | unsupported

    def summary(self) -> str:
        return (
            f"{self.n_pages} 頁 · {self.columns} 欄 · 內文字級 {self.body_font_size} · "
            f"大綱 {self.n_toc_entries} 筆 · 橫線 {self.n_h_rules} / 直線 {self.n_v_rules}"
        )


@dataclass(slots=True)
class Section:
    """章節。blocks 保留頁碼，讓切塊後仍能標出正確頁次。"""
    id: str
    title: str
    level: int
    start_order: float
    end_order: float
    blocks: list[tuple[int, str]] = field(default_factory=list)   # (page, text)

    @property
    def text(self) -> str:
        return "\n".join(t for _, t in self.blocks)

    @property
    def page_start(self) -> int:
        return self.blocks[0][0] if self.blocks else 0


@dataclass(slots=True)
class Chunk:
    """索引與檢索的最小單位。"""
    chunk_id: str
    text: str
    page: int
    section_id: str
    section_title: str
    kind: Literal["text", "table_row", "table_full"] = "text"
    n_tokens: int = 0
    meta: dict = field(default_factory=dict)
