"""Block A 驗收：確認 LLM 通道可用，並印出用量與成本。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.llm import client  # noqa: E402
from app.util import tokens  # noqa: E402


async def main() -> None:
    print(f"模型            {settings.llm_model}")
    print(f"檢索預算        {settings.retrieval_budget} tokens "
          f"(= {settings.max_context} − {settings.system_reserved} − "
          f"{settings.question_reserved} − {settings.answer_reserved} − {settings.safety_margin})")
    print(f"切塊策略        {settings.chunk_strategy} / {settings.chunk_target_tokens} tokens")
    print(f"檢索模式        {settings.retrieval_mode}")
    print(f"Embedding 後端  {settings.embedding_backend}")

    prompt = "用一句繁體中文說明 retrieval-augmented generation 是什麼。"
    print(f"\nprompt 長度     {tokens.count(prompt)} tokens")

    result = await client.generate(prompt, system="你是簡潔的技術助理。")
    print(f"\n回應            {result.text}")
    print(f"用量            {result.prompt_tokens} in / {result.completion_tokens} out")
    print(f"耗時            {result.latency_ms} ms")
    print(f"成本            US${result.cost_usd:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
