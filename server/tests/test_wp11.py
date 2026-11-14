"""WP11: P1 default OFF in balanced (diagnosis-driven config change)."""
from __future__ import annotations

import pytest
from app.optimizer.chain import run_chain
from app.optimizer.policy import resolve_policy

pytestmark = pytest.mark.usefixtures("db_pool")


def test_p1_disabled_in_safe_and_balanced_by_default() -> None:
    for mode in ("safe", "balanced"):
        pol = resolve_policy(mode, None)
        assert not pol.allows("P1"), f"P1 must default OFF in {mode}"
    assert resolve_policy("aggressive", None).allows("P1")


def test_p1_still_runs_when_explicitly_enabled_in_aggressive(monkeypatch) -> None:
    pol = resolve_policy("aggressive", None)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "content": "out", "tool_call_id": "t1"},
    ]
    _, reports = run_chain({"messages": messages}, pol)
    p1 = next(r for r in reports if r["pass"] == "P1")
    assert p1["skipped"] == ""


def test_chain_preserves_tool_ordering_with_p1_off() -> None:
    """Without P1, tool messages stay directly after their assistant tool_calls."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "run_tests", "arguments": "{}"}}]},
        {"role": "tool", "content": "1 FAILED", "tool_call_id": "call_1"},
        {"role": "user", "content": "continue"},
    ]
    out, _ = run_chain({"messages": messages}, resolve_policy("balanced", None))
    msgs = out["messages"]
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            prev = msgs[i - 1]
            assert prev.get("role") == "assistant" and prev.get("tool_calls"), (
                f"tool message at {i} detached from its assistant"
            )
            assert m["tool_call_id"] in [tc.get("id") for tc in prev["tool_calls"]]


def test_p7_still_applies_in_safe(monkeypatch) -> None:
    """P7 (output controls) remains enabled: style line appears once, not duplicated."""
    from app.gateway import proxy_gateway
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        seen: dict = {}

        def fake(payload):  # type: ignore[no-untyped-def]
            seen["messages"] = payload["messages"]
            return {"id": "c1", "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

        monkeypatch.setattr(proxy_gateway, "raw_completion", fake)
        r = client.post(
            "/v1/chat/completions",
            json={"model": f"m-{__import__('uuid').uuid4().hex[:8]}",
                  "messages": [{"role": "user", "content": "hello"}]},
            headers={"x-optimizer-mode": "safe"},
        )
        assert r.status_code == 200
        systems = [m for m in seen["messages"] if m["role"] == "system"]
        assert len(systems) == 1
        assert "no preamble" in systems[0]["content"]
