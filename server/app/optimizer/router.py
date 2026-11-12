"""Proxy API (M10.x): OpenAI-compatible endpoint, dry run, stats.

Fail-open rule (PRD v2 §6.1): any optimizer failure forwards the original
request unchanged and counts in the ledger as a passthrough.
"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.gateway import proxy_gateway
from app.optimizer.cache import ResponseCache, SavingsLedger
from app.optimizer.chain import run_chain
from app.optimizer.passes import _est_tokens, p1_cache_aware_assembly
from app.optimizer.policy import PROTECTED_HEADER, resolve_policy
from app.security import require_bearer

DEFAULT_MODEL = "groq/openai/gpt-oss-20b"


async def _handle_chat(request: Request) -> JSONResponse | StreamingResponse:
    require_bearer(request)  # sync dependency; direct call
    body = await request.json()
    pool = request.app.state.pool
    cache = ResponseCache(pool)
    ledger = SavingsLedger(pool)
    mode = request.headers.get("x-optimizer-mode")
    policy = resolve_policy(mode, request.headers.get(PROTECTED_HEADER))
    model = str(body.get("model") or DEFAULT_MODEL)
    session_id = request.headers.get("x-session-id")
    tokens_baseline_est = _est_tokens(json.dumps(body.get("messages", [])))

    # P1 even in off-mode? No: off = passthrough + metrics only (PRD v2 §4)
    if policy.mode == "off":
        if body.get("stream"):
            return StreamingResponse(
                proxy_gateway.raw_stream({**body, "model": model}), media_type="text/event-stream"
            )
        resp = proxy_gateway.raw_completion({**body, "model": model})
        tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
        await ledger.record(session_id, "off", model, False, True, tokens_baseline_est,
                            tokens_actual, 0.0, cost_actual, 0, [])
        return JSONResponse(resp)

    # P9 cache lookup (non-streaming only; exact match)
    if "P9" in policy.enabled and not body.get("stream"):
        hit = await cache.get(model, body)
        if hit is not None:
            tokens_actual, cost_actual = proxy_gateway.raw_usage(hit)
            await ledger.record(session_id, policy.mode, model, True, False,
                                tokens_baseline_est, tokens_actual, 0.0, cost_actual, 0, [])
            return JSONResponse({**hit, "cache_hit": True})

    optimized, reports = run_chain(body, policy)
    if not body.get("stream"):
        optimized, _ = p1_cache_aware_assembly(optimized, policy)
    payload = {**optimized, "model": model}

    if body.get("stream"):
        return StreamingResponse(
            proxy_gateway.raw_stream(payload), media_type="text/event-stream"
        )

    try:
        resp = proxy_gateway.raw_completion(payload)
    except Exception:  # noqa: BLE001 - fail-open: forward the original unchanged
        resp = proxy_gateway.raw_completion({**body, "model": model})
        tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
        await ledger.record(session_id, policy.mode, model, False, True,
                            tokens_baseline_est, tokens_actual, 0.0, cost_actual, 0, reports)
        return JSONResponse(resp)

    tokens_actual, cost_actual = proxy_gateway.raw_usage(resp)
    if "P9" in policy.enabled:
        await cache.store(model, body, resp)
    await ledger.record(session_id, policy.mode, model, False, False,
                        tokens_baseline_est, tokens_actual, 0.0, cost_actual, 0, reports)
    return JSONResponse(resp)


def build_proxy_router(app):  # type: ignore[no-untyped-def]
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        return await _handle_chat(request)

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
