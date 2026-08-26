"""Prometheus 指標。

與 metrics.jsonl 記錄的是同一組數字，只是換一種格式輸出：
JSONL 供離線報表與逐筆稽核，Prometheus 供即時觀察與 Grafana 儀表板。
兩者共用同一個進入點，不會出現「報表與儀表板數字對不上」的情形。
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

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
    "citelens_prompt_tokens", "送進模型的 token 數",
    buckets=(500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000),
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
        QUERIES.labels(route=fields.get("route", "unknown")).inc()
        RETRIEVAL_SECONDS.observe(fields.get("retrieval_ms", 0) / 1000)
        LLM_SECONDS.observe(fields.get("llm_ms", 0) / 1000)
        REQUEST_SECONDS.observe(fields.get("total_ms", 0) / 1000)
        PROMPT_TOKENS.observe(fields.get("prompt_tokens", 0))
        COST.inc(fields.get("cost_usd", 0))
        if fields.get("dropped"):
            DROPPED.inc(len(fields["dropped"]))

    elif event == "index":
        DOCUMENTS.inc()
        INDEX_SECONDS.observe(fields.get("index_seconds", 0))
        INDEX_API_CALLS.inc(fields.get("api_calls", 0))
        ok = fields.get("tables_validated", 0)
        TABLES.labels(validated="true").inc(ok)
        TABLES.labels(validated="false").inc(max(fields.get("tables", 0) - ok, 0))


def render() -> bytes:
    return generate_latest(REGISTRY)
