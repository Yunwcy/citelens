"""Prometheus 指標。

與 metrics.jsonl 記錄的是同一組數字，只是換一種格式輸出：
JSONL 供離線報表與逐筆稽核，Prometheus 供即時觀察與 Grafana 儀表板。
兩者共用同一個進入點，不會出現「報表與儀表板數字對不上」的情形。
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

QUERIES = Counter(
    "citelens_query_total", "查詢次數", ["route"], registry=REGISTRY,
)
DOCUMENTS = Counter(
    "citelens_index_total", "建立索引次數", registry=REGISTRY,
)
INDEX_API_CALLS = Counter(
    "citelens_index_external_api_calls_total",
    "索引階段的外部 API 呼叫次數（本地向量化時恆為 0）", registry=REGISTRY,
)
TABLES = Counter(
    "citelens_table_total", "偵測到的表格數", ["validated"], registry=REGISTRY,
)
DROPPED = Counter(
    "citelens_context_dropped_chunks_total", "因預算不足而捨棄的片段數", registry=REGISTRY,
)
COST = Counter(
    "citelens_cost_usd_total", "累計模型成本（USD）", registry=REGISTRY,
)
CITED = Counter(
    "citelens_answer_cited_total", "答案有標註引用編號的次數", ["cited"], registry=REGISTRY,
)
OUTCOME = Counter(
    "citelens_answer_outcome_total",
    "回答結果：cited 有標註引用、uncited 有作答但未標註、declined 文件未涵蓋",
    ["outcome"], registry=REGISTRY,
)

# --- 離線評估結果（由 scripts/eval.py 發布）---------------------------------
EVAL_TOP3 = Gauge(
    "citelens_eval_top3_rate", "目標章節進入前三名的比例", ["config"], registry=REGISTRY,
)
EVAL_TOP3_COUNT = Gauge(
    "citelens_eval_top3_hits", "目標章節進入前三名的題數", ["config"], registry=REGISTRY,
)
EVAL_TOTAL = Gauge(
    "citelens_eval_total_queries", "評估用的查詢總數", registry=REGISTRY,
)
EVAL_RANK = Gauge(
    "citelens_eval_rank", "目標章節的名次（11 表示未進前十）", ["config", "query"],
    registry=REGISTRY,
)
EVAL_TABLES = Gauge(
    "citelens_eval_tables", "表格解析結果", ["state"], registry=REGISTRY,
)

# 桶界依實測分布設定：檢索在數十毫秒，生成在數秒
RETRIEVAL_SECONDS = Histogram(
    "citelens_retrieval_seconds", "檢索耗時",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
    registry=REGISTRY,
)
LLM_SECONDS = Histogram(
    "citelens_llm_seconds", "模型生成耗時",
    buckets=(0.5, 1, 2, 3, 5, 8, 12, 20, 30),
    registry=REGISTRY,
)
REQUEST_SECONDS = Histogram(
    "citelens_request_seconds", "查詢端到端耗時",
    buckets=(0.5, 1, 2, 3, 5, 8, 12, 20, 30),
    registry=REGISTRY,
)
PROMPT_TOKENS = Histogram(
    "citelens_prompt_tokens", "送進模型的 token 數（系統提示＋脈絡＋問題）",
    buckets=(500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000),
    registry=REGISTRY,
)
# 脈絡用量必須與 prompt_tokens 分開量測。prompt 還含系統提示與問題，
# 本來就會超過 7,000 的檢索預算 —— 拿它畫「脈絡用量」的面板，
# 會讓圖表看起來剛好壓在上限線上，而說明文字寫著「從未觸及上限」。
CONTEXT_TOKENS = Histogram(
    "citelens_context_tokens", "檢索內容佔用的 token 數（上限為檢索預算）",
    buckets=(500, 1000, 1500, 2000, 3000, 4000, 5000, 6000, 6500, 7000),
    registry=REGISTRY,
)
INDEX_SECONDS = Histogram(
    "citelens_index_seconds", "建立索引耗時",
    buckets=(1, 2, 5, 10, 20, 40, 80, 160),
    registry=REGISTRY,
)


def observe(event: str, fields: dict) -> None:
    """由 metrics.record() 呼叫，把同一筆事件同步到 Prometheus。"""
    if event == "query":
        route = fields.get("route", "unknown")
        QUERIES.labels(route=route).inc()
        REQUEST_SECONDS.observe(fields.get("total_ms", 0) / 1000)
        COST.inc(fields.get("cost_usd", 0))
        CITED.labels(cited=str(fields.get("cited", True)).lower()).inc()
        # 三分法：正確拒答不應計為「未標註引用」的失敗
        if fields.get("declined"):
            outcome = "declined"
        elif fields.get("cited", True):
            outcome = "cited"
        else:
            outcome = "uncited"
        OUTCOME.labels(outcome=outcome).inc()

        # 摘要路由不走檢索也不組裝脈絡，其 token 與耗時為 0。
        # 一併記入直方圖會在圖上產生往下掉的假凹陷，
        # 讓「脈絡用量」看起來像有時候只用了 0 個 token。
        if route != "summary":
            RETRIEVAL_SECONDS.observe(fields.get("retrieval_ms", 0) / 1000)
            LLM_SECONDS.observe(fields.get("llm_ms", 0) / 1000)
            PROMPT_TOKENS.observe(fields.get("prompt_tokens", 0))
            CONTEXT_TOKENS.observe(fields.get("context_tokens", 0))
        if fields.get("dropped"):
            DROPPED.inc(len(fields["dropped"]))

    elif event == "index":
        DOCUMENTS.inc()
        INDEX_SECONDS.observe(fields.get("index_seconds", 0))
        INDEX_API_CALLS.inc(fields.get("api_calls", 0))
        ok = fields.get("tables_validated", 0)
        TABLES.labels(validated="true").inc(ok)
        TABLES.labels(validated="false").inc(max(fields.get("tables", 0) - ok, 0))


def replay(rows: list[dict]) -> int:
    """重啟後由既有紀錄還原計數器。

    Prometheus 的 Counter 與 Histogram 都在行程記憶體裡，容器一重啟就歸零 ——
    儀表板的累計成本會變回 $0、引用率與回答結果分布變成 No data，
    但 metrics.jsonl 其實完整保留著。看起來像沒人用過，實際上只是忘了讀回來。

    直方圖重放後的分位數會以整段歷史計算，而非近期視窗；
    這對「累計」型面板是正確的，對延遲趨勢則仍以新進資料為準。
    """
    for row in rows:
        event = row.get("event")
        if event in ("query", "index"):
            observe(event, row)
    return len(rows)


def publish_eval(report: dict) -> None:
    """把離線評估結果寫成 gauge，讓準確度與執行指標出現在同一張儀表板上。

    檢索準確度是離線評估出來的，不是每次查詢都能算 —— 因為需要事先標好
    每個查詢的目標章節。因此由 scripts/eval.py 產生後發布到這裡。
    """
    # 先清空：設定名稱若有變動，舊的標籤組合會留在 gauge 上，
    # 儀表板會同時顯示新舊兩套數字。
    for gauge in (EVAL_TOP3, EVAL_TOP3_COUNT, EVAL_RANK, EVAL_TABLES):
        gauge.clear()

    for cfg, rows in report.get("retrieval", {}).items():
        hits = sum(1 for r in rows.values() if r.get("rank") and r["rank"] <= 3)
        EVAL_TOP3_COUNT.labels(config=cfg).set(hits)
        EVAL_TOP3.labels(config=cfg).set(hits / len(rows) if rows else 0)
        EVAL_TOTAL.set(len(rows))
        for query, r in rows.items():
            EVAL_RANK.labels(config=cfg, query=query[:60]).set(r.get("rank") or 11)

    for state, value in (report.get("tables") or {}).items():
        EVAL_TABLES.labels(state=state).set(value)


def render() -> bytes:
    return generate_latest(REGISTRY)
