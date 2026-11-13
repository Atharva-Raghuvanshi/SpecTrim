"""WP2: Real-process crash/resume test.

Starts a worker process, kills it mid-pipeline (after stages 1..k have completed
and been persisted), reclaims the expired lease, and completes the run.
Asserts that stages 1..k are NOT re-executed and the session completes to 'ready'.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid

import pytest
from app import winloop  # noqa: F401
from app.gateway.fake import FakeGateway
from app.pipeline.orchestrator import PipelineContext, run_pipeline_job
from app.storage.repos import JobsRepo, SessionsRepo
from tests.worker_runner import STAGE_RESPONSES

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5433/qo")

pytestmark = pytest.mark.usefixtures("db_pool")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _get_done_stages(db_pool, session_id: str) -> list[str]:
    async def _q():
        async with db_pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT stage_key FROM stages WHERE session_id = %s AND status = 'done' "
                "ORDER BY created_at ASC",
                (session_id,),
            )
            return [str(row[0]) for row in await cur.fetchall()]

    return _run(_q())


def _start_worker_process(dsn: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["STAGE_DELAY_S"] = "0.5"
    env.pop("GROQ_API_KEY", None)

    # Resolve 'server' directory for PYTHONPATH
    server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env["PYTHONPATH"] = server_dir

    return subprocess.Popen(
        [sys.executable, "-m", "tests.worker_runner", dsn],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_real_process_crash_resume(db_pool):
    """Start worker subprocess, kill mid-pipeline, reclaim lease, verify stages 1..k not re-run."""
    # 1. Create a session and enqueue pipeline job
    session_id, _ = _run(
        SessionsRepo(db_pool).create_idempotent("habit tracker", f"k-crash-{uuid.uuid4()}")
    )
    _run(JobsRepo(db_pool).enqueue(session_id, kind="pipeline"))

    # 2. Start worker subprocess (paces each stage by 0.5s)
    proc = _start_worker_process(TEST_DSN)

    # 3. Wait until at least 1 stage is persisted (worker is mid-pipeline)
    deadline = time.time() + 15
    stages_before: list[str] = []
    while time.time() < deadline:
        time.sleep(0.2)
        stages_before = _get_done_stages(db_pool, session_id)
        if len(stages_before) >= 1:
            break

    # 4. Kill worker subprocess immediately while mid-pipeline
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)

    # Assert worker had completed stages 1..k but NOT the entire pipeline
    stages_before = _get_done_stages(db_pool, session_id)
    assert 1 <= len(stages_before) < 5, (
        f"Expected mid-pipeline state (1..4 stages done), got {stages_before} "
        f"(proc rc={proc.returncode})"
    )

    # 5. Back-date the lease in DB to simulate lease expiration
    async def _backdate():
        async with db_pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE jobs SET leased_until = now() - interval '1 second' "
                "WHERE session_id = %s AND status = 'leased'",
                (session_id,),
            )
            await conn.commit()

    _run(_backdate())

    # 6. Reclaim expired lease
    jobs = JobsRepo(db_pool)
    reclaimed = _run(jobs.reclaim_expired())
    assert reclaimed >= 1, "Expected at least 1 job lease to be reclaimed"

    # 7. Resume pipeline to completion with recording FakeGateway
    resume_gw = FakeGateway(canned=STAGE_RESPONSES)
    ctx = PipelineContext(db_pool, resume_gw, 3)

    final_status = _run(run_pipeline_job(ctx, session_id))
    assert final_status == "ready", f"Pipeline did not finish at 'ready', got {final_status}"

    # 8. Assertions:
    # a. Stages 1..k that completed before kill were NOT re-executed
    resumed_stage_calls = [c["stage"] for c in resume_gw.calls]
    for stage_key in stages_before:
        assert stage_key not in resumed_stage_calls, (
            f"Stage {stage_key} was already done before crash but was re-executed on resume!"
        )

    # b. Remaining stages (k+1..N) WERE executed during resume
    all_stages = ["analyzer", "classifier", "spec_writer", "contract", "decomposer"]
    expected_remaining = [s for s in all_stages if s not in stages_before]
    assert resumed_stage_calls == expected_remaining, (
        f"Expected resume to run {expected_remaining}, but it ran {resumed_stage_calls}"
    )

    # c. Session is ready with version 1
    session = _run(SessionsRepo(db_pool).get(session_id))
    assert session is not None
    assert session["status"] == "ready"
    assert session["current_version"] == 1

    # d. All 5 stages are done in DB
    done_after = _get_done_stages(db_pool, session_id)
    assert sorted(done_after) == sorted(all_stages)
