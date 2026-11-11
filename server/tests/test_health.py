from app.main import app
from fastapi.testclient import TestClient


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "OK"
