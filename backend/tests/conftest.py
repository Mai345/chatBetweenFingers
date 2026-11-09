import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    database_path = tmp_path / "test.sqlite3"
    monkeypatch.setenv("CBF_DB_PATH", str(database_path))

    with TestClient(app) as test_client:
        yield test_client
