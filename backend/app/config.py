"""集中管理所有可調參數。

evaluation 依賴 CHUNK_STRATEGY / RETRIEVAL_MODE / EMBEDDING_BACKEND 三個開關
切換出對照組，因此這些值只能從這裡讀取，不得散落在各模組。
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 外部服務 ---------------------------------------------------------
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0

    # 單價（USD / 1M tokens）。跑報表時換算成本用，數字以官方定價頁為準。
    price_input_per_1m: float = 0.15
    price_output_per_1m: float = 0.60

    # --- 策略開關（evaluation 對照組）--------------------------------------
    chunk_strategy: Literal["section", "fixed"] = "section"
    retrieval_mode: Literal["hybrid", "vector"] = "hybrid"
    embedding_backend: Literal["onnx", "torch", "openai"] = "onnx"

    embedding_model_onnx: str = "intfloat/multilingual-e5-small"
    embedding_model_torch: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_model_openai: str = "text-embedding-3-small"

    # --- Context budget ---------------------------------------------------
    # 作業假設 LLM max context = 10K，以下為切分方式
    max_context: int = 10_000
    system_reserved: int = 800
    question_reserved: int = 200
    answer_reserved: int = 1_500
    safety_margin: int = 500

    # --- 切塊與檢索 -------------------------------------------------------
    # 450 的理由是檢索粒度與 budget 效率，不是 encoder 上限。
    # baseline 的 fixed 切法必須用同一個值，否則對照實驗多一個混淆變因。
    chunk_target_tokens: int = 450
    chunk_overlap_tokens: int = 60
    top_k: int = 8
    rrf_k: int = 60
    vector_weight: float = 0.7  # 僅在停用 RRF、改用加權和時使用

    # --- 併發上限 ---------------------------------------------------------
    index_concurrency: int = 1  # 重路徑：解析 + embedding，序列化避免餓死查詢
    query_concurrency: int = 4  # 輕路徑：單句 query embedding
    llm_concurrency: int = 8    # 網路 I/O，天花板實際上是 OpenAI 額度

    # --- 匯入限制 ---------------------------------------------------------
    max_upload_mb: int = 30
    max_pages: int = 100
    fetch_timeout_s: int = 20

    # --- 儲存 -------------------------------------------------------------
    storage_dir: Path = Path("./storage")

    @property
    def retrieval_budget(self) -> int:
        """留給檢索內容的 token 上限。"""
        return (
            self.max_context
            - self.system_reserved
            - self.question_reserved
            - self.answer_reserved
            - self.safety_margin
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
