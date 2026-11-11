"""Shared fixtures. DB tests skip automatically when Postgres is unreachable."""
from __future__ import annotations

import asyncio
import os

import pytest
from app import winloop  # noqa: F401  - SelectorEventLoop policy before any loop is created

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5433/qo")
os.environ.setdefault("DATABASE_URL", TEST_DSN)


def _db_up() -> bool:
    try:
        import psycopg

        with psycopg.connect(TEST_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


DB_UP = _db_up()


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.SelectorEventLoop()
    yield loop
    loop.close()


@pytest.fixture()
def db_pool():
    if not DB_UP:
        pytest.skip("Postgres not reachable; run `make up` first")
    from app.storage.db import close_pool, open_pool
    from app.storage.migrate import apply_migrations

    async def _setup():
        apply_migrations(TEST_DSN)
        pool = await open_pool(TEST_DSN)
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "TRUNCATE events, questions, versions, stages, jobs, sessions, daily_spend"
            )
            await conn.commit()
        return pool

    pool = asyncio.get_event_loop().run_until_complete(_setup())
    yield pool
    asyncio.get_event_loop().run_until_complete(close_pool())


@pytest.fixture()
def fake_gateway():
    from tests.test_stages import canned_gateway

    return canned_gateway()
