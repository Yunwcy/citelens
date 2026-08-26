"""融合：Reciprocal Rank Fusion。

不用加權分數和的理由：向量相似度與 BM25 分數的量綱完全不同，
要相加就得先正規化，而正規化對離群值敏感、且會隨語料改變。
RRF 只看名次，對異質檢索器穩定得多。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass(slots=True)
class Hit:
    index: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)      # 各檢索器給的名次，供除錯面板顯示


def rrf(rankings: dict[str, list[tuple[int, float]]], k: int | None = None) -> list[Hit]:
    """rankings：{檢索器名稱: [(片段序號, 原始分數), ...]}，已依分數排序。"""
    k = k or settings.rrf_k
    fused: dict[int, Hit] = {}
    for source, results in rankings.items():
        for rank, (idx, _score) in enumerate(results, start=1):
            hit = fused.setdefault(idx, Hit(index=idx, score=0.0))
            hit.score += 1.0 / (k + rank)
            hit.ranks[source] = rank
    return sorted(fused.values(), key=lambda h: h.score, reverse=True)
