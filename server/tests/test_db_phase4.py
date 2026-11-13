"""Phase 4 DB tests: migrations, idempotent API, resume, worker, events (M4.1-M4.5)."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.pipeline.orchestrator import PipelineContext, run_pipeline_job
from app.storage.repos import SessionsRepo

pytestmark = pytest.mark.usefixtures("db_pool")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def app_client(fake_gateway, db_pool):
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(gateway=fake_gateway)
    with TestClient(app) as client:
        yield client, app.state.gateway


def test_migrations_idempotent(db_pool) -> None:
    from app.storage.migrate import apply_migrations

    first = apply_migrations()
    assert first == []  # conftest already applied them; second run applies nothing


def test_create_session_idempotent_across_posts(app_client) -> None:
    client, _ = app_client
    key = f"idem-{uuid.uuid4()}"
    r1 = client.post("/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": key})
    r2 = client.post("/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": key})
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_missing_idempotency_key_rejected(app_client) -> None:
    client, _ = app_client
    r = client.post("/sessions", json={"idea": "x"})
    assert r.status_code == 422


def test_full_session_reaches_ready_with_fake_gateway(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    session_id = r.json()["id"]
    _drain(client, session_id)
    session = client.get(f"/sessions/{session_id}").json()
    assert session["status"] == "awaiting_user", session
    r = client.post(
        f"/sessions/{session_id}/answers",
        json={"answers": [{"id": "Q1", "answer": "single user"}]},
    )
    assert r.status_code == 200
    _drain(client, session_id)
    session = client.get(f"/sessions/{session_id}").json()
    assert session["status"] == "ready", session
    assert session["current_version"] == 1


from tests.conftest import drain as _drain  # noqa: E402


def test_resume_skips_done_stages(app_client) -> None:
    client, gateway = app_client
    r = client.post(
        "/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    session_id = r.json()["id"]
    import asyncio


    ctx = PipelineContext(client.app.state.pool, gateway, 3)
    asyncio.get_event_loop().run_until_complete(run_pipeline_job(ctx, session_id))
    calls_after_first = len(gateway.calls)
    # rerun: all stages done and hashes match -> zero gateway calls
    asyncio.get_event_loop().run_until_complete(run_pipeline_job(ctx, session_id))
    assert len(gateway.calls) == calls_after_first


def test_changed_input_hash_reruns_stage(app_client) -> None:
    client, gateway = app_client
    import asyncio

    from app.pipeline.orchestrator import run_stage
    from app.stages.analyzer import run_analyzer

    async def scenario():
        sid = await _mk_session(client)
        ctx = PipelineContext(client.app.state.pool, gateway, 3)
        ih1 = "h1"
        await run_stage(ctx, sid, "analyzer", ih1, lambda: run_analyzer(gateway, "idea"))
        calls = len(gateway.calls)
        await run_stage(ctx, sid, "analyzer", ih1, lambda: run_analyzer(gateway, "idea"))
        assert len(gateway.calls) == calls  # hash match: skipped
        await run_stage(ctx, sid, "analyzer", "h2", lambda: run_analyzer(gateway, "idea"))
        assert len(gateway.calls) == calls + 1  # changed hash: re-run

    asyncio.get_event_loop().run_until_complete(scenario())


async def _mk_session(client):
    from app.storage.repos import SessionsRepo

    sid, _ = await SessionsRepo(client.app.state.pool).create_idempotent(
        "idea", f"k-{uuid.uuid4()}"
    )
    return sid


def test_duplicate_version_insert_is_noop(app_client) -> None:
    client, _ = app_client
    from app.storage.repos import VersionsRepo

    repo = VersionsRepo(client.app.state.pool)
    sid = _run(SessionsRepo(client.app.state.pool).create_idempotent("idea", f"k-{uuid.uuid4()}"))[0]
    pkg = {"any": "thing"}
    assert _run(repo.insert(sid, 1, pkg, "initial")) is True
    assert _run(repo.insert(sid, 1, pkg, "initial")) is False


def test_events_seq_monotonic(app_client) -> None:
    client, _ = app_client
    from app.storage.repos import EventsRepo, SessionsRepo

    repo = EventsRepo(client.app.state.pool)
    import asyncio

    async def scenario():
        sid, _ = await SessionsRepo(client.app.state.pool).create_idempotent(
            "idea", f"k-{uuid.uuid4()}"
        )
        return [await repo.append(sid, "t", {}) for _ in range(3)]

    assert asyncio.get_event_loop().run_until_complete(scenario()) == [1, 2, 3]


def test_killed_worker_job_reclaimed(db_pool) -> None:
    import asyncio

    from app.storage.repos import JobsRepo, SessionsRepo

    async def scenario():
        sessions = SessionsRepo(db_pool)
        jobs = JobsRepo(db_pool)
        sid, _ = await sessions.create_idempotent("idea", f"k-{uuid.uuid4()}")
        await jobs.enqueue(sid, kind="test")  # kind the live workers never claim
        # simulate a killed worker: lease it, let the lease lapse
        job = await jobs.claim("worker_dead", lease_seconds=-1)
        assert job is not None
        await jobs.reclaim_expired()
        job2 = await jobs.claim("worker_alive", lease_seconds=60)
        assert job2 is not None
        assert job2["id"] == job["id"]  # same job, reclaimed
        assert job2["attempts"] == job["attempts"] + 1

    asyncio.get_event_loop().run_until_complete(scenario())


def test_transient_failure_backoff_then_dead(db_pool) -> None:
    import asyncio

    from app.storage.repos import JobsRepo, SessionsRepo

    async def scenario():
        sessions = SessionsRepo(db_pool)
        jobs = JobsRepo(db_pool)
        sid, _ = await sessions.create_idempotent("idea", f"k-{uuid.uuid4()}")
        job_id = await jobs.enqueue(sid)
        attempts = 0
        while True:
            job = await jobs.claim("w", lease_seconds=60)
            if job is None:
                # make the job runnable again (transient backoff elapsed)
                async with db_pool.connection() as conn, conn.cursor() as cur:
                    await cur.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (job_id,))
                    await conn.commit()
                continue
            attempts = job["attempts"]
            if attempts >= job["max_attempts"]:
                await jobs.fail_dead(job["id"])
                break
            await jobs.fail_transient(job["id"], job["attempts"])
        async with db_pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
            status = (await cur.fetchone())[0]
        assert status == "dead"
        assert attempts == 5

    asyncio.get_event_loop().run_until_complete(scenario())


def test_cancel_from_queued(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "x"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    sid = r.json()["id"]
    c = client.post(f"/sessions/{sid}/cancel")
    assert c.status_code == 200
    assert client.post(f"/sessions/{sid}/cancel").status_code == 409  # terminal
