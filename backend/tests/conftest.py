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
