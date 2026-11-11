"""Thin repositories over the PRD 5.7 tables. All SQL lives here."""
from __future__ import annotations

import uuid
from typing import Any

from app.pipeline.states import State
from psycopg.types.json import Jsonb


class SessionsRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def create_idempotent(self, idea: str, idempotency_key: str) -> tuple[str, bool]:
        """Returns (session_id, created). Reuse returns the existing session (FR-2)."""
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        trace_id = uuid.uuid4().hex
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO sessions (id, trace_id, idempotency_key, idea) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING "
                "RETURNING id",
                (session_id, trace_id, idempotency_key, idea),
            )
            row = await cur.fetchone()
            if row is None:  # lost a race: fetch the winner
                await cur.execute(
                    "SELECT id FROM sessions WHERE idempotency_key = %s", (idempotency_key,)
                )
                row = await cur.fetchone()
                assert row is not None
                return str(row[0]), False
            return str(row[0]), True

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT id, status, trace_id, current_version, spend_usd, idea, created_at "
                "FROM sessions WHERE id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "status": row[1],
            "trace_id": row[2],
            "current_version": row[3],
            "spend_usd": float(row[4]),
            "idea": row[5],
            "created_at": row[6].isoformat(),
        }

    async def set_status(self, session_id: str, status: str, current_version: int | None = None) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE sessions SET status = %s, "
                "current_version = COALESCE(%s, current_version) WHERE id = %s",
                (status, current_version, session_id),
            )
            await conn.commit()

    async def add_spend(self, session_id: str, usd: float) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE sessions SET spend_usd = spend_usd + %s WHERE id = %s", (usd, session_id)
            )
            await cur.execute(
                "INSERT INTO daily_spend (day, usd) VALUES (CURRENT_DATE, %s) "
                "ON CONFLICT (day) DO UPDATE SET usd = daily_spend.usd + EXCLUDED.usd",
                (usd,),
            )
            await conn.commit()

    async def daily_spend(self) -> float:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT usd FROM daily_spend WHERE day = CURRENT_DATE")
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def idea(self, session_id: str) -> str:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT idea FROM sessions WHERE id = %s", (session_id,))
            row = await cur.fetchone()
        if row is None:
            raise KeyError(session_id)
        return str(row[0])


class JobsRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def enqueue(
        self, session_id: str, kind: str = "pipeline", payload: dict | None = None
    ) -> str:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO jobs (session_id, kind, payload) VALUES (%s, %s, %s) RETURNING id",
                (session_id, kind, Jsonb(payload) if payload else None),
            )
            job_id = str((await cur.fetchone())[0])
            await conn.commit()
            return job_id

    async def claim(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """Claim one runnable job with FOR UPDATE SKIP LOCKED (attempts incremented)."""
        query = (
            "UPDATE jobs SET status = 'leased', locked_by = %s, leased_until = now() + "
            "make_interval(secs => %s), attempts = attempts + 1 WHERE id = ("
            "  SELECT id FROM jobs WHERE status = 'pending' AND run_after <= now()"
            "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, session_id, kind, attempts, max_attempts"
        )
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(query, (worker_id, lease_seconds))
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            return None
        return {
            "id": str(row[0]),
            "session_id": row[1],
            "kind": row[2],
            "attempts": row[3],
            "max_attempts": row[4],
        }

    async def complete(self, job_id: str) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("UPDATE jobs SET status = 'done', locked_by = NULL WHERE id = %s", (job_id,))
            await conn.commit()

    async def fail_transient(self, job_id: str, attempts: int) -> None:
        """run_after = now() + min(300, 2^attempts)s + random(0,1)s (PRD 5.8)."""
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
            await cur.execute("UPDATE jobs SET status = 'dead', locked_by = NULL WHERE id = %s", (job_id,))
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


class StagesRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get_done(self, session_id: str, stage_key: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT input_hash, output FROM stages "
                "WHERE session_id = %s AND stage_key = %s AND status = 'done'",
                (session_id, stage_key),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {"input_hash": row[0], "output": row[1]}

    async def save(
        self,
        session_id: str,
        stage_key: str,
        input_hash: str,
        output: Any,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float | None,
        latency_ms: int,
    ) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO stages (session_id, stage_key, status, input_hash, output, model, "
                "tokens_in, tokens_out, cost_usd, latency_ms) "
                "VALUES (%s, %s, 'done', %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (session_id, stage_key) DO UPDATE SET "
                "status = 'done', input_hash = EXCLUDED.input_hash, output = EXCLUDED.output, "
                "model = EXCLUDED.model, tokens_in = EXCLUDED.tokens_in, "
                "tokens_out = EXCLUDED.tokens_out, cost_usd = EXCLUDED.cost_usd, "
                "latency_ms = EXCLUDED.latency_ms",
                (
                    session_id,
                    stage_key,
                    input_hash,
                    Jsonb(output),
                    model,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    latency_ms,
                ),
            )
            await conn.commit()


class VersionsRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def insert(self, session_id: str, version: int, package: dict, created_from: str) -> bool:
        """ON CONFLICT DO NOTHING: a replayed job cannot duplicate a version (PRD 5.8)."""
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO versions (session_id, version, package, created_from) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (session_id, version) DO NOTHING",
                (session_id, version, Jsonb(package), created_from),
            )
            inserted = cur.rowcount == 1
            await conn.commit()
            return inserted

    async def get(self, session_id: str, version: int) -> dict | None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT package FROM versions WHERE session_id = %s AND version = %s",
                (session_id, version),
            )
            row = await cur.fetchone()
        return dict(row[0]) if row else None

    async def latest_version(self, session_id: str) -> int | None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT MAX(version) FROM versions WHERE session_id = %s", (session_id,)
            )
            row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None


class QuestionsRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def upsert(self, session_id: str, questions: list[dict], round: int = 1) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            for q in questions:
                await cur.execute(
                    "INSERT INTO questions (id, session_id, round, text, category) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (session_id, id) DO NOTHING",
                    (q["id"], session_id, round, q["text"], q["category"]),
                )
            await conn.commit()

    async def list_unanswered(self, session_id: str) -> list[dict]:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT id, text, category FROM questions "
                "WHERE session_id = %s AND answer IS NULL ORDER BY id",
                (session_id,),
            )
            rows = await cur.fetchall()
        return [{"id": r[0], "text": r[1], "category": r[2]} for r in rows]

    async def answer(self, session_id: str, question_id: str, answer: str) -> bool:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE questions SET answer = %s, answered_at = now() "
                "WHERE session_id = %s AND id = %s AND answer IS NULL",
                (answer, session_id, question_id),
            )
            updated = cur.rowcount == 1
            await conn.commit()
            return updated

    async def answers(self, session_id: str) -> list[str]:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT answer FROM questions WHERE session_id = %s "
                "AND answer IS NOT NULL ORDER BY id",
                (session_id,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]


class EventsRepo:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def append(self, session_id: str, type_: str, payload: dict | None = None) -> int:
        """Monotonic per-session seq; safe under concurrency."""
        query = (
            "INSERT INTO events (session_id, seq, type, payload) "
            "SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s FROM events WHERE session_id = %s "
            "RETURNING seq"
        )
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(query, (session_id, type_, Jsonb(payload or {}), session_id))
            seq = int((await cur.fetchone())[0])
            await conn.commit()
            return seq

    async def after(self, session_id: str, last_seq: int, limit: int = 100) -> list[dict]:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT seq, type, payload, created_at FROM events "
                "WHERE session_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (session_id, last_seq, limit),
            )
            rows = await cur.fetchall()
        return [
            {"seq": r[0], "type": r[1], "payload": r[2], "created_at": r[3].isoformat()}
            for r in rows
        ]


def state_of(row_status: str) -> State:
    return State(row_status)
