"""Proxy API (FR-30/31/33/44/46): OpenAI + Anthropic compatible endpoints.

Shared core: policy -> P9 cache -> chain -> provider call -> ledger, with
fail-open at both pass level and router level, and latency/counter metrics.
Fail-open rule (PRD v2 §6.1): any optimizer failure forwards the original
request unchanged and counts as a passthrough — never an error to the client.
"""
from __future__ import annotations

import json
import time

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.gateway import proxy_gateway
from app.observability import (
    PASS_SAVINGS_EST,
    PROXY_CACHE_HITS,
    PROXY_INVALID_AFTER_PASS,
    PROXY_INVALID_ORIGINAL,
    PROXY_OVERHEAD,
    PROXY_PASSTHROUGH,
    PROXY_REQUESTS,
)
from app.optimizer.anthropic import from_anthropic, to_anthropic, translate_stream_to_anthropic
from app.optimizer.cache import ResponseCache, SavingsLedger
from app.optimizer.chain import run_chain
from app.optimizer.passes import _est_tokens
from app.optimizer.policy import PROTECTED_HEADER, resolve_policy
from app.security import require_bearer

DEFAULT_MODEL = "groq/openai/gpt-oss-20b"


def _record_savings(reports: list[dict]) -> None:
    for r in reports:
        saved = r.get("tokens_before_est", 0) - r.get("tokens_after_est", 0)
        if saved > 0:
            PASS_SAVINGS_EST.labels(**{"pass": r["pass"]}).inc(saved)


async def _handle_chat(request: Request, api: str) -> Response:
    require_bearer(request)  # sync dependency; direct call
    time.perf_counter()
    raw_body = await request.json()
    pool = request.app.state.pool
    cache = ResponseCache(pool)
    ledger = SavingsLedger(pool)
    mode = request.headers.get("x-optimizer-mode")
    policy = resolve_policy(mode, request.headers.get(PROTECTED_HEADER))
    endpoint = "/v1/messages" if api == "anthropic" else "/v1/chat/completions"
    PROXY_REQUESTS.labels(endpoint=endpoint, mode=policy.mode).inc()

    if api == "anthropic":
        body = from_anthropic(raw_body)
    else:
        body = dict(raw_body)
    model = str(body.get("model") or DEFAULT_MODEL)
    session_id = request.headers.get("x-session-id")
    tokens_baseline_est = _est_tokens(json.dumps(body.get("messages", [])))
    from app.optimizer.validate import validate_internal as _validate

    if _validate(body):
        PROXY_INVALID_ORIGINAL.inc()
    # Quota guard: reject payloads over the per-request token budget before any
    # provider call (413 = infra, never a quality failure).
    import os as _os

    budget = int(_os.environ.get("QO_MAX_REQUEST_TOKENS", "8000"))
    if tokens_baseline_est + int(body.get("max_tokens") or 0) > budget * 1.5:
        PROXY_PASSTHROUGH.labels(reason="oversize_rejected").inc()
        return JSONResponse(
            {"error": {"type": "oversize_request",
                       "message": f"~{tokens_baseline_est} est tokens exceeds budget {budget}"}},
            status_code=413,
        )

    # ---- mode off: passthrough + metrics only (PRD v2 §4)
    if policy.mode == "off":
        payload = {**body, "model": model}
        if body.get("stream"):
            return StreamingResponse(
                proxy_gateway.raw_stream(payload), media_type="text/event-stream"
            )
        resp = proxy_gateway.raw_completion(payload)
        tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
        usage_info = proxy_gateway.raw_usage_breakdown(resp)
        await ledger.record(
            session_id, "off", model, False, True, tokens_baseline_est,
            tokens_actual, 0.0, cost_actual, 0, [],
            prompt_tokens_actual=usage_info.get("prompt_tokens"),
            completion_tokens_actual=usage_info.get("completion_tokens"),
            reasoning_tokens_actual=usage_info.get("reasoning_tokens"),
        )
        return _reply(api, resp)

    # ---- added-overhead measurement (policy+cache+chain, excludes provider)
    t_overhead = time.perf_counter()
    # ---- P9 cache lookup (non-streaming; exact match, version-aware)
    t_cache = time.perf_counter()
    cache_hit = None
    if "P9" in policy.enabled and not body.get("stream"):
        cache_hit = await cache.get(model, body)
    PROXY_OVERHEAD.observe(time.perf_counter() - t_cache)

    if cache_hit is not None:
        PROXY_CACHE_HITS.inc()
        tokens_actual, cost_actual = proxy_gateway.raw_usage(cache_hit)
        usage_info = proxy_gateway.raw_usage_breakdown(cache_hit)
        await ledger.record(
            session_id, policy.mode, model, True, False,
            tokens_baseline_est, tokens_actual, 0.0, cost_actual, 0, [],
            prompt_tokens_actual=usage_info.get("prompt_tokens"),
            completion_tokens_actual=usage_info.get("completion_tokens"),
            reasoning_tokens_actual=usage_info.get("reasoning_tokens"),
        )
        out = _reply(api, cache_hit, cache_hit_flag=True)
        return out

    # ---- chain (fail-open inside; reports always)
    t_chain = time.perf_counter()
    optimized, reports = run_chain(body, policy)
    PROXY_OVERHEAD.observe(time.perf_counter() - t_chain)
    overhead_ms = int((time.perf_counter() - t_overhead) * 1000)
    broken = next((r["pass"] for r in reports if r.get("invalid_after_pass")), None)
    if broken is not None:
        PROXY_INVALID_AFTER_PASS.labels(**{"pass": broken}).inc()
        # WP12 validity guard: a pass broke the request -> forward the ORIGINAL
        PROXY_PASSTHROUGH.labels(reason=f"invalid_after_pass:{broken}").inc()
        if body.get("stream"):
            return StreamingResponse(
                proxy_gateway.raw_stream({**body, "model": model}),
                media_type="text/event-stream",
            )
        resp = proxy_gateway.raw_completion({**body, "model": model})
        tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
        usage_info = proxy_gateway.raw_usage_breakdown(resp)
        await ledger.record(
            session_id, policy.mode, model, False, True,
            tokens_baseline_est, tokens_actual, 0.0, cost_actual,
            overhead_ms, reports,
            prompt_tokens_actual=usage_info.get("prompt_tokens"),
            completion_tokens_actual=usage_info.get("completion_tokens"),
            reasoning_tokens_actual=usage_info.get("reasoning_tokens"),
        )
        return _reply(api, resp)
    _record_savings(reports)
    payload = {**optimized, "model": model}

    if body.get("stream"):
        openai_sse = proxy_gateway.raw_stream(payload)
        if api == "anthropic":
            return StreamingResponse(
                translate_stream_to_anthropic(openai_sse), media_type="text/event-stream"
            )
        return StreamingResponse(openai_sse, media_type="text/event-stream")

    # ---- provider call with router-level fail-open
    try:
        resp = proxy_gateway.raw_completion(payload)
    except Exception:  # noqa: BLE001 - forward the ORIGINAL unchanged, count passthrough
        PROXY_PASSTHROUGH.labels(reason="optimized_call_failed").inc()
        try:
            resp = proxy_gateway.raw_completion({**body, "model": model})
        except Exception as exc:  # noqa: BLE001 - transport error: infra, never quality
            return JSONResponse(
                {"error": {"type": "infra_error", "message": f"{type(exc).__name__}: {exc}"[:200]}},
                status_code=502,
            )
        tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
        usage_info = proxy_gateway.raw_usage_breakdown(resp)
        await ledger.record(
            session_id, policy.mode, model, False, True,
            tokens_baseline_est, tokens_actual, 0.0, cost_actual,
            overhead_ms, reports,
            prompt_tokens_actual=usage_info.get("prompt_tokens"),
            completion_tokens_actual=usage_info.get("completion_tokens"),
            reasoning_tokens_actual=usage_info.get("reasoning_tokens"),
        )
        return _reply(api, resp)

    tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
    usage_info = proxy_gateway.raw_usage_breakdown(resp)
    if "P9" in policy.enabled:
        await cache.store(model, body, resp)
    await ledger.record(
        session_id, policy.mode, model, False, False,
        tokens_baseline_est, tokens_actual, 0.0, cost_actual,
        overhead_ms, reports,
        prompt_tokens_actual=usage_info.get("prompt_tokens"),
        completion_tokens_actual=usage_info.get("completion_tokens"),
        reasoning_tokens_actual=usage_info.get("reasoning_tokens"),
    )
    return _reply(api, resp)


def _reply(api: str, resp: dict, cache_hit_flag: bool = False) -> Response:
    if api == "anthropic":
        return JSONResponse(to_anthropic(resp, cache_hit=cache_hit_flag))
    return JSONResponse({**resp, **({"cache_hit": True} if cache_hit_flag else {})})


def build_proxy_router(app):  # type: ignore[no-untyped-def]
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> Response:
        return await _handle_chat(request, api="openai")

    @router.post("/v1/messages", response_model=None)
    async def messages(request: Request) -> Response:
        return await _handle_chat(request, api="anthropic")

    @router.post("/v1/optimize", response_model=None)
    async def optimize_dry_run(request: Request) -> JSONResponse:
        require_bearer(request)  # sync dependency; direct call
        body = await request.json()
        policy = resolve_policy(
            request.headers.get("x-optimizer-mode"), request.headers.get(PROTECTED_HEADER)
        )
        optimized, reports = run_chain(body, policy)
        return JSONResponse({"request": optimized, "reports": reports, "mode": policy.mode})

    @router.get("/stats")
    async def stats(request: Request) -> JSONResponse:
        ledger = SavingsLedger(request.app.state.pool)
        return JSONResponse(await ledger.stats())

    return router
