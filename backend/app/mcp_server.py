"""MCP server：把檢索核心以工具形式對外暴露。

設計主張：agent 負責決策與編排，檢索負責可驗證的事實。
本系統刻意採用確定性的檢索路徑（規則路由、固定子查詢、確定性查表），
使同一個問題永遠得到同一組證據；agent 的不確定性因此不會污染事實層。

執行：
    python -m app.mcp_server            （stdio）
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

from app.services import documents

log = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "search_document",
        "description": "在已索引的文件中檢索，回傳帶頁碼與章節出處的片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "文件識別碼"},
                "query": {"type": "string", "description": "查詢字串，支援中英文"},
                "top_k": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
            },
            "required": ["doc_id", "query"],
        },
    },
    {
        "name": "get_table_cell",
        "description": ("確定性查表：依座標取回表格儲存格的值。"
                        "值來自解析後的結構化索引，非由模型生成，因此不會有幻覺。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "table_id": {"type": "string", "description": "例如 T2"},
                "row": {"type": "string", "description": "列標題，例如 -High / Diversity"},
                "column": {"type": "string", "description": "欄名，例如 CS / -High"},
            },
            "required": ["doc_id", "table_id"],
        },
    },
    {
        "name": "summarize_document",
        "description": "取回文件的階層式摘要（以章節為單位產生後快取）。",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "list_documents",
        "description": "列出已索引的文件。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


async def call(name: str, args: dict) -> dict:
    if name == "list_documents":
        return {"documents": documents.list_documents()}

    if name == "summarize_document":
        from app.summarization import hierarchical

        data = hierarchical.load(args["doc_id"])
        if data is None:
            return {"error": "摘要尚未產生"}
        return {"summary": data["summary"],
                "sections": [s["section"] for s in data["section_summaries"]]}

    idx = await documents.get_index(args["doc_id"])

    if name == "search_document":
        hits = idx.search(args["query"], top_k=args.get("top_k", 8))
        return {"hits": [
            {"page": idx.chunk(h.index).page,
             "section": idx.chunk(h.index).section_title,
             "kind": idx.chunk(h.index).kind,
             "score": round(h.score, 4),
             "text": idx.chunk(h.index).text}
            for h in hits
        ]}

    if name == "get_table_cell":
        table = idx.tables.get(args["table_id"])
        if table is None:
            return {"error": f"找不到 {args['table_id']}",
                    "available": sorted(idx.tables)}
        if not table.validated or table.kind != "data":
            return {"error": "這張表未通過解析驗證，僅提供整表原文",
                    "note": table.validation_note, "markdown": table.markdown}
        if "row" not in args or "column" not in args:
            return {"table_id": table.table_id, "caption": table.caption,
                    "rows": [r for r, _ in table.rows], "columns": table.columns}
        value = table.cell(args["row"], args["column"])
        return {"value": value, "found": value is not None,
                "caption": table.caption, "page": table.page}

    return {"error": f"未知的工具 {name}"}


async def serve() -> None:
    """最小 stdio JSON-RPC 迴圈。"""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    while line := await reader.readline():
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "citelens", "version": "0.1.0"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = req.get("params", {})
            try:
                result = await call(params["name"], params.get("arguments", {}))
            except Exception as exc:                       # noqa: BLE001
                result = {"error": str(exc)}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(result, ensure_ascii=False)}]}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": f"未支援的方法 {method}"}})


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(serve())
