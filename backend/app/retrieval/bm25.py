"""關鍵字檢索。

BM25 是 lexical 的，跨語言無效，且中文沒有空白分隔，必須斷詞。
沿用先前專案的正規表達式切法（把整串中文當一個詞）等於沒有斷詞，
中文查詢會完全命不中，因此改用 jieba 處理 CJK、空白切分處理拉丁字母。

表格逐列線性化之後，Diversity、-High、Legal 這些詞才成為可被關鍵字
命中的 token —— 這是混合檢索在此專案特別有效的原因。
"""
from __future__ import annotations

import re

import jieba
from rank_bm25 import BM25Okapi

_CJK = re.compile(r"[一-鿿぀-ヿ]+")
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-+%/]*")

jieba.setLogLevel(60)      # 關掉初始化訊息
jieba.initialize()         # 預先載入字典：延後到首次查詢會讓該次多花約一秒


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for chunk in _CJK.split(text):
        out.extend(t.lower() for t in _LATIN.findall(chunk))
    for cjk in _CJK.findall(text):
        out.extend(t for t in jieba.cut(cjk) if t.strip())
    return out


class Bm25Index:
    """不序列化：從片段重建只需毫秒，存起來反而增加版本相容的負擔。"""

    def __init__(self, texts: list[str]):
        self.corpus = [tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(self.corpus) if any(self.corpus) else None

    def __len__(self) -> int:
        return len(self.corpus)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        if self._bm25 is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(i, float(scores[i])) for i in order if scores[i] > 0]
