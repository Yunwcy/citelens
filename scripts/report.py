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

from app.observability import metrics  # noqa: E402


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(int(len(s) * p), len(s) - 1)]


def build(rows: list[dict]) -> str:
    idx = [r for r in rows if r["event"] == "index"]
    qry = [r for r in rows if r["event"] == "query"]
    out: list[str] = ["# 執行指標", "",
                      f"樣本：索引 {len(idx)} 次 · 查詢 {len(qry)} 次",
                      "", "重現：`python scripts/report.py --md docs/results/runtime.md`", ""]

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
        budget = qry[-1].get("context_budget", 7000)

        out += ["## 查詢延遲", "", "| 階段 | p50 | p95 |", "|---|---:|---:|",
                f"| 檢索 | {pct(ret, .5):.0f} ms | {pct(ret, .95):.0f} ms |",
                f"| 生成 | {pct(llm, .5):.0f} ms | {pct(llm, .95):.0f} ms |",
                f"| 端到端 | {pct(tot, .5) / 1000:.2f} s | {pct(tot, .95) / 1000:.2f} s |", "",
                "## 脈絡與成本", "", "| 指標 | 值 |", "|---|---|",
                f"| 脈絡用量 p50 | {pct(ctx, .5):.0f} / {budget} tokens |",
                f"| 脈絡用量 p95 | {pct(ctx, .95):.0f} / {budget} tokens |",
                f"| 因預算不足而捨棄的片段 | {sum(len(r.get('dropped', [])) for r in qry)} |",
                f"| 平均每次查詢 | {statistics.mean([r.get('prompt_tokens', 0) for r in qry]):.0f} in"
                f" / {statistics.mean([r.get('completion_tokens', 0) for r in qry]):.0f} out |",
                f"| 平均每次成本 | US${cost / len(qry):.6f} |",
                f"| 累計成本 | US${cost:.4f} |", "",
                "## 回答品質", "", "| 結果 | 次數 |", "|---|---:|"]

        # 引用率的分母排除拒答：文件未涵蓋該問題時，模型應如實說明，
        # 這類回答本來就沒有可標註的來源，計入會把正確行為算成失敗。
        answered = [r for r in qry if "cited" in r]
        declined_n = sum(1 for r in answered if r.get("declined"))
        cited_n = sum(1 for r in answered if r.get("cited") and not r.get("declined"))
        uncited_n = len(answered) - cited_n - declined_n
        rate = f"{cited_n / (cited_n + uncited_n) * 100:.0f}%" if cited_n + uncited_n else "—"
        out += [f"| 有標註引用 | {cited_n} |",
                f"| 有作答但未標註引用 | {uncited_n} |",
                f"| 文件未涵蓋而如實拒答 | {declined_n} |",
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
