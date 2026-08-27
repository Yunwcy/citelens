"""在一段時間內平均送出查詢，讓監控面板呈現真實的時間序列。

單點資料在時間序列圖上只是一個孤立的點，看不出趨勢。
這支腳本把查詢分散在數分鐘內送出，並輪流走不同路由，
使每個面板都有內容 —— 供簡報截圖使用。

用法：
    python scripts/seed_metrics.py --minutes 6
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import httpx

BASE = "http://localhost:8000"

# 通用問題：任何論文都適用
GENERIC = [
    "summary this document",
    "摘要這份文件",
    "What datasets were used in the evaluation?",
    "這篇論文用了哪些資料集？",
    "實驗是怎麼設計的？",
    "比較文中提到的方法",
    "找出表格中的數據",
    "What are the main contributions?",
]

# 文件專屬問題：關鍵字命中檔名或標題時才使用。
# 先前版本對所有文件隨機發問，導致把 LightRAG 的問題丟給 BERT 論文 ——
# 模型正確回答「文件未提及」，卻在指標上被計為「未標註引用」的失敗。
SPECIFIC = {
    "LightRAG": ["compare lightRAG with GraphRAG", "LightRAG 和 GraphRAG 有什麼差別",
                 "Performance of ablated versions of LightRAG", "消融實驗的結果如何？"],
    "BERT": ["What is masked language modeling?", "BERT 的預訓練任務是什麼？"],
    "Attention": ["What is multi-head attention?", "自注意力機制是怎麼運作的？"],
    "Retrieval-Augmented": ["How does RAG combine retrieval and generation?"],
    "Survey": ["What are the main categories discussed?"],
}


def questions_for(doc: dict) -> list[str]:
    name = f"{doc.get('filename','')} {doc.get('source_name','')}"
    extra = [qs for key, qs in SPECIFIC.items() if key.lower() in name.lower()]
    return GENERIC + [q for group in extra for q in group]


async def ask(client: httpx.AsyncClient, doc: str, q: str) -> dict | None:
    try:
        async with client.stream("POST", f"{BASE}/api/query",
                                 json={"doc_id": doc, "question": q}, timeout=180) as r:
            async for line in r.aiter_lines():
                if line.startswith("data: ") and '"type": "done"' in line:
                    return json.loads(line[6:])["debug"]
    except Exception:                                     # noqa: BLE001
        return None
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=6)
    ap.add_argument("--concurrent", type=int, default=2)
    args = ap.parse_args()

    async with httpx.AsyncClient() as c:
        docs = (await c.get(f"{BASE}/api/documents", timeout=30)).json()
    if not docs:
        print("請先上傳文件"); return

    deadline = time.perf_counter() + args.minutes * 60
    sent = cost = 0.0
    n = 0
    print(f"開始餵資料，預計 {args.minutes:.0f} 分鐘。文件 {len(docs)} 份。")

    async with httpx.AsyncClient() as c:
        while time.perf_counter() < deadline:
            batch = []
            for _ in range(random.randint(1, args.concurrent)):
                doc = random.choice(docs)
                batch.append(ask(c, doc["doc_id"], random.choice(questions_for(doc))))
            for r in await asyncio.gather(*batch):
                n += 1
                if r:
                    cost += r.get("cost_usd", 0)
            left = max(deadline - time.perf_counter(), 0)
            print(f"  已送出 {n} 次 · 累計成本 US${cost:.4f} · 剩餘 {left/60:.1f} 分鐘")
            await asyncio.sleep(random.uniform(6, 14))

    print(f"\n完成：{n} 次查詢，總成本 US${cost:.4f}")
    print("開啟 http://localhost:3001 截圖")


if __name__ == "__main__":
    asyncio.run(main())
