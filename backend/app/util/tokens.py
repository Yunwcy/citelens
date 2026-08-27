"""全系統統一的 token 計數。

一律使用 cl100k_base 作為計價單位：換模型時 budget 邏輯不必跟著改，
成本與長度的報表也才有一致的基準。
"""
from functools import lru_cache

import tiktoken


@lru_cache
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def encoding():
    """需要逐 token 操作時使用（例如固定視窗切塊）。"""
    return _encoding()


def count(text: str) -> int:
    return len(_encoding().encode(text))


def truncate(text: str, limit: int) -> str:
    """截到指定 token 數以內。用於 context packing 的最後手段。"""
    enc = _encoding()
    ids = enc.encode(text)
    if len(ids) <= limit:
        return text
    return enc.decode(ids[:limit])
