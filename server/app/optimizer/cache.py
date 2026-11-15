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
        prompt_tokens_actual: int | None = None,
        completion_tokens_actual: int | None = None,
        reasoning_tokens_actual: int | None = None,
        demo: bool = False,
    ) -> None:
        table = "proxy_demo_requests" if demo else "proxy_requests"
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {table} (session_id, mode, model, cache_hit, passthrough, "
                f"tokens_baseline_est, tokens_actual, cost_baseline_est, cost_actual, latency_ms, "
                f"pass_reports, prompt_tokens_actual, completion_tokens_actual, reasoning_tokens_actual) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    session_id, mode, model, cache_hit, passthrough,
                    tokens_baseline_est, tokens_actual, cost_baseline_est, cost_actual,
                    latency_ms, Jsonb(pass_reports),
                    prompt_tokens_actual, completion_tokens_actual, reasoning_tokens_actual,
                ),
            )

    async def stats(self) -> dict:
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*), "
                "COALESCE(SUM(CASE WHEN prompt_tokens_actual IS NOT NULL THEN tokens_baseline_est ELSE 0 END), 0), "
                "COALESCE(SUM(prompt_tokens_actual), 0), "
                "COALESCE(SUM(cache_hit::int), 0), "
                "COALESCE(SUM(passthrough::int), 0), "
                "COALESCE(SUM(completion_tokens_actual), 0), "
                "COALESCE(SUM(reasoning_tokens_actual), 0), "
                "SUM(CASE WHEN prompt_tokens_actual IS NULL THEN 1 ELSE 0 END) "
                "FROM proxy_requests"
            )
            row = await cur.fetchone()
        n, base, actual, hits, passthroughs, comp, reas, excl = row
        saved = int(base) - int(actual or 0)
        return {
            "requests": int(n),
            "tokens_baseline_est": int(base),
            "tokens_actual": int(actual or 0),
            "prompt_tokens_baseline_est": int(base),
            "prompt_tokens_actual": int(actual or 0),
            "completion_tokens": int(comp or 0),
            "reasoning_tokens": int(reas or 0),
            "excluded_count": int(excl or 0),
            "tokens_saved_est": saved,
            "cache_hits": int(hits),
            "passthroughs": int(passthroughs),
        }


    RANGE_INTERVALS = {"1h": "1 minute", "24h": "30 minutes", "7d": "6 hours"}
    RANGE_SPANS = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}

    async def series(self, rng: str, demo: bool = False) -> list[dict]:
        """Time buckets for the range: baseline-est vs actual, hits, latency."""
        bucket = self.RANGE_INTERVALS.get(rng, "30 minutes")
        span = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}[rng]
        table = "proxy_demo_requests" if demo else "proxy_requests"
        q = (
            f"SELECT date_bin(make_interval(secs => %s), created_at, '2000-01-01') AS b, "
            f"COALESCE(SUM(CASE WHEN prompt_tokens_actual IS NOT NULL THEN tokens_baseline_est ELSE 0 END), 0), "
            f"COALESCE(SUM(prompt_tokens_actual), 0), COUNT(*), "
            f"COALESCE(SUM(cache_hit::int), 0), "
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), "
            f"COALESCE(SUM(completion_tokens_actual), 0), "
            f"COALESCE(SUM(reasoning_tokens_actual), 0) "
            f"FROM {table} WHERE created_at > now() - INTERVAL '{span}' "
            f"GROUP BY b ORDER BY b"
        )
        secs = {"1 minute": 60, "30 minutes": 1800, "6 hours": 21600}[bucket]
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(q, (secs,))
            rows = await cur.fetchall()
        return [
            {
                "bucket": r[0].isoformat(),
                "tokens_baseline_est": int(r[1] or 0),
                "tokens_actual": int(r[2] or 0),
                "prompt_tokens_baseline_est": int(r[1] or 0),
                "prompt_tokens_actual": int(r[2] or 0),
                "requests": int(r[3]),
                "cache_hits": int(r[4]),
                "latency_p50_ms": int(r[5] or 0),
                "latency_p95_ms": int(r[6] or 0),
                "completion_tokens": int(r[7] or 0),
                "reasoning_tokens": int(r[8] or 0),
            }
            for r in rows
        ]

    async def passes(self, rng: str, demo: bool = False) -> list[dict]:
        span = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}[rng]
        table = "proxy_demo_requests" if demo else "proxy_requests"
        q = (
            f"SELECT pr->>'pass', SUM((pr->>'tokens_before_est')::int - "
            f"(pr->>'tokens_after_est')::int), COUNT(*) FROM {table}, "
            f"jsonb_array_elements(pass_reports) pr "
            f"WHERE created_at > now() - INTERVAL '{span}' "
            f"AND COALESCE(pr->>'skipped','') = '' "
            f"GROUP BY pr->>'pass' ORDER BY 2 DESC"
        )
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(q)
            rows = await cur.fetchall()
        return [{"pass": r[0], "tokens_saved_est": max(0, int(r[1] or 0)), "uses": int(r[2])}
                for r in rows]

    async def recent(self, demo: bool = False, limit: int = 50, offset: int = 0) -> dict:
        table = "proxy_demo_requests" if demo else "proxy_requests"
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT COUNT(*) FROM {table}",
            )
            total = int((await cur.fetchone())[0])
            await cur.execute(
                f"SELECT created_at, model, mode, tokens_baseline_est, COALESCE(tokens_actual,0), "
                f"pass_reports, cache_hit, passthrough, latency_ms, "
                f"prompt_tokens_actual, completion_tokens_actual, reasoning_tokens_actual FROM {table} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            rows = await cur.fetchall()
        items = []
        for r in rows:
            reports = list(r[5] or [])
            base = int(r[3])
            p_act = r[9]
            if p_act is not None:
                saved = int(base) - int(p_act)
                saved_pct = round(100 * saved / base, 1) if base else 0
            else:
                saved = max(0, base - int(r[4]))
                saved_pct = round(100 * saved / base, 1) if base else 0
            items.append({
                "time": r[0].isoformat(), "model": r[1], "mode": r[2],
                "tokens_baseline_est": base,
                "tokens_actual": int(r[4]),
                "prompt_tokens_actual": int(p_act) if p_act is not None else None,
                "completion_tokens_actual": int(r[10]) if r[10] is not None else None,
                "reasoning_tokens_actual": int(r[11]) if r[11] is not None else None,
                "saved_pct": saved_pct,
                "passes_applied": sum(1 for x in reports if not x.get("skipped")),
                "cache_hit": bool(r[6]), "passthrough": bool(r[7]),
                "latency_ms": int(r[8] or 0),
            })
        return {"total": total, "items": items}

    async def summary(self, rng: str, demo: bool = False) -> dict:
        span = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}[rng]
        table = "proxy_demo_requests" if demo else "proxy_requests"
        q = (
            f"SELECT "
            f"COALESCE(SUM(CASE WHEN prompt_tokens_actual IS NOT NULL THEN tokens_baseline_est ELSE 0 END), 0), "
            f"COALESCE(SUM(prompt_tokens_actual), 0), "
            f"COUNT(*), "
            f"COALESCE(SUM(cache_hit::int), 0), "
            f"COALESCE(SUM(passthrough::int), 0), "
            f"COALESCE(SUM(cost_actual), 0), "
            f"COUNT(cost_actual), "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), "
            f"SUM(CASE WHEN pass_reports::text LIKE '%invalid_after_pass%' THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN pass_reports::text LIKE '%invalid_original%' THEN 1 ELSE 0 END), "
            f"COALESCE(SUM(completion_tokens_actual), 0), "
            f"COALESCE(SUM(reasoning_tokens_actual), 0), "
            f"SUM(CASE WHEN prompt_tokens_actual IS NULL THEN 1 ELSE 0 END), "
            f"COUNT(prompt_tokens_actual) "
            f"FROM {table} WHERE created_at > now() - INTERVAL '{span}'"
        )
        q_modes = (
            f"SELECT COALESCE(mode, 'unknown'), COUNT(*) FROM {table} "
            f"WHERE created_at > now() - INTERVAL '{span}' GROUP BY 1"
        )
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(q)
            r = await cur.fetchone()
            await cur.execute(q_modes)
            modes_rows = await cur.fetchall()

        base, actual, n, hits, passthroughs, cost, cost_n, p95, inv_ap, inv_orig, comp, reas, excl, measured = r
        saved = int(base) - int(actual)
        pct = round(100 * saved / base, 1) if (base and measured) else 0.0
        by_mode = {row[0]: int(row[1]) for row in modes_rows}

        return {
            "range": rng,
            "tokens_saved_est": saved if measured else 0,
            "tokens_saved_pct_est": pct,
            "prompt_tokens_baseline_est": int(base),
            "prompt_tokens_actual": int(actual),
            "completion_tokens": int(comp or 0),
            "reasoning_tokens": int(reas or 0),
            "excluded_count": int(excl or 0),
            "measured_count": int(measured or 0),
            "cost_saved_usd": float(cost) if cost_n else None,
            "requests": int(n),
            "cache_hits": int(hits),
            "cache_hit_rate": round(100 * hits / n, 1) if n else 0.0,
            "passthroughs": int(passthroughs),
            "invalid_after_pass_events": int(inv_ap or 0),
            "invalid_original_events": int(inv_orig or 0),
            "latency_p95_ms": int(p95 or 0),
            "by_mode": by_mode,
        }
