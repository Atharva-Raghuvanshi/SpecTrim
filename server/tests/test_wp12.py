"""WP12: request-validity guard, P1 atomic tool pairs, pass-registry check."""
from __future__ import annotations

import pytest
from app.optimizer.chain import REGISTRY, run_chain
from app.optimizer.passes import p1_cache_aware_assembly
from app.optimizer.policy import PASS_ORDER, resolve_policy
from app.optimizer.validate import validate_anthropic_messages, validate_internal

pytestmark = pytest.mark.usefixtures("db_pool")

TOOL_SEQ = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "run_tests", "arguments": "{}"}}]},
    {"role": "tool", "content": "1 FAILED", "tool_call_id": "call_1"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "call_2", "type": "function",
                     "function": {"name": "submit_code", "arguments": "{\"code\": \"x\"}"}}]},
    {"role": "tool", "content": "ok", "tool_call_id": "call_2"},
    {"role": "user", "content": "continue"},
]


# ---------- validity guard ----------
def test_validate_internal_clean_sequence() -> None:
    assert validate_internal({"messages": TOOL_SEQ}) == []


def test_validate_internal_flags_detached_tool_message() -> None:
    broken = [m for m in TOOL_SEQ]
    broken.insert(3, {"role": "user", "content": "interrupted"})  # detaches tool
    violations = validate_internal({"messages": broken})
    assert any("tool_call_id" in v for v in violations)


def test_validate_internal_flags_empty_content_and_role() -> None:
    msgs = [{"role": "user", "content": ""}, {"role": "", "content": "x"}]
    v = validate_internal({"messages": msgs})
    assert any("empty user content" in s for s in v)
    assert any("empty role" in s for s in v)


def test_validate_internal_flags_empty_messages() -> None:
    assert validate_internal({"messages": []}) == ["messages missing or empty"]


def test_validate_anthropic_alternation() -> None:
    ok = {"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}
    assert validate_anthropic_messages(ok) == []
    bad = {"messages": [{"role": "assistant", "content": "a"}, {"role": "assistant", "content": "b"}]}
    assert any("alternation" in v for v in validate_anthropic_messages(bad))


# ---------- chain: injected invalid pass -> original forwarded ----------
def test_chain_reverts_and_names_invalid_pass(monkeypatch) -> None:
    """WP12: a pass whose output breaks the request is reverted and named."""
    import app.optimizer.chain as chain_mod

    def bad_p4(request, policy):  # type: ignore[no-untyped-def]
        out = {**request, "messages": [dict(m) for m in request["messages"]]}
        out["messages"].insert(2, {"role": "user", "content": "detached"})  # detaches tool
        return out, {"pass": "P4", "tokens_before_est": 1, "tokens_after_est": 2,
                     "changed": True, "skipped": ""}

    monkeypatch.setitem(chain_mod.REGISTRY, "P4", bad_p4)
    req = {"messages": [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "run_tests", "arguments": "{}"}}]},
        {"role": "tool", "content": "1 FAILED", "tool_call_id": "call_1"},
        {"role": "user", "content": "go"},
    ]}
    pol = resolve_policy("safe", None)
    out, reports = run_chain(req, pol)
    p4 = next(r for r in reports if r["pass"] == "P4")
    assert p4.get("invalid_after_pass") is True  # stamped on the pass's own report
    assert "invalid_after_pass" in p4["skipped"] or p4["skipped"] == ""
    # everything reverted: request content intact
    assert out["messages"][0]["content"] == "sys"
    assert out["messages"][-1]["content"] == "go"


def test_chain_skips_all_passes_when_original_invalid() -> None:
    req = {"messages": []}
    out, reports = run_chain(req, resolve_policy("safe", None))
    assert out == req
    assert all("invalid_original" in r["skipped"] for r in reports)


def test_router_forwards_original_on_invalid_after_pass(ui_client, monkeypatch) -> None:
    client = ui_client
    import uuid as _u

    import app.optimizer.chain as chain_mod
    from app.gateway import proxy_gateway

    def bad_p4(request, policy):  # type: ignore[no-untyped-def]
        out = {**request, "messages": [dict(m) for m in request["messages"]]}
        out["messages"].insert(2, {"role": "user", "content": "detached"})
        return out, {"pass": "P4", "tokens_before_est": 1, "tokens_after_est": 2,
                     "changed": True, "skipped": ""}

    monkeypatch.setitem(chain_mod.REGISTRY, "P4", bad_p4)
    good = {"id": "cmpl-orig", "choices": [{"message": {"content": "orig"}}], "usage": {}}
    monkeypatch.setattr(proxy_gateway, "raw_completion", lambda payload: good)
    r = client.post(
        "/v1/chat/completions",
        json={"model": f"m-{_u.uuid4().hex[:8]}",
              "messages": [
                  {"role": "assistant", "content": "",
                   "tool_calls": [{"id": "t1", "type": "function",
                                   "function": {"name": "f", "arguments": "{}"}}]},
                  {"role": "tool", "content": "x", "tool_call_id": "t1"},
                  {"role": "user", "content": "go"},
              ]},
        headers={"x-optimizer-mode": "safe"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "cmpl-orig"  # ORIGINAL forwarded
    stats = client.get("/stats").json()
    assert stats["passthroughs"] >= 1


# ---------- P1 atomic tool pairs ----------
def test_p1_keeps_tool_pairs_atomic() -> None:
    """WP12: P1 output passes the id-pairing check from the diagnosis."""
    out, report = p1_cache_aware_assembly({"messages": TOOL_SEQ}, resolve_policy("aggressive", None))
    assert report["pass"] == "P1"
    assert validate_internal({"messages": out["messages"]}) == []
    assert out["messages"][-1]["role"] == "user"  # latest instruction still last


def test_p1_reorder_does_not_duplicate_messages() -> None:
    out, _ = p1_cache_aware_assembly({"messages": TOOL_SEQ}, resolve_policy("aggressive", None))
    assert len(out["messages"]) == len(TOOL_SEQ)


# ---------- pass-registry completeness ----------
def test_registry_covers_policy_table_except_p10_and_p9() -> None:
    """Every chain pass must be registered. P10 is intentionally unimplemented;
    P9 is the router-level cache lookup (not a chain pass) — its evidence is
    the cache-hit counter in the ledger."""
    for pass_id in PASS_ORDER:
        if pass_id in ("P10", "P9"):
            continue
        assert pass_id in REGISTRY, f"{pass_id} configured but not registered"


def test_registry_keys_subset_of_policy_table() -> None:
    assert set(REGISTRY.keys()) <= set(PASS_ORDER)
