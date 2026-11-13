"""WP9: /ui static, /ui/api/* endpoints, demo isolation, escaping guards."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_pool")


@pytest.fixture()
def ui_client(fake_gateway, db_pool, monkeypatch):
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(gateway=fake_gateway)
    with TestClient(app) as c:
        yield c


def test_ui_index_served(ui_client) -> None:
    r = ui_client.get("/ui/")
    assert r.status_code == 200
    assert "Overview" in r.text and "app.js" in r.text
    for asset in ("/ui/app.js", "/ui/styles.css"):
        assert ui_client.get(asset).status_code == 200


def test_summary_endpoint_shape(ui_client) -> None:
    r = ui_client.get("/ui/api/summary?range=24h")
    body = r.json()
    assert r.status_code == 200 and body["demo"] is False
    for k in ("tokens_saved_est", "cache_hit_rate", "requests", "passthroughs", "latency_p95_ms"):
        assert k in body


def test_series_invalid_range_422(ui_client) -> None:
    r = ui_client.get("/ui/api/series?range=5m")
    assert r.status_code == 422
    ok = ui_client.get("/ui/api/series?range=1h")
    assert ok.status_code == 200 and "points" in ok.json()


def test_passes_and_requests_shapes(ui_client) -> None:
    p = ui_client.get("/ui/api/passes?range=7d").json()
    assert "passes" in p
    q = ui_client.get("/ui/api/requests?limit=10").json()
    assert {"total", "items"} <= set(q)


def test_benchmarks_not_measured_when_empty(ui_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ui_api.EVALS_RESULTS", __import__("pathlib").Path("/nonexistent-evals")
    )
    body = ui_client.get("/ui/api/benchmarks").json()
    assert body["measured"] is False and body["note"] == "NOT MEASURED"


def test_benchmarks_reads_real_e4_file(ui_client) -> None:
    body = ui_client.get("/ui/api/benchmarks").json()
    assert body["measured"] is True and body["e4"]["n_cells"] >= 1


def test_sessions_list_and_detail(ui_client) -> None:
    import uuid

    r = ui_client.post(
        "/sessions", json={"idea": "habit tracker"},
        headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
    )
    sid = r.json()["id"]
    lst = ui_client.get("/ui/api/sessions").json()
    assert any(s["id"] == sid for s in lst["sessions"])
    d = ui_client.get(f"/ui/api/sessions/{sid}")
    # no version yet (pipeline never ran) -> 404 is the correct answer
    assert d.status_code == 404
    assert ui_client.get(f"/ui/api/sessions/{sid}/openapi.yaml").status_code == 404


def test_demo_rows_isolated_from_stats(ui_client) -> None:
    import asyncio

    from scripts.seed_demo_traffic import seed

    asyncio.get_event_loop().run_until_complete(seed(ui_client.app.state.pool, 5))
    real = ui_client.get("/stats").json()  # /stats must never include demo rows
    assert real["requests"] == 0
    import os

    os.environ["UI_DEMO"] = "1"
    try:
        s = ui_client.get("/ui/api/summary?range=24h").json()
        assert s["demo"] is True and s["requests"] == 5  # UI sees demo when enabled
    finally:
        os.environ.pop("UI_DEMO", None)


def test_xss_content_is_escaped_client_side(ui_client) -> None:
    """Specs are untrusted LLM output: app.js must escape before rendering."""
    app_js = ui_client.get("/ui/app.js").text
    assert "function escapeHtml" in app_js
    # no raw innerHTML assignment of unescaped interpolations like ${...} outside escapeHtml
    import re

    raw = re.findall(r"innerHTML\s*=", app_js)
    assert raw  # innerHTML IS used, but every ${} interpolation goes through escapeHtml
    # every innerHTML line with interpolations must escape them
    for line in app_js.splitlines():
        if "innerHTML" in line and "${" in line:
            assert "escapeHtml(" in line or "escapeHtml" in app_js[max(0, app_js.find(line) - 400):], line


def test_seed_script_refuses_without_ui_demo_flag() -> None:
    import os

    os.environ.pop("UI_DEMO", None)
    from scripts.seed_demo_traffic import main

    assert main() == 2  # refuses without UI_DEMO=1
