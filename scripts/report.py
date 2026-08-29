"""把指標紀錄整理成報表。

容器以具名卷保存資料，主機上的 ./storage 是另一份 —— 直接執行只會讀到
本機開發時留下的舊紀錄。要對容器內的實際流量出報表，加上 --container。

用法：
    python scripts/report.py                                    # 讀本機 storage
    python scripts/report.py --container                        # 讀執行中的容器
    python scripts/report.py --container --md docs/results/runtime.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.observability import metrics  # noqa: E402


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(int(len(s) * p), len(s) - 1)]


def build(rows: list[dict]) -> str:
    idx = [r for r in rows if r["event"] == "index"]
    qry = [r for r in rows if r["event"] == "query"]

    # 指標檔會跨設定累積。脈絡預算改過之後，舊紀錄描述的是另一套系統 ——
    # 混在一起算，分母會變成兩個時期的混合值，而報表看起來完全正常。
    # 現在的預算不可能超過 retrieval_budget，比它大的就是改設定前留下的。
    ceiling = settings.retrieval_budget
    stale = [r for r in qry if r.get("context_budget", 0) > ceiling]
    qry = [r for r in qry if r not in stale]

    out: list[str] = ["# 執行指標", "",
                      f"樣本：索引 {len(idx)} 次 · 查詢 {len(qry)} 次",
                      "", "重現：`python scripts/report.py --md docs/results/runtime.md`", ""]
    if stale:
        out += [f"> 另有 {len(stale)} 筆查詢紀錄產生於脈絡預算調整之前"
                f"（額度上限非現行的 {ceiling}），未計入。", ""]

    if idx:
        secs = [r.get("index_seconds", 0) for r in idx]
        out += ["## 索引", "", "| 指標 | 值 |", "|---|---|",
                f"| 平均耗時 | {statistics.mean(secs):.1f}s |",
                f"| 吞吐 | {statistics.mean([r.get('chunks_per_second', 0) for r in idx]):.1f} 片段/秒 |",
                f"| 表格驗證通過 | {sum(r.get('tables_validated', 0) for r in idx)}"
                f" / {sum(r.get('tables', 0) for r in idx)} |",
                f"| 索引階段外部 API 呼叫 | {sum(r.get('api_calls', 0) for r in idx)} |", ""]

    if qry:
        tot = [r.get("total_ms", 0) for r in qry]
        ret = [r.get("retrieval_ms", 0) for r in qry]
        llm = [r.get("llm_ms", 0) for r in qry]
        ctx = [r.get("context_tokens", 0) for r in qry]
        cost = sum(r.get("cost_usd", 0) for r in qry)
        # 額度是每一次請求各自算出來的（問題越長、能放的文件內容越少），
        # 所以沒有單一分母可寫。取最常見的那個當代表，並在額度不只一種時
        # 附上實際觀察到的範圍 —— 直接拿最後一筆當全體，會把某一次長問題
        # 的縮減額度誤植成整份報表的分母。
        budgets = [r["context_budget"] for r in qry if r.get("context_budget")]
        budget = statistics.mode(budgets) if budgets else settings.retrieval_budget
        span = ("" if len(set(budgets)) <= 1
                else f"（本批次觀察到 {min(budgets)}–{max(budgets)}）")
        used_pct = [r["context_tokens"] / r["context_budget"] * 100
                    for r in qry if r.get("context_budget")]

        out += ["## 查詢延遲", "", "| 階段 | p50 | p95 |", "|---|---:|---:|",
                f"| 檢索 | {pct(ret, .5):.0f} ms | {pct(ret, .95):.0f} ms |",
                f"| 生成 | {pct(llm, .5):.0f} ms | {pct(llm, .95):.0f} ms |",
                f"| 端到端 | {pct(tot, .5) / 1000:.2f} s | {pct(tot, .95) / 1000:.2f} s |", "",
                "## 脈絡與成本", "", "| 指標 | 值 |", "|---|---|",
                f"| 脈絡用量 p50 | {pct(ctx, .5):.0f} / {budget} tokens{span} |",
                f"| 脈絡用量 p95 | {pct(ctx, .95):.0f} / {budget} tokens{span} |",
                f"| 佔各自額度的比例 p50 / p95 | {pct(used_pct, .5):.0f}%"
                f" / {pct(used_pct, .95):.0f}% |",
                f"| 因預算不足而捨棄的片段 | {sum(len(r.get('dropped', [])) for r in qry)} |",
                f"| 平均每次查詢 | {statistics.mean([r.get('prompt_tokens', 0) for r in qry]):.0f} in"
                f" / {statistics.mean([r.get('completion_tokens', 0) for r in qry]):.0f} out |",
                f"| 平均每次成本 | US${cost / len(qry):.6f} |",
                f"| 累計成本 | US${cost:.4f} |", "",
                "## 回答品質", "", "| 結果 | 次數 |", "|---|---:|"]

        # 分母只計「真的產生了答案」的那些。
        #
        # 排除拒答：文件未涵蓋該問題時模型應如實說明，這類回答本來就沒有
        # 可標註的來源，計入會把正確行為算成失敗。
        #
        # 排除連線中斷：串流失敗時根本沒有答案，卻會被記成 cited=False。
        # 實測離線驗收跑過幾輪之後，引用率由 100% 掉到 97% —— 那 16 筆
        # 全部是 APIConnectionError，不是模型忘了標引用。
        answered = [r for r in qry if "cited" in r and not r.get("stream_error")]
        broken_n = sum(1 for r in qry if r.get("stream_error"))
        declined_n = sum(1 for r in answered if r.get("declined"))
        cited_n = sum(1 for r in answered if r.get("cited") and not r.get("declined"))
        uncited_n = len(answered) - cited_n - declined_n
        rate = f"{cited_n / (cited_n + uncited_n) * 100:.0f}%" if cited_n + uncited_n else "—"
        out += [f"| 有標註引用 | {cited_n} |",
                f"| 有作答但未標註引用 | {uncited_n} |",
                f"| 文件未涵蓋而如實拒答 | {declined_n} |",
                f"| 連線中斷而未產生答案 | {broken_n} |",
                f"| **有作答時的引用率** | **{rate}** |", "",
                "## 路由分布", "", "| 路由 | 次數 |", "|---|---:|"]
        routes: dict[str, int] = {}
        for r in qry:
            routes[r.get("route", "?")] = routes.get(r.get("route", "?"), 0) + 1
        out += [f"| {k} | {v} |" for k, v in sorted(routes.items())] + [""]

    return "\n".join(out)


def read_from_container() -> list[dict]:
    """由執行中的容器取出指標。"""
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "cat", "/data/metrics.jsonl"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"無法讀取容器內的指標：{r.stderr.strip()[:200]}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md")
    ap.add_argument("--container", action="store_true",
                    help="改讀執行中容器的 /data/metrics.jsonl")
    args = ap.parse_args()
    rows = read_from_container() if args.container else metrics.read_all()
    if not rows:
        print("尚無指標資料。先執行 scripts/ask.py 或透過介面提問。")
        return
    text = build(rows)
    print(text)
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"\n已寫入 {args.md}")


if __name__ == "__main__":
    main()
