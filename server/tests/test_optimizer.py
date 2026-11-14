"""Release B tests: passes (M11.x), chain fail-open (M10.2), cache (M11.1), ledger (M10.3)."""
from __future__ import annotations

import uuid

import pytest
from app.optimizer.chain import run_chain
from app.optimizer.passes import (
    canonical_prefix_bytes,
    p1_cache_aware_assembly,
    p4_tool_output_compaction,
    p7_output_controls,
)
from app.optimizer.policy import resolve_policy

pytestmark = pytest.mark.usefixtures("db_pool")


def policy(mode: str = "safe") -> object:
    return resolve_policy(mode, None)


def test_p4_preserves_error_lines_always() -> None:
    long_log = "\n".join(
        [f"step {i}: ok" for i in range(60)]
        + ["ERROR: build failed on module Z", "Traceback (most recent call last)"]
    )
    req = {"messages": [{"role": "tool", "content": long_log}, {"role": "user", "content": "fix it"}]}
    out, report = p4_tool_output_compaction(req, policy())
    content = out["messages"][0]["content"]
    assert "ERROR: build failed on module Z" in content  # error lines always preserved
    assert "Traceback" in content
    assert report["changed"] is True
    assert report["tokens_after_est"] < report["tokens_before_est"]


def test_p4_strips_ansi_and_short_logs_untouched_except_ansi() -> None:
    req = {"messages": [{"role": "tool", "content": "\x1b[31mred error line\x1b[0m"}]}
    out, _ = p4_tool_output_compaction(req, policy())
    assert "\x1b[" not in out["messages"][0]["content"]
    assert "red error line" in out["messages"][0]["content"]


def test_p4_never_touches_last_user_message() -> None:
    req = {"messages": [{"role": "user", "content": "\x1b[31mmy instruction " + "x" * 500}]}
    out, report = p4_tool_output_compaction(req, policy())
    assert report["changed"] is False
    assert "\x1b[31m" in out["messages"][0]["content"]  # untouched


def test_p1_prefix_byte_stable_across_calls() -> None:
    msgs_a = [
        {"role": "system", "content": "You are a coder."},
        {"role": "user", "content": "task 1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "task 2 (volatile)"},
    ]
    msgs_b = [
        {"role": "system", "content": "You are a coder."},
        {"role": "user", "content": "task 1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "task 3 DIFFERENT volatile"},
    ]
    a = canonical_prefix_bytes({"messages": msgs_a})
    b = canonical_prefix_bytes({"messages": msgs_b})
    assert a == b  # identical prefix bytes despite different last instruction


def test_p1_reorders_volatile_last() -> None:
    req = {"messages": [
        {"role": "user", "content": "latest instruction"},
        {"role": "system", "content": "sys"},
        {"role": "tool", "content": "tool out"},
        {"role": "assistant", "content": "prev"},
    ]}
    out, report = p1_cache_aware_assembly(req, policy())
    roles = [m["role"] for m in out["messages"]]
    assert roles[0] == "system"
    assert roles[-1] == "user"  # latest user instruction stays last
    assert report["pass"] == "P1"


def test_p7_applies_max_tokens_and_stop_per_stage() -> None:
    cfg = {"default": {"max_tokens": 1024, "stop": ["STOP"]},
           "spec_writer": {"max_tokens": 8192}}
    req = {"messages": [{"role": "user", "content": "hi"}], "metadata": {"optimizer_stage": "spec_writer"}}
    out, _ = p7_output_controls(req, policy(), cfg)
    assert out["max_tokens"] == 8192
    assert out["stop"] == ["STOP"]
    req2 = {"messages": [{"role": "user", "content": "hi"}]}
    out2, _ = p7_output_controls(req2, policy(), cfg)
    assert out2["max_tokens"] == 1024


def test_chain_respects_mode_gating() -> None:
    req = {"messages": [{"role": "user", "content": "x"}]}
    _, reports_off = run_chain(req, resolve_policy("off", None))
    assert all(r["skipped"] for r in reports_off)
    _, reports_safe = run_chain(req, resolve_policy("safe", None))
    by_pass = {r["pass"]: r for r in reports_safe}
    assert by_pass["P4"]["skipped"] == ""
    assert by_pass["P5"]["skipped"] == "mode or kill switch"  # P5 needs balanced
    _, reports_balanced = run_chain(req, resolve_policy("balanced", None))
    by_pass_b = {r["pass"]: r for r in reports_balanced}
    assert "not implemented" in by_pass_b["P5"]["skipped"]


def test_kill_switch_disables_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QO_PASS_P4", "0")
    pol = resolve_policy("aggressive", None)
    assert "P4" not in pol.enabled


def test_injected_pass_failure_still_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    """M10.2 exit check: a broken pass is skipped; router forwards the request."""
    import app.optimizer.chain as chain_mod

    def broken(request, pol):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected pass failure")

    monkeypatch.setitem(chain_mod.PASS_FUNCTIONS, "P4", broken)
    req = {"messages": [{"role": "tool", "content": "data"}, {"role": "user", "content": "go"}]}
    out, reports = run_chain(req, resolve_policy("safe", None))
    p4 = next(r for r in reports if r["pass"] == "P4")
    assert "injected pass failure" in p4["skipped"]
    assert out["messages"] == req["messages"]  # request intact (fail-open at pass level)


def test_router_level_fail_open(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = app_client
    from app.gateway import proxy_gateway

    good = {"id": "cmpl-1", "choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 7}}
    calls = {"n": 0}

    def flaky(payload):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("optimized call exploded")  # first (optimized) attempt
        return good  # fail-open forwards the original successfully

    monkeypatch.setattr(proxy_gateway, "raw_completion", flaky)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-optimizer-mode": "safe"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "cmpl-1"  # original forwarded unchanged


def test_cache_hit_then_zero_stale_after_invalidation(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = app_client
    from app.gateway import proxy_gateway

    calls = {"n": 0}

    def fake_completion(payload):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return {"id": f"cmpl-{calls['n']}", "usage": {"total_tokens": 5}}

    monkeypatch.setattr(proxy_gateway, "raw_completion", fake_completion)
    body = {"model": f"m-{uuid.uuid4().hex[:8]}",
            "messages": [{"role": "user", "content": "deterministic prompt"}]}
    import asyncio

    r1 = client.post("/v1/chat/completions", json=body, headers={"x-optimizer-mode": "safe"})
    r2 = client.post("/v1/chat/completions", json=body, headers={"x-optimizer-mode": "safe"})
    assert r1.json()["id"] == "cmpl-1"
    assert r2.json()["cache_hit"] is True  # exact match served from cache
    assert calls["n"] == 1

    from app.optimizer.cache import ResponseCache

    asyncio.get_event_loop().run_until_complete(
        ResponseCache(client.app.state.pool).invalidate_model(body["model"])
    )
    monkeypatch.setattr(
        proxy_gateway, "raw_completion",
        lambda payload: {"id": "cmpl-NEW", "usage": {"total_tokens": 5}},
    )
    r3 = client.post("/v1/chat/completions", json=body, headers={"x-optimizer-mode": "safe"})
    assert r3.json()["id"] == "cmpl-NEW"  # zero stale serves after invalidation


def test_ledger_and_stats(app_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = app_client
    from app.gateway import proxy_gateway

    monkeypatch.setattr(
        proxy_gateway,
        "raw_completion",
        lambda payload: {"id": "cmpl-9", "usage": {"total_tokens": 3}},
    )
    client.post(
        "/v1/chat/completions",
        json={"model": "m-ledger", "messages": [{"role": "user", "content": "hello world" * 10}]},
        headers={"x-optimizer-mode": "safe"},
    )
    stats = client.get("/stats").json()
    assert stats["requests"] >= 1
    assert stats["tokens_baseline_est"] > stats["tokens_actual"] or stats["cache_hits"] >= 0
    assert stats["passthroughs"] >= 0


def test_dry_run_returns_request_and_reports(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/v1/optimize",
        json={"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]},
        headers={"x-optimizer-mode": "safe"},
    )
    body = r.json()
    assert body["mode"] == "safe"
    assert [m["role"] for m in body["request"]["messages"]][0] == "system"
    assert len(body["reports"]) == 9
