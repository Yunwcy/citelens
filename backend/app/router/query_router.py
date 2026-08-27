"""查詢路由。

三個指定問題其實是三種不同的檢索任務，用同一套 top-k 處理必然有一項做不好：
- 摘要：需要全篇覆蓋，top-k 只會看到文件的一部分
- 比較：需要跨章節取證，單一查詢容易只命中其中一方
- 表格：答案是特定儲存格，應該用查表而不是讓模型從文字中讀

以規則優先而非 LLM 分類器：省一次往返、延遲更低，且 demo 每次結果一致。
規則涵蓋不到的才落到一般問答。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import Table

_SUMMARY = re.compile(
    r"\b(summar\w*|overview|tl;?dr)\b|摘要|總結|概述|重點整理", re.I
)
_COMPARE = re.compile(
    r"\b(compare|comparison|versus|vs\.?|differ\w*|difference)\b|比較|差別|差異|對比|哪個好", re.I
)

# 「compare A with B」「A vs B」「比較 A 和 B」
_PAIR_PATTERNS = [
    re.compile(r"compar\w*\s+(.+?)\s+(?:with|to|and|against|vs\.?)\s+(.+?)[\s?.,]*$", re.I),
    re.compile(r"^(.+?)\s+(?:vs\.?|versus)\s+(.+?)[\s?.,]*$", re.I),
    re.compile(r"比較\s*(.+?)\s*(?:和|與|跟|及)\s*(.+?)[\s?。，、]*$"),
    re.compile(r"^(.+?)\s*(?:和|與|跟)\s*(.+?)\s*(?:有什麼|的)?(?:差別|差異|不同)"),
]

_STOP = {"the", "a", "an", "of", "in", "on", "for", "and", "or", "is", "are", "to", "with"}


@dataclass(slots=True)
class Route:
    name: str                                   # summary | comparison | table_lookup | qa
    reason: str = ""
    entities: list[str] = field(default_factory=list)
    table_id: str | None = None
    matched_labels: list[str] = field(default_factory=list)


def route(question: str, tables: dict[str, Table] | None = None) -> Route:
    if _SUMMARY.search(question):
        return Route("summary", "命中摘要關鍵字")

    if _COMPARE.search(question):
        ents = _entities(question)
        return Route("comparison", "命中比較關鍵字", entities=ents)

    hit = _table_hit(question, tables or {})
    if hit:
        table_id, labels = hit
        return Route("table_lookup", f"命中 {table_id} 的座標標籤",
                     table_id=table_id, matched_labels=labels)

    return Route("qa", "未命中特殊規則")


def _entities(question: str) -> list[str]:
    """抽出被比較的兩個對象，供拆解子查詢使用。抽不出就回空清單。"""
    for pat in _PAIR_PATTERNS:
        m = pat.search(question.strip())
        if m:
            pair = [" ".join(g.split()) for g in m.groups()[:2]]
            pair = [p for p in pair if 1 < len(p) < 60]
            if len(pair) == 2:
                return pair
    return []


def _table_hit(question: str, tables: dict[str, Table]) -> tuple[str, list[str]] | None:
    """查詢是否指到某張表的座標。

    需要同時命中至少兩個標籤才算 —— 只命中一個（例如「Legal」）
    很可能只是正文用詞，貿然走查表會答非所問。
    """
    q = question.lower()
    best: tuple[int, str, list[str]] | None = None

    for tid, t in tables.items():
        if t.kind != "data":
            continue
        labels = {lbl.split(" #")[0] for lbl, _ in t.rows}
        # 用每一列實際的鍵，而不是 t.columns ——
        # 多區塊表格的 columns 只保留最後一個區塊的命名，
        # 實測 Table 2 的 20 個鍵裡有 12 個不在 columns 內，
        # 其中包含全部的 LightRAG 與 -High / -Low 欄。
        for vals in (v for _, v in t.rows):
            for col in vals:
                labels.update(part.strip() for part in col.split("/"))
        # 區塊前綴本身也是可匹配的對象（「-High 在 Legal 的表現」）
        for lbl, _ in t.rows:
            labels.update(part.strip() for part in lbl.split("/"))

        matched = sorted(
            {lbl for lbl in labels if len(lbl) > 2 and lbl.lower() in q},
            key=len, reverse=True,
        )
        if len(matched) >= 2 and (best is None or len(matched) > best[0]):
            best = (len(matched), tid, matched)

    return (best[1], best[2]) if best else None


def subqueries(question: str, entities: list[str]) -> list[str]:
    """比較類問題的固定拆解。

    用寫死的樣板而非讓模型動態拆解：省下每次查詢一到兩次往返，
    延遲更低，且同一個問題永遠拆出同一組子查詢，評估數據才可重現。
    """
    if len(entities) != 2:
        return [question]
    a, b = entities
    return [
        question,
        f"{a} architecture method how it works",
        f"{b} architecture method how it works",
        f"{a} {b} performance results comparison",
        f"{a} {b} cost efficiency token overhead",
    ]
