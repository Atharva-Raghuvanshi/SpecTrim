"""FastAPI app: sessions, answers, revisions, SSE, exports (Phases 4-6)."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app import winloop  # noqa: F401  (must run before asyncio/psycopg on Windows)
from app.api_sse import sse_response
from app.exports import to_markdown, to_openapi_yaml
from app.gateway.base import Gateway
from app.gateway.config import load_models_config
from app.gateway.litellm_gateway import LiteLLMGateway
from app.observability import REQUESTS, SESSIONS_CREATED, get_logger, setup_logging, trace_id_var
from app.pipeline.orchestrator import PipelineContext, cancel_session
from app.pipeline.states import InvalidTransition
from app.settings import Settings, get_settings
from app.storage.db import close_pool, open_pool
from app.storage.migrate import apply_migrations
from app.storage.repos import EventsRepo, JobsRepo, QuestionsRepo, SessionsRepo, VersionsRepo


class CreateSession(BaseModel):
    idea: str = Field(min_length=1)


class AnswerItem(BaseModel):
    id: str
    answer: str = Field(min_length=1)


class AnswersIn(BaseModel):
    answers: list[AnswerItem] = Field(min_length=1)


class RevisionIn(BaseModel):
    type: str = Field(pattern=r"^(change_request|failure_report)$")
    feedback: str = Field(min_length=1)


def build_gateway(settings: Settings) -> Gateway:
    cfg = load_models_config(settings.models_config, settings.active_config)
    return LiteLLMGateway(cfg)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    setup_logging(settings.log_level)
    apply_migrations(settings.database_url)
    app.state.pool = await open_pool(settings.database_url)
    app.state.gateway = build_gateway(settings)
    yield
    await close_pool()


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="Query Optimizer Service", lifespan=lifespan)
    if gateway is not None:  # test injection: skip real gateway construction
        original = app.router.lifespan_context

        @asynccontextmanager
        async def patched(app: FastAPI) -> AsyncIterator[None]:
            async with original(app):
                app.state.gateway = gateway
                yield

        app.router.lifespan_context = patched

    def ctx(request: Request) -> PipelineContext:
        settings: Settings = request.app.state.settings
        return PipelineContext(
            request.app.state.pool, request.app.state.gateway, settings.max_questions_per_round
        )

    def repos(request: Request) -> dict:
        pool = request.app.state.pool
        return {
            "sessions": SessionsRepo(pool),
            "jobs": JobsRepo(pool),
            "questions": QuestionsRepo(pool),
            "events": EventsRepo(pool),
        }

    @app.middleware("http")
    async def trace_and_metrics(request: Request, call_next):  # type: ignore[no-untyped-def]
        import uuid as _uuid

        tid = request.headers.get("X-Trace-Id") or _uuid.uuid4().hex
        token = trace_id_var.set(tid)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
        response.headers["X-Trace-Id"] = tid
        return response

    @app.get("/metrics")
    async def metrics() -> Response:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz(request: Request) -> PlainTextResponse:
        pool = getattr(request.app.state, "pool", None)
        if pool is not None:
            try:
                async with pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1")
            except Exception:
                return PlainTextResponse("DB unreachable", status_code=503)
        return PlainTextResponse("OK")

    @app.post("/sessions", status_code=201)
    async def create_session(
        body: CreateSession, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")
    ) -> JSONResponse:
        if not idempotency_key.strip():
            raise HTTPException(422, "Idempotency-Key must be non-empty")
        r = repos(request)
        session_id, created = await r["sessions"].create_idempotent(body.idea, idempotency_key)
        if created:
            SESSIONS_CREATED.inc()
            get_logger("api").info("session_created", session_id=session_id)
            await r["events"].append(session_id, "session_created", {"idea": body.idea[:200]})
            await r["jobs"].enqueue(session_id)
        session = await r["sessions"].get(session_id)
        code = 201 if created else 200
        return JSONResponse({"id": session_id, "status": session["status"]}, status_code=code)

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> JSONResponse:
        session = await repos(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        return JSONResponse(session)

    @app.post("/sessions/{session_id}/answers")
    async def post_answers(session_id: str, body: AnswersIn, request: Request) -> JSONResponse:
        r = repos(request)
        session = await r["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if session["status"] != "awaiting_user":
            raise HTTPException(409, f"cannot answer in state {session['status']}")
        answered = 0
        for item in body.answers:
            if await r["questions"].answer(session_id, item.id, item.answer):
                answered += 1
        if answered == 0:
            raise HTTPException(422, "no matching unanswered questions")
        remaining = await r["questions"].list_unanswered(session_id)
        await r["events"].append(session_id, "answers_received", {"answered": answered})
        await r["jobs"].enqueue(session_id)  # worker moves awaiting_user -> analyzing
        return JSONResponse({"answered": answered, "remaining": len(remaining)})

    @app.post("/sessions/{session_id}/cancel")
    async def cancel(session_id: str, request: Request) -> JSONResponse:
        session = await repos(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        try:
            await cancel_session(ctx(request), session_id)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse({"id": session_id, "status": "cancelled"})

    @app.post("/sessions/{session_id}/revisions")
    async def post_revision(session_id: str, body: RevisionIn, request: Request) -> JSONResponse:
        r = repos(request)
        session = await r["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if session["status"] != "ready":
            raise HTTPException(409, f"can only revise a ready session (now {session['status']})")
        try:
            from app.pipeline.orchestrator import _move
            from app.pipeline.states import State

            await _move(ctx(request), session_id, State.revising)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        await r["jobs"].enqueue(
            session_id,
            kind="revision",
            payload={"feedback": body.feedback, "type": body.type},
        )
        return JSONResponse({"id": session_id, "status": "revising"}, status_code=202)

    @app.get("/sessions/{session_id}/diff")
    async def get_diff(
        session_id: str, request: Request, from_version: int | None = None, to_version: int | None = None
    ) -> JSONResponse:
        from app.diffing.diff import diff_packages

        r = repos(request)
        latest = await r["sessions"].get(session_id)
        if latest is None:
            raise HTTPException(404, "session not found")
        to_v = to_version or latest["current_version"]
        from_v = from_version or (to_v - 1 if to_v and to_v > 1 else to_v)
        if not to_v or not from_v:
            raise HTTPException(404, "no versions to diff")
        old = await VersionsRepo(request.app.state.pool).get(session_id, from_v)
        new = await VersionsRepo(request.app.state.pool).get(session_id, to_v)
        if old is None or new is None:
            raise HTTPException(404, "version not found")
        return JSONResponse(diff_packages(old, new))

    @app.get("/sessions/{session_id}/questions")
    async def get_questions(session_id: str, request: Request) -> JSONResponse:
        session = await repos(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        questions = await repos(request)["questions"].list_unanswered(session_id)
        return JSONResponse({"questions": questions})

    @app.get("/sessions/{session_id}/events")
    async def get_events(
        session_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        session = await repos(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        last_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        events = repos(request)["events"]
        return sse_response(request, session_id, events, last_seq)

    @app.get("/sessions/{session_id}/export")
    async def export_session(
        session_id: str, request: Request, format: str = "json", version: int | None = None
    ) -> Response:
        session = await repos(request)["sessions"].get(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        v = version or session["current_version"]
        if not v:
            raise HTTPException(404, "no versions yet")
        package = await VersionsRepo(request.app.state.pool).get(session_id, v)
        if package is None:
            raise HTTPException(404, f"version {v} not found")
        if format == "json":
            return Response(package and json.dumps(package, indent=2), media_type="application/json")
        if format == "markdown":
            return Response(to_markdown(package), media_type="text/markdown")
        if format in ("openapi", "yaml"):
            return Response(to_openapi_yaml(package), media_type="application/yaml")
        raise HTTPException(422, f"unknown format {format!r} (json|markdown|openapi)")

    return app


app = create_app()
