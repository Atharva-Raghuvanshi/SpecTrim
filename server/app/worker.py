"""Worker: claim loop with SKIP LOCKED, leases, retries, dead jobs (M4.2)."""
from __future__ import annotations

import asyncio
import uuid

from psycopg_pool import AsyncConnectionPool

from app import winloop  # noqa: F401  (must run before asyncio/psycopg on Windows)
from app.gateway.base import Gateway, GatewayRateLimited
from app.pipeline.orchestrator import PipelineContext, run_pipeline_job
from app.pipeline.states import InvalidTransition
from app.settings import Settings
from app.storage.repos import EventsRepo, SessionsRepo


class Worker:
    def __init__(self, pool: AsyncConnectionPool, gateway: Gateway, settings: Settings) -> None:
        self.pool = pool
        self.gateway = gateway
        self.settings = settings
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.jobs = _Jobs(pool)

    async def process_one(self) -> bool:
        """Claim and process one job. Returns True if a job was processed."""
        job = await self.jobs.claim(self.worker_id, self.settings.lease_seconds)
        if job is None:
            await self.jobs.reclaim_expired()
            return False
        ctx = PipelineContext(self.pool, self.gateway, self.settings.max_questions_per_round)
        try:
            await run_pipeline_job(ctx, job["session_id"])
            await self.jobs.complete(job["id"])
        except GatewayRateLimited:
            await self.jobs.fail_transient(job["id"], job["attempts"])
        except InvalidTransition:
            await self.jobs.fail_dead(job["id"])
        except Exception:
            if job["attempts"] >= job["max_attempts"]:
                await self.jobs.fail_dead(job["id"])
                await self._mark_session_failed(job["session_id"])
            else:
                await self.jobs.fail_transient(job["id"], job["attempts"])
        return True

    async def _mark_session_failed(self, session_id: str) -> None:
        sessions = SessionsRepo(self.pool)
        events = EventsRepo(self.pool)
        session = await sessions.get(session_id)
        if session is None:
            return
        from app.pipeline.states import State, transition

        try:
            transition(State(session["status"]), State.failed)
        except InvalidTransition:
            return
        await sessions.set_status(session_id, State.failed.value)
        await events.append(session_id, "state_changed", {"to": "failed"})

    async def run_forever(self, poll_interval: float = 0.5) -> None:
        while True:
            await self.process_one()
            await asyncio.sleep(poll_interval)


class _Jobs:
    """Job-queue SQL; mirrors JobsRepo but kept local to the worker for leases."""

    def __init__(self, pool) -> None:
        self.pool = pool

    async def claim(self, worker_id: str, lease_seconds: int) -> dict | None:
        query = (
            "UPDATE jobs SET status = 'leased', locked_by = %s, leased_until = now() + "
            "make_interval(secs => %s), attempts = attempts + 1 WHERE id = ("
            "  SELECT id FROM jobs WHERE status = 'pending' AND run_after <= now()"
            "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id::text, session_id, attempts, max_attempts"
        )
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(query, (worker_id, lease_seconds))
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            return None
        return {"id": row[0], "session_id": row[1], "attempts": row[2], "max_attempts": row[3]}

    async def complete(self, job_id: str) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (job_id,))
            await conn.commit()

    async def fail_transient(self, job_id: str, attempts: int) -> None:
        import random

        delay = min(300, 2**attempts) + random.random()
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE jobs SET status = 'pending', locked_by = NULL, leased_until = NULL, "
                "run_after = now() + make_interval(secs => %s) WHERE id = %s",
                (delay, job_id),
            )
            await conn.commit()

    async def fail_dead(self, job_id: str) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE jobs SET status = 'dead', locked_by = NULL WHERE id = %s", (job_id,)
            )
            await conn.commit()

    async def reclaim_expired(self) -> int:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE jobs SET status = 'pending', locked_by = NULL, leased_until = NULL "
                "WHERE status = 'leased' AND leased_until < now()"
            )
            count = cur.rowcount
            await conn.commit()
            return count
