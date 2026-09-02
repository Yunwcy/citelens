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
    # 這次實際採用的額度。問題較長時會低於預設值 —— 回報預設值會讓
    # 介面與指標顯示一個沒有被使用的數字。
    limit: int = 0
    dropped: list[str] = field(default_factory=list)                      # 被捨棄的片段編號
    truncated: list[str] = field(default_factory=list)
    # 由資料算出來、必須讓模型看到的統計。放在脈絡區塊裡會被當成一般內容略過 ——
    # 實測埋在 6,000 字的表格片段開頭時，模型照樣寫出與它相反的結論。
    # 因此改由呼叫端接在問題後面，那是提示詞裡最不會被跳過的位置。
    facts: list[str] = field(default_factory=list)

    @property
    def budget(self) -> int:
        return self.limit or settings.retrieval_budget

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
    question: str | None = None,
) -> PackedContext:
    """依既有順序（已由檢索排序）組裝，不重新排序。

    all_chunks 用於表格展開：檢索命中表格中的某一列時，把整張表一併帶入。
    否則模型只看得到被命中的那幾列，會據此回答「整張表」的問題 ——
    實測問「消融版本的表現」時，只取回基準區塊的四列，
    答案完全沒有涵蓋三個消融變體，卻看起來十分完整。
    """
    # 問題比保留額長時，超出的部分從檢索額度扣掉 —— 否則總量會超過上限
    if budget is None:
        budget = (settings.budget_for(tokens.count(question)) if question
                  else settings.retrieval_budget)
    ctx = PackedContext(limit=budget)

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
    """命中表格列時，以該表格的完整內容取代那幾列。

    逐列片段的用途是讓表格「被找到」—— 欄名與值展開成文字後，
    關鍵字與語意檢索才命中得到。但一旦找到了，完整表格才是更好的脈絡：
    它保有全部區塊與欄列對應。

    兩者同時出現時，被命中的那幾列會錨定模型的注意力。實測問
    「消融版本的表現」時，檢索取回的是基準區塊的四列，
    模型就據此回答，完全沒有使用同在脈絡中的完整表格 ——
    答案裡沒有任何一個消融版本。

    表格進入脈絡時，**同一節的討論文字也一併帶入**。
    表格只有數字，數字的意義寫在正文裡 —— 而表格本身會把自己的討論擠出前 K 名：
    實測問「消融版本的表現」時，前三名全是表格與表格列，
    解讀那段排到第 9，「-Origin 意外沒有下降」那段連前 20 都沒有。
    結果是答案把數字列得很完整，卻完全沒有回答「這些數字代表什麼」。
    """
    if not all_chunks:
        return chunks

    full_by_table = {
        c.meta.get("table_id"): c for c in all_chunks if c.kind == "table_full"
    }
    # 先掃一遍已存在的整表片段，避免檢索本身已取回整表時又補一份
    # 哪些表格會以完整形式出現：檢索直接取回的，加上由命中列展開的
    with_full = {
        c.meta["table_id"] for c in chunks
        if c.kind == "table_full" and c.meta.get("table_id")
    }
    with_full |= {
        c.meta["table_id"] for c in chunks
        if c.kind == "table_row" and c.meta.get("table_id") in full_by_table
    }

    emitted: set[str] = set()
    out: list[Chunk] = []
    for c in chunks:
        tid = c.meta.get("table_id")
        if c.kind == "table_row" and tid in with_full:
            # 以完整表格取代該列，且每張表只放一次
            if tid not in emitted:
                emitted.add(tid)
                out.append(full_by_table[tid])
                out.extend(_discussion_of(full_by_table[tid], all_chunks, chunks))
            continue
        if c.kind == "table_full" and tid:
            if tid in emitted:
                continue
            emitted.add(tid)
            out.append(c)
            out.extend(_discussion_of(c, all_chunks, chunks))
            continue
        out.append(c)
    return out


_MAX_DISCUSSION_TOKENS = 700


def _discussion_of(table: Chunk, all_chunks: list[Chunk], already: list[Chunk]) -> list[Chunk]:
    """表格所屬章節的正文，依原文順序帶入，總量設上限。

    表格給的是數字，數字的意義寫在正文裡。少了這段，
    模型只能複述數值，答不出「這些數字代表什麼」。
    """
    if not table.section_id:
        return []
    have = {c.chunk_id for c in already}
    picked, used = [], 0
    for c in all_chunks:
        if (c.kind != "text" or c.section_id != table.section_id
                or c.chunk_id in have):
            continue
        n = c.n_tokens or 0
        if used + n > _MAX_DISCUSSION_TOKENS:
            break
        picked.append(c)
        used += n
    return picked


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
