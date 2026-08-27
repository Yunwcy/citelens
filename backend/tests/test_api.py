"""HTTP 介面的輸入驗證與錯誤處理。

這些測試不需要模型或測試文件，因此在 CI 上永遠會跑。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_健康檢查回報檢索預算(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["retrieval_budget"] == 7000


def test_只接受_pdf_副檔名(client):
    r = client.post("/api/documents", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_副檔名對但內容不是_pdf_也要擋(client):
    """只看副檔名不夠 —— 必須驗證檔頭。"""
    r = client.post("/api/documents", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400


def test_查詢不存在的文件回_404(client):
    r = client.post("/api/query/sync", json={"doc_id": "nope", "question": "hi"})
    assert r.status_code == 404


def test_空白問題被擋下(client):
    r = client.post("/api/query/sync", json={"doc_id": "x", "question": ""})
    assert r.status_code == 422


def test_找不到工作回_404(client):
    assert client.get("/api/jobs/nope/events").status_code == 404


async def _fake_stream(prompt, system=None, **kw):
    """假的模型：只吐固定文字，讓串流路徑可以在沒有 API key 的情況下跑完。"""
    for piece in ("依據文件，", "雙層檢索分為低階與高階 [1]。"):
        yield piece


def test_串流問答能跑完整條路徑(lightrag_index, monkeypatch):
    """這條路徑原本沒有任何測試 —— 於是 metrics.record 收到重複的
    declined 關鍵字時，答案已經串流出去、卻在送出 done 之前就中斷。

    症狀是前端拿不到來源與 debug，但畫面上答案看起來是好的：
    又一個不會報錯的錯。
    """
    import asyncio

    from app.llm import client
    from app.services import qa

    monkeypatch.setattr(client, "generate_stream", _fake_stream)

    async def run():
        return [e async for e in qa.answer_stream(lightrag_index, "什麼是雙層檢索？")]

    events = asyncio.run(run())
    kinds = [e["type"] for e in events]
    assert kinds[0] == "route"
    assert "token" in kinds
    assert kinds[-1] == "done", f"串流未正常結束：{kinds[-3:]}"

    done = events[-1]
    assert "sources" in done and "debug" in done
    assert "declined" in done["debug"]


def test_摘要失敗不得讓整份文件被判定為失敗(lightrag_pdf, monkeypatch, tmp_path):
    """摘要是索引流程裡唯一需要外部服務的一步。

    實測拔網路上傳：解析、切塊、向量化全部成功，只有摘要失敗 ——
    但當時整個工作被標成 failed，介面顯示「連線錯誤」，
    看起來像整份文件都沒上傳成功。索引其實已經可以提問了。
    """
    import asyncio

    from app.config import settings
    from app.services import documents
    from app.summarization import hierarchical

    async def _boom(*a, **kw):
        raise RuntimeError("Connection error.")

    monkeypatch.setattr(hierarchical, "build", _boom)
    monkeypatch.setattr(settings, "storage_dir", tmp_path)

    async def run():
        job = await documents.submit(lightrag_pdf.name, lightrag_pdf.read_bytes())
        for _ in range(600):
            if job.done:
                break
            await asyncio.sleep(0.5)
        return job

    job = asyncio.run(run())
    assert job.error is None, f"摘要失敗不應讓工作失敗：{job.error}"
    assert job.stage == "ready"
    assert job.detail.get("summary_ready") is False
