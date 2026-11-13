"""WP1: Anthropic /v1/messages (FR-30), FR-37 invalidation matrix, metrics."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("db_pool")

ANTH_REQ = {
    "model": "m-anth",
    "max_tokens": 128,
    "system": "You are terse.",
    "messages": [{"role": "user", "content": [{"type": "text", "text": "say hi"}]}],
}


def _install(monkeypatch, responses):  # type: ignore[no-untyped-def]
    from app.gateway import proxy_gateway

    calls = {"n": 0}

    def fake_completion(payload):  # type: ignore[no-untyped-def]
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[idx]

    monkeypatch.setattr(proxy_gateway, "raw_completion", fake_completion)
    monkeypatch.setattr(proxy_gateway, "raw_usage", lambda r: (5, None))
    return calls


def test_anthropic_non_streaming_translation(app_client, monkeypatch) -> None:
    client, _ = app_client
    _install(monkeypatch, [{"id": "cmpl-a", "model": "m",
                            "choices": [{"message": {"content": "hi there"}}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}])
    r = client.post("/v1/messages", json=ANTH_REQ, headers={"x-optimizer-mode": "safe"})
    body = r.json()
    assert r.status_code == 200
    assert body["type"] == "message" and body["role"] == "assistant"
    assert body["content"][0]["text"] == "hi there"
    assert body["usage"]["input_tokens"] == 3 and body["usage"]["output_tokens"] == 2
    assert body["stop_reason"] == "end_turn"


def test_anthropic_cache_hit_flag(app_client, monkeypatch) -> None:
    client, _ = app_client
    resp = {"id": "cmpl-a", "model": "m", "choices": [{"message": {"content": "same"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    _install(monkeypatch, [resp])
    r1 = client.post("/v1/messages", json=ANTH_REQ, headers={"x-optimizer-mode": "safe"})
    r2 = client.post("/v1/messages", json=ANTH_REQ, headers={"x-optimizer-mode": "safe"})
    assert r1.json().get("cache_hit") is None  # first: miss
    assert r2.json()["cache_hit"] is True  # second: exact hit


def test_anthropic_streaming_emits_anthropic_events(app_client, monkeypatch) -> None:
    client, _ = app_client

    from app.gateway import proxy_gateway

    def fake_stream(payload):  # type: ignore[no-untyped-def]
        for text in ("hel", "lo"):
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"content": text}}]}
            ) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(proxy_gateway, "raw_stream", fake_stream)
    req = {**ANTH_REQ, "stream": True}
    with client.stream("POST", "/v1/messages", json=req,
                       headers={"x-optimizer-mode": "safe"}) as r:
        body = "".join(chunk.decode() for chunk in r.iter_bytes())[:4000]
    assert "event: message_start" in body
    assert "event: content_block_delta" in body
    assert "text_delta" in body
    assert "event: message_stop" in body


def test_anthropic_injected_pass_failure_passthrough(app_client, monkeypatch) -> None:
    """Fail-open on /v1/messages: broken chain still forwards the original."""
    client, _ = app_client
    import app.optimizer.chain as chain_mod
    from app.gateway import proxy_gateway

    def broken(request, pol):  # type: ignore[no-untyped-def]
        raise RuntimeError("injected chain failure")

    monkeypatch.setattr(chain_mod, "run_chain", broken)
    good = {"id": "cmpl-ok", "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    calls = {"n": 0}

    def fake_completion(payload):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return good

    monkeypatch.setattr(proxy_gateway, "raw_completion", fake_completion)
    r = client.post("/v1/messages", json=ANTH_REQ, headers={"x-optimizer-mode": "safe"})
    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "ok"
    assert calls["n"] == 1  # forwarded once, no error surfaced


def test_frozen_content_protection_header(app_client, monkeypatch) -> None:
    """x-optimizer-protect tagged content must not be rewritten by P4."""
    client, _ = app_client
    from app.gateway import proxy_gateway

    seen: dict = {}

    def fake_completion(payload):  # type: ignore[no-untyped-def]
        seen["messages"] = payload["messages"]
        return {"id": "c", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(proxy_gateway, "raw_completion", fake_completion)
    big_log = "\n".join(f"step {i}" for i in range(80)) + "\nERROR: x"
    r = client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "tool", "content": big_log}]},
        headers={"x-optimizer-mode": "safe", "x-optimizer-protect": "step 5"},
    )
    assert r.status_code == 200
    assert "step 79" in seen["messages"][0]["content"]  # NOT compacted: protected


def test_fr37_invalidation_on_model_version(app_client, monkeypatch) -> None:
    """Cache key includes the model: same messages on another model = miss."""
    client, _ = app_client
    _install(monkeypatch, [
        {"id": "cmpl-1", "choices": [{"message": {"content": "a"}}], "usage": {}},
        {"id": "cmpl-2", "choices": [{"message": {"content": "b"}}], "usage": {}},
    ])
    body_a = {"model": "m1", "messages": [{"role": "user", "content": "same"}]}
    body_b = {"model": "m2", "messages": [{"role": "user", "content": "same"}]}
    r1 = client.post("/v1/chat/completions", json=body_a, headers={"x-optimizer-mode": "safe"})
    r2 = client.post("/v1/chat/completions", json=body_b, headers={"x-optimizer-mode": "safe"})
    assert r1.json()["id"] == "cmpl-1"
    assert r2.json()["id"] == "cmpl-2"  # different model: no stale serve


def test_oversize_and_429_classified_as_infra(app_client, monkeypatch) -> None:
    """Rate limit (429) / oversize (413) from provider = infra_aborted, not a
    quality failure: the proxy surfaces a transport error, the ledger keeps the
    request out of quality metrics."""
    import httpx as _httpx
    from app.gateway import proxy_gateway

    client, _ = app_client
    ok = {"id": "c", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    calls = {"n": 0}

    def flaky(payload):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] <= 2:  # optimized AND fail-open attempts both 429
            raise _httpx.HTTPStatusError("429", request=None, response=None)  # type: ignore[arg-type]
        return ok

    monkeypatch.setattr(proxy_gateway, "raw_completion", flaky)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-optimizer-mode": "safe"},
    )
    assert r.status_code >= 500 or r.status_code in (429, 413)  # transport-level error
    body = r.json() if r.status_code != 429 else {}
    assert body.get("quality_fail") is not True  # never labeled a quality failure


def test_huge_payload_rejected_before_provider(app_client, monkeypatch) -> None:
    """Requests that would blow the 8K-token budget are rejected 413 without
    calling the provider (quota protection)."""
    client, _ = app_client
    from app.gateway import proxy_gateway

    called = {"n": 0}

    def fake_completion(payload):  # type: ignore[no-untyped-def]
        called["n"] += 1
        return {"id": "c", "choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(proxy_gateway, "raw_completion", fake_completion)
    huge = "x" * 200_000  # ~50K tokens: far over the 8K budget
    r = client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": huge}]},
        headers={"x-optimizer-mode": "safe"},
    )
    assert r.status_code == 413
    assert called["n"] == 0  # provider untouched
