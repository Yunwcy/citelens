"""脈絡預算管理。

作業假設模型脈絡上限為 10K tokens。實務上不能把整份文件送進去，
但也不該只用「取前 K 個片段」草率了事 —— 片段長度差異很大，
固定 K 值要嘛浪費額度，要嘛超標。

這裡做的是 token 感知的組裝：依融合分數依序放入，放不下時先嘗試在
句子邊界裁切，仍放不下才跳過，並記錄被捨棄的片段供除錯面板顯示。
「有東西被捨棄」必須是看得見的，否則答案不完整時無從得知原因。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings
from app.models import Chunk
from app.util import tokens

_SENTENCE_END = re.compile(r"(?<=[。！？.!?])\s+")
_MIN_USEFUL = 80          # 裁切後低於此長度就沒有保留價值


@dataclass(slots=True)
class PackedContext:
    blocks: list[tuple[int, Chunk, str]] = field(default_factory=list)   # (引用編號, 片段, 實際文字)
    used_tokens: int = 0
    dropped: list[str] = field(default_factory=list)                      # 被捨棄的片段編號
    truncated: list[str] = field(default_factory=list)

    @property
    def budget(self) -> int:
        return settings.retrieval_budget

    def render(self, cite: callable) -> str:
        """組成送進模型的脈絡文字。

        每個片段都標上引用編號與出處，模型才有辦法在答案中標註 [1]、[2]。
        欄位化的格式沿用先前專案的經驗 —— 這是引用能穩定輸出的主因。
        """
        parts = []
        for n, chunk, text in self.blocks:
            parts.append(
                f"[{n}] 出處：{cite(chunk)}\n"
                f"    類型：{'表格' if chunk.kind != 'text' else '內文'}\n"
                f"    內容：{text}"
            )
        return "\n\n".join(parts)


def pack(
    chunks: list[Chunk],
    budget: int | None = None,
    all_chunks: list[Chunk] | None = None,
) -> PackedContext:
    """依既有順序（已由檢索排序）組裝，不重新排序。

    all_chunks 用於表格展開：檢索命中表格中的某一列時，把整張表一併帶入。
    否則模型只看得到被命中的那幾列，會據此回答「整張表」的問題 ——
    實測問「消融版本的表現」時，只取回基準區塊的四列，
    答案完全沒有涵蓋三個消融變體，卻看起來十分完整。
    """
    budget = budget or settings.retrieval_budget
    ctx = PackedContext()

    for chunk in _expand_tables(chunks, all_chunks):
        remaining = budget - ctx.used_tokens
        if remaining <= _MIN_USEFUL:
            ctx.dropped.append(chunk.chunk_id)
            continue

        text = chunk.text
        n = chunk.n_tokens or tokens.count(text)

        if n > remaining:
            # 表格不裁切：少了幾列的表格會讓模型讀出錯誤的對應關係，
            # 比完全沒有這張表更危險。
            if chunk.kind != "text":
                ctx.dropped.append(chunk.chunk_id)
                continue
            text = _trim(text, remaining)
            if not text or tokens.count(text) < _MIN_USEFUL:
                ctx.dropped.append(chunk.chunk_id)
                continue
            ctx.truncated.append(chunk.chunk_id)
            n = tokens.count(text)

        ctx.blocks.append((len(ctx.blocks) + 1, chunk, text))
        ctx.used_tokens += n

    return ctx


def _expand_tables(chunks: list[Chunk], all_chunks: list[Chunk] | None) -> list[Chunk]:
    """命中表格列時，緊接著補上該表格的完整內容。

    只補一次，且放在第一個命中列之後 —— 保持檢索排序，
    同時讓模型看到完整的欄列對應關係。
    """
    if not all_chunks:
        return chunks

    full_by_table = {
        c.meta.get("table_id"): c for c in all_chunks if c.kind == "table_full"
    }
    # 先掃一遍已存在的整表片段，避免檢索本身已取回整表時又補一份
    seen: set[str] = {
        c.meta["table_id"] for c in chunks
        if c.kind == "table_full" and c.meta.get("table_id")
    }
    out: list[Chunk] = []
    for c in chunks:
        out.append(c)
        tid = c.meta.get("table_id")
        if c.kind == "table_row" and tid and tid not in seen:
            seen.add(tid)
            full = full_by_table.get(tid)
            if full is not None:
                out.append(full)
    return out


def _trim(text: str, limit: int) -> str:
    """在句子邊界裁切；沒有句界可用時才硬切。"""
    sentences = _SENTENCE_END.split(text)
    out, used = [], 0
    for s in sentences:
        n = tokens.count(s)
        if used + n > limit:
            break
        out.append(s)
        used += n
    if out:
        return " ".join(out)
    return tokens.truncate(text, limit)
