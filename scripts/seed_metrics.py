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

QUESTIONS = [
    ("summary this document", "summary"),
    ("compare lightRAG with GraphRAG", "comparison"),
    ("Performance of ablated versions of LightRAG", "qa"),
    ("消融實驗的結果如何？", "qa"),
    ("LightRAG 和 GraphRAG 有什麼差別", "comparison"),
    ("What datasets were used in the evaluation?", "qa"),
    ("摘要這份文件", "summary"),
    ("實驗是怎麼設計的？", "qa"),
]


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
            batch = [
                ask(c, random.choice(docs)["doc_id"], random.choice(QUESTIONS)[0])
                for _ in range(random.randint(1, args.concurrent))
            ]
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
