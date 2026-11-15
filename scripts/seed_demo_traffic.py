"""Seed clearly-flagged demo rows into proxy_demo_requests (WP9).

Only meaningful with UI_DEMO=1 — the UI reads the demo table only then.
Demo rows NEVER appear in /stats or measured results (separate table).

Usage:
  UI_DEMO=1 python -m scripts.seed_demo_traffic            # 40 rows, 24h spread
  python -m scripts.seed_demo_traffic --wipe               # remove demo rows
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import winloop  # noqa: E402,F401
from app.storage.db import close_pool, open_pool  # noqa: E402
from app.storage.migrate import apply_migrations  # noqa: E402

PASSES = ["P9", "P4", "P5", "P2", "P3", "P1", "P7", "P6"]
MODES = ["off", "safe", "balanced"]
MODELS = ["groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b", "groq/qwen/qwen3.8-27b"]


async def seed(pool, n: int = 40) -> None:
    rnd = random.Random(42)
    now_base = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM proxy_demo_requests")
        for i in range(n):
            created = now_base - timedelta(minutes=rnd.randint(1, 60 * 24))
            mode = rnd.choice(MODES)
            model = rnd.choice(MODELS)
            base = rnd.randint(1200, 6000)
            saved = rnd.randint(0, int(base * 0.45))
            actual = base - saved
            hit = rnd.random() < 0.3
            reports = [
                {"pass": p, "tokens_before_est": base // 4,
                 "tokens_after_est": base // 4 - (saved // len(PASSES)) if p in ("P4", "P5") else base // 4,
                 "changed": p in ("P4", "P5"), "skipped": ""}
                for p in PASSES if rnd.random() > 0.25
            ]
            prompt_act = actual
            comp_act = rnd.randint(80, 300)
            tot_act = prompt_act + comp_act
            await cur.execute(
                "INSERT INTO proxy_demo_requests (session_id, mode, model, cache_hit, "
                "passthrough, tokens_baseline_est, tokens_actual, latency_ms, pass_reports, "
                "created_at, prompt_tokens_actual, completion_tokens_actual, reasoning_tokens_actual) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    f"demo_ses_{i:03d}", mode, model, hit, mode == "off",
                    base, tot_act, rnd.randint(2, 40),
                    json.dumps(reports), created,
                    prompt_act, comp_act, 0,
                ),
            )
    print(f"seeded {n} demo rows")


async def wipe(pool) -> None:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("DELETE FROM proxy_demo_requests")
    print("demo rows wiped")


async def main_async() -> int:
    apply_migrations(os.environ.get(
        "DATABASE_URL", "postgresql://app:app@localhost:5433/qo"))
    pool = await open_pool(os.environ.get(
        "DATABASE_URL", "postgresql://app:app@localhost:5433/qo"))
    if "--wipe" in sys.argv:
        await wipe(pool)
    else:
        n = 40
        if "--n" in sys.argv:
            n = int(sys.argv[sys.argv.index("--n") + 1])
        await seed(pool, n)
    await close_pool()
    return 0


def main() -> int:
    if "--wipe" not in sys.argv and os.environ.get("UI_DEMO", "0") != "1":
        print("refusing: set UI_DEMO=1 to seed demo rows")
        return 2
    import asyncio

    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
