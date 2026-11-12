"""P9 exact-match response cache (M11.1) and savings ledger (M10.3)."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from app.optimizer.passes import cache_key


class ResponseCache:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get(self, model: str, request: dict) -> dict | None:
        key = cache_key(model, request, cache_version=self.version_for(model))
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT response FROM proxy_cache WHERE model = %s AND key_hash = %s "
                "AND cache_version = 1",
                (model, key),
            )
            row = await cur.fetchone()
        return dict(row[0]) if row else None

    async def store(self, model: str, request: dict, response: dict) -> None:
        key = cache_key(model, request, cache_version=self.version_for(model))
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO proxy_cache (model, key_hash, response) VALUES (%s, %s, %s) "
                "ON CONFLICT (model, key_hash, cache_version) DO NOTHING",
                (model, key, Jsonb(response)),
            )

    async def invalidate_model(self, model: str) -> int:
        """Bump the version for a model: old entries become unreachable (zero stale serves)."""
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(MAX(cache_version), 1) FROM proxy_cache WHERE model = %s",
                (model,),
            )
            current = int((await cur.fetchone())[0])
            await cur.execute("DELETE FROM proxy_cache WHERE model = %s", (model,))
            count = cur.rowcount  # next key uses version+1: zero stale serves possible
        self._next_version(model, current + 1)
        return count

    _versions: dict[str, int] = {}

    def _next_version(self, model: str, version: int) -> None:
        self._versions[model] = max(self._versions.get(model, 1), version)

    def version_for(self, model: str) -> int:
        return self._versions.get(model, 1)


class SavingsLedger:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def record(
        self, session_id: str | None, mode: str, model: str, cache_hit: bool,
        passthrough: bool, tokens_baseline_est: int, tokens_actual: int | None,
        cost_baseline_est: float, cost_actual: float | None, latency_ms: int,
        pass_reports: list[dict],
    ) -> None:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO proxy_requests (session_id, mode, model, cache_hit, passthrough, "
                "tokens_baseline_est, tokens_actual, cost_baseline_est, cost_actual, latency_ms, "
                "pass_reports) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    session_id, mode, model, cache_hit, passthrough,
                    tokens_baseline_est, tokens_actual, cost_baseline_est, cost_actual,
                    latency_ms, Jsonb(pass_reports),
                ),
            )

    async def stats(self) -> dict:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(tokens_baseline_est),0), "
                "COALESCE(SUM(tokens_actual),0), COALESCE(SUM(cache_hit::int),0), "
                "COALESCE(SUM(passthrough::int),0) FROM proxy_requests"
            )
            row = await cur.fetchone()
        n, base, actual, hits, passthroughs = row
        saved = int(base) - int(actual or 0)
        return {
            "requests": int(n),
            "tokens_baseline_est": int(base),
            "tokens_actual": int(actual or 0),
            "tokens_saved_est": saved if saved > 0 else 0,
            "cache_hits": int(hits),
            "passthroughs": int(passthroughs),
        }
