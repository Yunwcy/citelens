"""向量檢索：numpy 內積。

單一文件約 300 個片段，300×384 的矩陣乘法是微秒級。
原本規劃使用的 FAISS IndexFlatIP 本身就是暴力精確搜尋，結果與此完全相同；
FAISS 真正的價值在近似索引，而近似是以召回率換取速度 —— 這個規模不需要。
介面保持一致，語料成長後換成 FAISS 只需替換這個類別。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


class VectorIndex:
    def __init__(self, vectors: np.ndarray):
        self.vectors = vectors.astype(np.float32)

    def __len__(self) -> int:
        return len(self.vectors)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        """向量已 L2 正規化，內積即餘弦相似度。"""
        if len(self.vectors) == 0:
            return []
        scores = self.vectors @ query.astype(np.float32)
        k = min(k, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(i), float(scores[i])) for i in top]

    def save(self, path: Path) -> None:
        np.save(path, self.vectors)

    @classmethod
    def load(cls, path: Path) -> "VectorIndex":
        return cls(np.load(path))
