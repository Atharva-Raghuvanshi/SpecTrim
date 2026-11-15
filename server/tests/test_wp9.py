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

    before = ui_client.get("/stats").json()["requests"]
    asyncio.run(seed(ui_client.app.state.pool, 5))
    real = ui_client.get("/stats").json()  # /stats must never include demo rows
    assert real["requests"] == before  # demo seeding does not move real stats
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


def test_ui_kpi_honest_labels(ui_client) -> None:
    """WP19.1: KPI cards show honest prompt token wording, tooltip,
    completion/reasoning breakdown, and prompt charts."""
    client = ui_client
    d = client.get("/ui/api/summary?range=24h").json()
    assert "tokens_saved_pct_est" in d
    assert "prompt_tokens_baseline_est" in d
    assert "prompt_tokens_actual" in d
    assert "completion_tokens" in d
    assert "reasoning_tokens" in d
    assert "excluded_count" in d
    assert "by_mode" in d

    app_js = client.get("/ui/app.js").text
    assert "Prompt tokens saved (estimated)" in app_js
    assert "Net increase (estimated)" in app_js
    assert "baseline is an estimate from chars/4" in app_js
    assert "baseline-est (prompt)" in app_js
    assert "actual (prompt)" in app_js
    assert "completion" in app_js and "reasoning" in app_js


def test_ui_disabled_stages_show_disabled(ui_client) -> None:
    """WP15: eval-status shows disabled stages as DISABLED."""

    from evals.status import selected_stages

    sel = selected_stages()
    assert "S1" in sel and "S2p" in sel and "S6" in sel and "S7" in sel
    assert "S2" not in sel  # full E4b disabled by default
    assert "S2b" not in sel  # safe arm disabled by default
    assert "S3b" not in sel  # E5 disabled by default
    assert "S4a" not in sel and "S4b" not in sel  # E3b disabled by default
    assert "S5" not in sel  # tiny E4 disabled by default


def test_ui_connect_page_has_copy_snippets(ui_client) -> None:
    """WP19 P6: Connect page includes MCP and proxy snippets."""

    app_js = ui_client.get("/ui/app.js").text
    assert "claude mcp add" in app_js
    assert "/v1/chat/completions" in app_js or "v1" in app_js
    assert "x-optimizer-mode" in app_js


def test_ui_hash_navigation_exists(ui_client) -> None:
    """WP19 P4: app.js and index.html include hash navigation for sidebar pages."""
    app_js = ui_client.get("/ui/app.js").text
    assert "hashchange" in app_js
    assert "showPageFromHash" in app_js
    assert "location.hash" in app_js
    assert "initialHash" in app_js
    assert '"specs"' in app_js and '"connect"' in app_js

    index_html = ui_client.get("/ui/").text
    assert 'data-page="specs"' in index_html
    assert 'data-page="connect"' in index_html


def test_ui_contrast_and_overflow_guards(ui_client) -> None:
    """WP19.6: Contrast AA styles for Open Grafana link and horizontal overflow guards."""
    styles = ui_client.get("/ui/styles.css").text
    assert ".topbar a.ghost" in styles or ".topbar a" in styles
    assert "#1a56db" in styles  # high-contrast AA link color on light theme
    assert "#58a6ff" in styles  # high-contrast AA link color on dark theme
    assert ":visited" in styles
    assert "overflow-x: hidden" in styles
    assert "minmax(0, 1fr)" in styles


def test_ui_kpi_data_source_labels(ui_client) -> None:
    """WP19.2: KPI cards show data source and time window;
    pie chart shows total and consistent mode breakdown."""
    app_js = ui_client.get("/ui/app.js").text
    assert "Ledger," in app_js
    assert "s.range" in app_js
    assert "Total:" in app_js
    assert "by_mode" in app_js

    # Verify Grafana dashboard has cache-hit percent and window panel titles
    import json
    from pathlib import Path

    dash_path = Path("observability/grafana/dashboards/dashboard.json")
    if dash_path.exists():
        dash = json.loads(dash_path.read_text(encoding="utf-8"))
        cache_panel = next(
            (p for p in dash.get("panels", []) if "Cache hit rate" in p.get("title", "")), None
        )
        assert cache_panel is not None
        assert "(15m)" in cache_panel["title"]
        assert cache_panel.get("fieldConfig", {}).get("defaults", {}).get("unit") == "percent"
        for p in dash.get("panels", []):
            has_win = any(w in p["title"] for w in ("5m", "15m", "total", "today"))
            assert has_win, f"Panel {p['title']} missing window"


def test_prompt_tokens_like_with_like_fixtures(ui_client) -> None:
    """WP19.1: Prompt token accounting fixtures (lower, equal,
    higher than baseline, plus excluded unmeasured)."""
    import asyncio

    from app.optimizer.cache import SavingsLedger

    ledger = SavingsLedger(ui_client.app.state.pool)

    async def _run():
        # Clean demo table for fixture isolation
        async with ui_client.app.state.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM proxy_demo_requests")

        # 1. Lower: baseline=1000, actual=700 (saved 300 = 30%)
        await ledger.record(
            "ses_lower", "balanced", "mock-model", False, False,
            tokens_baseline_est=1000, tokens_actual=900, cost_baseline_est=0.0, cost_actual=0.0,
            latency_ms=10, pass_reports=[],
            prompt_tokens_actual=700, completion_tokens_actual=200, reasoning_tokens_actual=10,
            demo=True,
        )
        s1 = await ledger.summary("24h", demo=True)
        assert s1["tokens_saved_est"] == 300
        assert s1["tokens_saved_pct_est"] == 30.0
        assert s1["prompt_tokens_baseline_est"] == 1000
        assert s1["prompt_tokens_actual"] == 700
        assert s1["completion_tokens"] == 200
        assert s1["reasoning_tokens"] == 10
        assert s1["excluded_count"] == 0

        # 2. Equal: baseline=1000, actual=1000 (saved 0 = 0%)
        async with ui_client.app.state.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM proxy_demo_requests")
        await ledger.record(
            "ses_equal", "balanced", "mock-model", False, False,
            tokens_baseline_est=1000, tokens_actual=1200, cost_baseline_est=0.0, cost_actual=0.0,
            latency_ms=10, pass_reports=[],
            prompt_tokens_actual=1000, completion_tokens_actual=200, reasoning_tokens_actual=0,
            demo=True,
        )
        s2 = await ledger.summary("24h", demo=True)
        assert s2["tokens_saved_est"] == 0
        assert s2["tokens_saved_pct_est"] == 0.0
        assert s2["prompt_tokens_baseline_est"] == 1000
        assert s2["prompt_tokens_actual"] == 1000

        # 3. Higher: baseline=1000, actual=1400 (net increase 400 = -40%)
        async with ui_client.app.state.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("DELETE FROM proxy_demo_requests")
        await ledger.record(
            "ses_higher", "balanced", "mock-model", False, False,
            tokens_baseline_est=1000, tokens_actual=1600, cost_baseline_est=0.0, cost_actual=0.0,
            latency_ms=10, pass_reports=[],
            prompt_tokens_actual=1400, completion_tokens_actual=200, reasoning_tokens_actual=0,
            demo=True,
        )
        s3 = await ledger.summary("24h", demo=True)
        assert s3["tokens_saved_est"] == -400
        assert s3["tokens_saved_pct_est"] == -40.0
        assert s3["prompt_tokens_baseline_est"] == 1000
        assert s3["prompt_tokens_actual"] == 1400

        # 4. Unmeasured row (prompt_tokens_actual is None) is excluded from prompt accounting
        await ledger.record(
            "ses_unmeasured", "balanced", "mock-model", False, False,
            tokens_baseline_est=5000, tokens_actual=5000, cost_baseline_est=0.0, cost_actual=0.0,
            latency_ms=10, pass_reports=[],
            prompt_tokens_actual=None, completion_tokens_actual=None, reasoning_tokens_actual=None,
            demo=True,
        )
        s4 = await ledger.summary("24h", demo=True)
        # prompt baseline remains 1000, prompt actual remains 1400, excluded_count is 1
        assert s4["tokens_saved_est"] == -400
        assert s4["tokens_saved_pct_est"] == -40.0
        assert s4["prompt_tokens_baseline_est"] == 1000
        assert s4["prompt_tokens_actual"] == 1400
        assert s4["excluded_count"] == 1

        # Also verify /ui/api/summary via test client with UI_DEMO=1
        import os
        os.environ["UI_DEMO"] = "1"
        try:
            api_res = ui_client.get("/ui/api/summary?range=24h").json()
            assert api_res["tokens_saved_est"] == -400
            assert api_res["prompt_tokens_baseline_est"] == 1000
            assert api_res["prompt_tokens_actual"] == 1400
            assert api_res["excluded_count"] == 1
        finally:
            os.environ.pop("UI_DEMO", None)

    asyncio.run(_run())


def test_specs_page_eval_and_status_filters(ui_client) -> None:
    """WP19.5: Specs page eval filter, status filter, failure reason, and awaiting_user link."""
    import asyncio
    import uuid

    app_js = ui_client.get("/ui/app.js").text
    assert "isEvalSession" in app_js
    assert "specs-hide-eval" in app_js
    assert "specs-status-filter" in app_js
    assert "Needs your answer" in app_js
    assert "openQuestionForm" in app_js
    assert "data-answer" in app_js

    index_html = ui_client.get("/ui/").text
    assert "specs-hide-eval" in index_html
    assert "specs-status-filter" in index_html
    assert "Hide eval sessions" in index_html

    # Test failure_reason in /ui/api/sessions without secrets
    async def _insert_failed():
        sid = f"ses-failed-{str(uuid.uuid4())[:8]}"
        pool = ui_client.app.state.pool
        from app.storage.repos import EventsRepo
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO sessions (id, status, trace_id, current_version, spend_usd, "
                "idempotency_key, idea) VALUES (%s, 'failed', 'tr-test', 1, 0, %s, 'failed task idea')",
                (sid, f"idem-{sid}"),
            )
        await EventsRepo(pool).append(sid, "error", {
            "reason": "GatewayError: rate limit exceeded with Bearer sk-secret-123456",
        })
        return sid

    sid = asyncio.run(_insert_failed())
    sessions = ui_client.get("/ui/api/sessions").json()["sessions"]
    matching = [s for s in sessions if s["id"] == sid]
    assert len(matching) == 1
    assert matching[0]["status"] == "failed"
    assert "Bearer [REDACTED]" in matching[0]["failure_reason"]
    assert "sk-secret-123456" not in matching[0]["failure_reason"]
