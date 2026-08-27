"""Demo 前的驗收閘門。全程走 nginx，不走後端埠。

為什麼要另外做一支：backend/tests 的 55 項全部是 in-process（TestClient），
**沒有任何一項真的經過 nginx** —— 而 nginx 正是唯一踩過整合坑的地方
（上傳 413、backend 掛掉但 /health 仍回 200）。單元測試抓不到那種問題。

用法：
    python scripts/demo_gate.py              # 非破壞性項目
    python scripts/demo_gate.py --full       # 另含重啟與停用後端（會中斷服務）
    python scripts/demo_gate.py --cold       # 先 down 再 up，量測冷啟動

任何一項紅燈就不要 freeze code。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "_working" / "testdocs"
BASE = "http://localhost:3000"          # 一律走 nginx
GRAFANA = "http://localhost:3001"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {'🟢' if ok else '🔴'} {name}" + (f"　{detail}" if detail else ""))
    return ok


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT,
                          capture_output=True, text=True)


def wait_ready(timeout: float = 180) -> float:
    """等到 /health 真的回 200 且內容正確。回傳等待秒數。"""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        try:
            r = httpx.get(f"{BASE}/health", timeout=5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return time.perf_counter() - start
        except Exception:                                   # noqa: BLE001
            pass
        time.sleep(2)
    raise TimeoutError(f"{timeout} 秒內服務未就緒")


def upload(path: Path) -> str:
    """上傳並等到索引完成，回傳 doc_id。"""
    with path.open("rb") as fh:
        r = httpx.post(f"{BASE}/api/documents", files={"file": (path.name, fh, "application/pdf")},
                       timeout=120)
    r.raise_for_status()
    doc_id = r.json()["doc_id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        docs = httpx.get(f"{BASE}/api/documents", timeout=30).json()
        hit = next((d for d in docs if d["doc_id"] == doc_id), None)
        if hit and hit.get("chunks"):
            return doc_id
        time.sleep(3)
    raise TimeoutError(f"{path.name} 索引未完成")


def ask(doc_id: str, question: str) -> dict:
    """走 SSE 問一題，收完整答案與 debug。"""
    text, sources, debug, stages = "", [], {}, []
    with httpx.stream("POST", f"{BASE}/api/query", timeout=300,
                      json={"doc_id": doc_id, "question": question}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "token":
                text += ev["text"]
            elif ev["type"] == "stage":
                stages.append(ev["stage"])
            elif ev["type"] == "done":
                sources, debug = ev["sources"], ev["debug"]
    return {"text": text, "sources": sources, "debug": debug, "stages": stages}


# --- 各項檢查 ---------------------------------------------------------------

def gate_services() -> None:
    print("\n【服務】")
    for name, url, want in [("介面（nginx）", BASE, 200),
                            ("健康檢查", f"{BASE}/health", 200),
                            ("指標端點", f"{BASE}/api/metrics", 200),
                            ("API 文件", f"{BASE}/docs", 200)]:
        try:
            r = httpx.get(url, timeout=10)
            check(name, r.status_code == want, f"HTTP {r.status_code}")
        except Exception as exc:                            # noqa: BLE001
            check(name, False, str(exc)[:60])

    try:
        r = httpx.get(f"{GRAFANA}/api/health", timeout=10)
        check("Grafana", r.status_code == 200, r.json().get("database", ""))
    except Exception as exc:                                # noqa: BLE001
        check("Grafana", False, str(exc)[:60])

    # health 必須是後端真的回的，不能是 SPA fallback 回的 HTML
    r = httpx.get(f"{BASE}/health", timeout=10)
    check("/health 回的是 JSON 不是 SPA 頁面",
          "application/json" in r.headers.get("content-type", ""),
          r.headers.get("content-type", "?"))


def gate_upload() -> str:
    print("\n【上傳】")
    big = DOCS / "1706.03762.pdf"                # 2.1MB，遠超 nginx 預設的 1MB
    target = DOCS / "2410.05779.pdf"             # 作業指定文件，1.1MB

    t = time.perf_counter()
    try:
        upload(big)
        check(f"大檔上傳（{big.stat().st_size / 1e6:.1f}MB）", True,
              f"{time.perf_counter() - t:.0f} 秒")
    except Exception as exc:                                # noqa: BLE001
        check(f"大檔上傳（{big.stat().st_size / 1e6:.1f}MB）", False, str(exc)[:70])

    t = time.perf_counter()
    doc_id = upload(target)
    check("指定文件索引完成", True, f"{time.perf_counter() - t:.0f} 秒")

    # 非 PDF 必須被明確拒絕，而不是當掉
    r = httpx.post(f"{BASE}/api/documents",
                   files={"file": ("x.txt", b"not a pdf", "text/plain")}, timeout=30)
    check("非 PDF 被明確拒絕", r.status_code == 400, f"HTTP {r.status_code}")

    # arXiv 連結
    try:
        r = httpx.post(f"{BASE}/api/documents/from-url", timeout=120,
                       json={"url": "https://arxiv.org/abs/2410.05779"})
        check("arXiv 連結匯入", r.status_code == 202, f"HTTP {r.status_code}")
    except Exception as exc:                                # noqa: BLE001
        check("arXiv 連結匯入", False, str(exc)[:70])

    # SSRF 仍要擋
    r = httpx.post(f"{BASE}/api/documents/from-url", timeout=30,
                   json={"url": "http://169.254.169.254/latest/meta-data/"})
    check("SSRF 內部位址被擋", r.status_code == 400, f"HTTP {r.status_code}")
    return doc_id


def gate_golden(doc_id: str) -> None:
    """三個官方指定問題。斷言的是內容，不只是「有回答」。"""
    print("\n【三個官方問題】")

    # ① 摘要：關鍵證據不得被改寫成程度副詞
    a = ask(doc_id, "summary this document")
    check("① 摘要　有作答", len(a["text"]) > 200, f"{len(a['text'])} 字")
    check("① 摘要　保留 610,000 這個數字", "610,000" in a["text"] or "610000" in a["text"])
    check("① 摘要　保留「不到 100 tokens」的對照",
          bool(re.search(r"(<\s*100|不到\s*100|less than 100|under 100)", a["text"])))
    check("① 摘要　來源不為空", len(a["sources"]) > 0, f"{len(a['sources'])} 個來源")
    check("① 摘要　走摘要路由", a["debug"].get("route") == "summary", a["debug"].get("route", "?"))

    # ② 比較：雙方都要有，且不得拿 NaiveRAG 的數字當 GraphRAG
    b = ask(doc_id, "compare lightRAG with GraphRAG")
    check("② 比較　兩方都有描述",
          "LightRAG" in b["text"] and "GraphRAG" in b["text"])
    check("② 比較　走比較路由", b["debug"].get("route") == "comparison",
          b["debug"].get("route", "?"))
    # 涵蓋面看的是答案談到哪些面向，不是引用到幾個章節 ——
    # 章節數取決於模型引用了誰，那不是系統可控的性質。
    dims = {
        "架構": bool(re.search(r"(dual-level|雙層|graph|圖索引|retrieval paradigm)", b["text"], re.I)),
        "效能": bool(re.search(r"\d+\.\d+%", b["text"])),
        "成本": bool(re.search(r"(610,?000|token|cost|成本|API call)", b["text"], re.I)),
    }
    check("② 比較　涵蓋架構／效能／成本三面向", all(dims.values()),
          "、".join(k for k, v in dims.items() if not v) + " 缺漏" if not all(dims.values())
          else "三項齊全")
    # Diversity 上 LightRAG 確實四個資料集全勝，全稱說法是對的；
    # Comprehensiveness 只勝三個（Mix 為 49.6% 對 50.4%），全稱就是錯的。
    bad_claim = [
        sent for sent in re.split(r"[.。\n]", b["text"])
        if re.search(r"comprehensiveness|完整性", sent, re.I)
        and re.search(r"(all (four )?datasets|所有(四個)?資料集|全面)", sent, re.I)
    ]
    # 勝率表是兩兩對比，配對的兩數必相加為 100 ——
    # 相加不等於 100 就代表兩個數字取自不同的基線區塊。
    pairs = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(
        r"(\d+\.\d+)%[^。\n]{0,26}?(?:而|vs\.?|versus)[^。\n]{0,26}?(\d+\.\d+)%", b["text"])]
    off = [(a, c) for a, c in pairs if abs(a + c - 100) >= 0.6]
    check("② 比較　配對數值取自同一列", not off,
          f"{len(pairs)} 組配對" + (f"　錯配：{off}" if off else "　全部相加為 100%"))

    check("② 比較　Comprehensiveness 未被誤稱為全勝", not bad_claim,
          (bad_claim[0].strip()[:70] + "…") if bad_claim else "已正確限定")

    # ③ 消融：必須真的出現消融變體，而不是只有基線
    c = ask(doc_id, "Performance of ablated versions of LightRAG")
    variants = [v for v in ("-High", "-Low", "-Origin") if v in c["text"]]
    check("③ 消融　三個變體都出現", len(variants) == 3, f"出現 {variants}")
    check("③ 消融　以表格呈現", c["text"].count("|") > 20, f"{c['text'].count('|')} 個分隔符")
    check("③ 消融　答案沒有被截斷", not c["debug"].get("answer_truncated"),
          f"{c['debug'].get('completion_tokens', 0)} tokens"
          + ("　撞到輸出上限" if c["debug"].get("answer_truncated") else ""))

    # 共通：引用與預算
    print("\n【引用與預算】")
    for tag, res in (("①", a), ("②", b), ("③", c)):
        cited = {int(x) for x in re.findall(r"\[(\d+)\]", res["text"])}
        # 編號是脈絡區塊的序號（1..packed），不是來源清單的序號 ——
        # _used_sources 刻意保留原編號，答案裡的 [5] 才對得上面板的 [5]。
        packed = res["debug"].get("packed", 0)
        bad = sorted(x for x in cited if not (1 <= x <= max(packed, 1)))
        check(f"{tag} 引用編號都在範圍內", not bad,
              f"{sorted(cited)} / 共 {packed} 段" + (f"　超出：{bad}" if bad else ""))
        if res["debug"].get("route") != "summary":
            check(f"{tag} 有標註引用", bool(cited),
                  "無任何 [n] 標記" if not cited else f"{len(cited)} 個")
        budget = res["debug"].get("context_budget", 7000)
        used = res["debug"].get("context_tokens", 0)
        check(f"{tag} 脈絡未超標", used <= budget, f"{used} / {budget}")


def gate_generic_comparison(doc_id: str) -> None:
    """未點名對象的比較問法。

    「比較文中提到的方法」抽不出實體，區塊過濾因此不生效，
    四個基線區塊全部留在脈絡裡 —— 實測模型會把 LightRAG 對 NaiveRAG 的
    勝率，拿去和 GraphRAG 的數字並列，四組配對全錯。
    """
    print("\n【未點名對象的比較】")
    r = ask(doc_id, "比較文中提到的方法")
    pairs = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(
        r"(\d+\.\d+)%[^。\n]{0,26}?(?:而|vs\.?|versus)[^。\n]{0,26}?(\d+\.\d+)%", r["text"])]
    off = [(a, c) for a, c in pairs if abs(a + c - 100) >= 0.6]
    check("配對數值取自同一列", not off,
          f"{len(pairs)} 組配對" + (f"　錯配：{off}" if off else "　全部相加為 100%"))
    check("有標註引用", bool(re.findall(r"\[\d+\]", r["text"])))


def gate_isolation(doc_id: str) -> None:
    """A 文件不得污染 B 文件。"""
    print("\n【文件隔離】")
    other = upload(DOCS / "1810.04805.pdf")      # BERT
    r = ask(other, "What is LightRAG's dual-level retrieval paradigm?")
    declined = r["debug"].get("declined") or re.search(
        r"(未提及|沒有提到|not (mentioned|covered|found)|does not (mention|contain))", r["text"])
    check("向 BERT 問 LightRAG 應拒答", bool(declined),
          "已拒答" if declined else r["text"][:60])


def gate_persistence() -> None:
    print("\n【重啟後仍在】")
    before = {d["doc_id"] for d in httpx.get(f"{BASE}/api/documents", timeout=30).json()}
    compose("restart", "backend")
    waited = wait_ready()
    after = httpx.get(f"{BASE}/api/documents", timeout=30).json()
    check("重啟後文件仍在", {d["doc_id"] for d in after} >= before,
          f"{len(after)} 份 · 重啟耗時 {waited:.0f} 秒")
    doc_id = after[0]["doc_id"]
    r = ask(doc_id, "這份文件在講什麼？")
    check("重啟後可直接提問", len(r["text"]) > 50, f"{len(r['text'])} 字")


def gate_health_when_down() -> None:
    """backend 停掉時 /health 必須失敗 —— 曾經因 SPA fallback 回 200。"""
    print("\n【後端停用】")
    compose("stop", "backend")
    time.sleep(3)
    try:
        r = httpx.get(f"{BASE}/health", timeout=10)
        code = r.status_code
    except Exception:                                       # noqa: BLE001
        code = 0
    check("backend 停用時 /health 不得回 200", code != 200, f"HTTP {code or '連線失敗'}")
    compose("start", "backend")
    wait_ready()
    check("backend 復原", True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="含重啟與停用後端（會中斷服務）")
    ap.add_argument("--cold", action="store_true", help="先 down 再 up，量測冷啟動")
    args = ap.parse_args()

    print("Demo 驗收閘門　全程走 nginx（:3000）")

    if args.cold:
        print("\n【冷啟動】")
        compose("down")
        t = time.perf_counter()
        compose("up", "-d")
        waited = wait_ready(300)
        check("冷啟動後服務就緒", True, f"{waited:.0f} 秒（含容器啟動 {time.perf_counter() - t:.0f} 秒）")

    gate_services()
    doc_id = gate_upload()
    gate_golden(doc_id)
    gate_generic_comparison(doc_id)
    gate_isolation(doc_id)
    if args.full:
        gate_persistence()
        gate_health_when_down()

    red = [n for n, ok, _ in results if not ok]
    print(f"\n{'─' * 58}")
    print(f"{len(results) - len(red)} / {len(results)} 綠燈")
    if red:
        print("\n紅燈：")
        for n in red:
            print(f"  🔴 {n}")
        print("\n有紅燈就不要 freeze code。")
    else:
        print("全數通過。")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
