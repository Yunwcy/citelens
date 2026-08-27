"""查詢路由的規則保證。

規則式路由的好處是可測試且可重現；這些測試同時是路由行為的規格書。
"""
from app.models import Table
from app.router import query_router as qr


def _table() -> Table:
    t = Table(table_id="T2", page=8, caption="Table 2", y0=0, y1=1, order=0, strategy="booktabs")
    t.columns = ["Agriculture / NaiveRAG", "CS / -High"]
    t.rows = [("Diversity", {"CS / -High": "63.2%"}), ("Overall", {"CS / -High": "56.0%"})]
    return t


def test_摘要關鍵字走摘要路由():
    for q in ["summary this document", "請幫我摘要這份文件", "give me an overview"]:
        assert qr.route(q).name == "summary", q


def test_比較關鍵字走比較路由():
    for q in ["compare lightRAG with GraphRAG", "LightRAG 和 GraphRAG 有什麼差別",
              "LightRAG vs GraphRAG"]:
        assert qr.route(q).name == "comparison", q


def test_能抽出被比較的兩個對象():
    assert qr.route("compare lightRAG with GraphRAG").entities == ["lightRAG", "GraphRAG"]
    assert qr.route("LightRAG vs GraphRAG").entities == ["LightRAG", "GraphRAG"]
    assert qr.route("比較 LightRAG 和 GraphRAG").entities == ["LightRAG", "GraphRAG"]


def test_命中兩個座標標籤才走查表():
    """只命中一個標籤時不得走查表 —— 那很可能只是正文用詞。"""
    tables = {"T2": _table()}
    assert qr.route("Diversity 在 CS / -High 是多少", tables).name == "table_lookup"
    assert qr.route("tell me about Diversity", tables).name == "qa"


def test_一般問題落到問答路由():
    assert qr.route("Performance of ablated versions of LightRAG").name == "qa"


def test_子查詢拆解可重現():
    """同一個問題永遠拆出同一組子查詢，評估數據才可重現。"""
    r = qr.route("compare lightRAG with GraphRAG")
    a = qr.subqueries("compare lightRAG with GraphRAG", r.entities)
    b = qr.subqueries("compare lightRAG with GraphRAG", r.entities)
    assert a == b
    assert len(a) == 5
    assert any("cost" in q for q in a), "必須有一路查詢成本面向"


def test_抽不出對象時退回單一查詢():
    assert qr.subqueries("compare them", []) == ["compare them"]


def test_多區塊表格的每個區塊都能被路由命中(lightrag):
    """t.columns 只保留最後一個區塊的命名 —— 用它建標籤集會漏掉其他區塊。

    實測 Table 2 的 20 個欄鍵裡有 12 個不在 columns 內，
    包含全部的 LightRAG 與 -High / -Low 欄。
    """
    from app.router import query_router

    tables = {t.table_id: t for t in lightrag.tables}
    for entity in ("LightRAG", "-High", "-Low", "-Origin", "NaiveRAG"):
        r = query_router.route(f"{entity} 在 Legal 資料集上的 Diversity 是多少？", tables)
        assert r.name == "table_lookup", f"{entity} 未走表格查詢，實得 {r.name}（{r.reason}）"
