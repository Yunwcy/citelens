import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

TESTDOCS = ROOT / "_working" / "testdocs"


@pytest.fixture(scope="session")
def lightrag_pdf() -> Path:
    """作業指定的文件。缺檔時跳過，由 scripts/fetch_test_docs.sh 取得。"""
    path = TESTDOCS / "2410.05779.pdf"
    if not path.exists():
        pytest.skip("測試文件不存在，請先執行 scripts/fetch_test_docs.sh")
    return path


@pytest.fixture(scope="session")
def all_pdfs() -> list[Path]:
    paths = sorted(TESTDOCS.glob("*.pdf"))
    if not paths:
        pytest.skip("測試文件不存在，請先執行 scripts/fetch_test_docs.sh")
    return paths


@pytest.fixture(scope="session")
def lightrag(lightrag_pdf):
    from app.services.ingest import ingest
    return ingest(lightrag_pdf)


@pytest.fixture(scope="session")
def lightrag_index(lightrag):
    from app.retrieval.index import DocumentIndex
    return DocumentIndex.build("test-lightrag", lightrag)


@pytest.fixture(scope="session", autouse=True)
def _release_onnx_models():
    """測試結束前釋放向量化模型。

    fastembed 的 ONNX 執行環境若留到直譯器結束才釋放，在 macOS 上會拋出
    recursive_mutex 錯誤並使行程以非零狀態結束 —— 測試全部通過，CI 卻是紅燈。
    """
    yield
    import gc

    from app.retrieval.embedding import get_embedder

    get_embedder.cache_clear()
    gc.collect()
