"""Async Postgres access (psycopg3).

Note: psycopg_pool's AsyncConnectionPool fails to initialize on this Windows
host even though direct connections are instant, so we use a lightweight
per-checkout connection factory with the same `.connection()` context manager
interface the repos expect.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        conn = await psycopg.AsyncConnection.connect(self.dsn)
        try:
            yield conn
            await conn.commit()  # commit-on-success; rollback on any error
        except BaseException:
            await conn.rollback()
            raise
        finally:
            await conn.close()


_pool: Database | None = None


async def open_pool(dsn: str) -> Database:
    global _pool
    _pool = Database(dsn)
    return _pool


async def close_pool() -> None:
    global _pool
    _pool = None
