"""向量化。單一介面，三個可切換後端。

預設走本地 ONNX：文件解析與向量化全程不呼叫外部服務，
`/metrics` 上的 indexing 階段 API 呼叫次數因此恆為 0。
openai 後端僅在磁碟空間受限時使用，且不改變「模型只做 text-to-text」
這條限制 —— 向量化收到的是本地已切好的純文字。
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


class FastEmbedBackend(Embedder):
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.name = model_name
        # threads=1：並行工作池在直譯器結束時會於 macOS 觸發
        # recursive_mutex 崩潰，且本專案的並行度由請求層的 semaphore 控制。
        self._model = TextEmbedding(model_name=model_name, threads=1)
        self.dim = next(
            m["dim"] for m in TextEmbedding.list_supported_models() if m["model"] == model_name
        )

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        # passage_embed 會依模型自動套用正確前綴（e5 需要，MiniLM 不需要）
        return _normalize(np.array(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.array(list(self._model.query_embed([text])), dtype=np.float32))[0]


class OpenAIBackend(Embedder):
    """僅在磁碟受限時使用。預設不啟用，避免與作業限制產生模糊地帶。"""

    def __init__(self, model_name: str):
        from openai import OpenAI

        self.name = model_name
        self.dim = 1536
        self._client = OpenAI(api_key=settings.openai_api_key)

    def _embed(self, texts: list[str]) -> np.ndarray:
        rsp = self._client.embeddings.create(model=self.name, input=texts)
        return _normalize(np.array([d.embedding for d in rsp.data], dtype=np.float32))

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        out = [self._embed(texts[i:i + 128]) for i in range(0, len(texts), 128)]
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


def _normalize(v: np.ndarray) -> np.ndarray:
    """L2 正規化後，內積即等於餘弦相似度。"""
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norms, 1e-12)


@lru_cache(maxsize=3)
def get_embedder(backend: str | None = None) -> Embedder:
    backend = backend or settings.embedding_backend
    if backend == "onnx":
        embedder = FastEmbedBackend(settings.embedding_model_onnx)
    elif backend == "onnx-large":
        embedder = FastEmbedBackend(settings.embedding_model_onnx_large)
    elif backend == "openai":
        embedder = OpenAIBackend(settings.embedding_model_openai)
    else:
        raise ValueError(f"未知的向量化後端：{backend}")

    # 預熱：首次推論有約一秒的一次性初始化成本。不預熱的話這筆帳會記在
    # 使用者的第一次查詢上，看起來像是檢索很慢，實際上檢索只要幾十毫秒。
    if backend != "openai":
        embedder.embed_query("warmup")
    return embedder
