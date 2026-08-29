"""向量化。單一介面，數個可切換的本地後端。

所有後端一律在本地推論。作業限制要求文件不得交給現成的 gpt/gemini API 解析，
因此這裡刻意不提供任何「把文件送到外部服務取向量」的選項 ——
indexing 階段的 API 呼叫次數恆為 0，是結構上做不到，而不是預設值剛好如此。
外部模型在本專案只出現在 `app/llm/client.py`，且只收字串、只回字串。
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)


class Embedder:
    """向量化後端的共同介面。

    query 與 passage 分開是必要的：e5 系列需要不同前綴，
    兩邊用錯不會報錯，只會讓分數整體下滑 —— 屬於最難察覺的錯誤。
    """

    dim: int
    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        raise NotImplementedError


# 檢索用前綴。e5 與 bge 系列在訓練時就帶著這些前綴，少加會讓分數整體下滑；
# 兩邊加反了同樣不會報錯。因此不依賴套件的內部行為，在這裡明確指定。
_PREFIXES: dict[str, tuple[str, str]] = {          # 模型關鍵字 -> (query 前綴, passage 前綴)
    "e5": ("query: ", "passage: "),
    "bge-small-zh": ("为这个句子生成表示以用于检索相关文章：", ""),
}


def _prefix_for(model_name: str) -> tuple[str, str]:
    for key, pair in _PREFIXES.items():
        if key in model_name:
            return pair
    return ("", "")


def _register_custom_models() -> None:
    """fastembed 未收錄 multilingual-e5-small，但官方 repo 提供 ONNX，手動註冊。

    這是本專案在多語言與體積之間最理想的落點：約 470MB、100 種語言、
    且是為非對稱檢索訓練的模型（與 2019 年的相似度模型有本質差異）。
    """
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    name = "intfloat/multilingual-e5-small"
    if any(m["model"] == name for m in TextEmbedding.list_supported_models()):
        return
    TextEmbedding.add_custom_model(
        model=name,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=name),
        dim=384,
        model_file="onnx/model.onnx",
        description="Multilingual (~100 languages), retrieval-trained, 512 input tokens",
        size_in_gb=0.47,
    )


class FastEmbedBackend(Embedder):
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        _register_custom_models()
        self.name = model_name
        self.query_prefix, self.passage_prefix = _prefix_for(model_name)
        # threads=1：並行工作池在直譯器結束時會於 macOS 觸發
        # recursive_mutex 崩潰，且本專案的並行度由請求層的 semaphore 控制。
        self._model = TextEmbedding(
            model_name=model_name, threads=1,
            **({"cache_dir": str(settings.model_cache_dir)} if settings.model_cache_dir else {}),
        )
        self.dim = next(
            m["dim"] for m in TextEmbedding.list_supported_models() if m["model"] == model_name
        )

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        batch = [self.passage_prefix + t for t in texts]
        return _normalize(np.array(list(self._model.embed(batch)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        vec = list(self._model.embed([self.query_prefix + text]))
        return _normalize(np.array(vec, dtype=np.float32))[0]


def _normalize(v: np.ndarray) -> np.ndarray:
    """L2 正規化後，內積即等於餘弦相似度。"""
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norms, 1e-12)


@lru_cache(maxsize=8)
def get_embedder(backend: str | None = None, purpose: str = "query") -> Embedder:
    """purpose 決定使用哪一個推論工作階段。

    索引與查詢必須用各自獨立的工作階段：ONNX 的單一 session 在內部是序列化的，
    共用時查詢的單句向量化會排在整批 90 個片段之後。實測併發上傳會讓檢索
    中位數由 44 ms 升到 733 ms —— 兩組 semaphore 隔離的是排程，
    但沒有隔離推論資源本身。
    """
    backend = backend or settings.embedding_backend
    if backend == "onnx":
        embedder = FastEmbedBackend(settings.embedding_model_onnx)
    elif backend == "e5-small":
        embedder = FastEmbedBackend("intfloat/multilingual-e5-small")
    elif backend == "bge-zh":
        embedder = FastEmbedBackend("BAAI/bge-small-zh-v1.5")
    elif backend == "zh":
        embedder = FastEmbedBackend(settings.embedding_model_zh)
    elif backend == "onnx-large":
        embedder = FastEmbedBackend(settings.embedding_model_onnx_large)
    else:
        raise ValueError(f"未知的向量化後端：{backend}")

    # 預熱：首次推論有約一秒的一次性初始化成本。不預熱的話這筆帳會記在
    # 使用者的第一次查詢上，看起來像是檢索很慢，實際上檢索只要幾十毫秒。
    embedder.embed_query("warmup")
    return embedder
