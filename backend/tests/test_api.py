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
