import pytest
from app.main import app
from fastapi.testclient import TestClient
from tests.conftest import DB_UP


def test_healthz_returns_ok() -> None:
    if not DB_UP:
        pytest.skip("Postgres not reachable; run `make up` first")
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "OK"
