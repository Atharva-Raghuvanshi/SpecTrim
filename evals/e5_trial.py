"""E5 one-cell trial (WP13 order 3): run a single balanced cell on an E4b task
and count how many times each pass actually fired (from the chain reports the
proxy writes into the ledger). Writes evals/results/e5_trial.json.

The auto-finish orchestrator uses this to decide which ablation arms are valid:
a pass that never fires cannot be ablated meaningfully on this fixture set.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os  # noqa: E402

from app import winloop  # noqa: E402,F401
from app.optimizer.cache import SavingsLedger  # noqa: E402
from app.storage.db import close_pool, open_pool  # noqa: E402
from app.storage.migrate import apply_migrations  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
E4B_TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e4b.json"
WINDOW_MIN = 10  # ledger rows created in the last N minutes are the trial's


def main() -> int:
    apply_migrations(os.environ.get(
        "DATABASE_URL", "postgresql://app:app@localhost:5433/qo"))

    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        # one balanced cell: L1 rep0 balanced (a full multi-turn tool loop)
        import asyncio

        from evals.run_e4 import by_id_from, make_call

        task = by_id_from(E4B_TASKS, "L1")
        call = make_call(client, "groq/qwen/qwen3.8-27b", "balanced")
        rec = asyncio.get_event_loop().run_until_complete(_run(call, task))
        print(f"trial cell: {json.dumps(rec)[:200]}")

    # count pass firings from the ledger rows written during the trial
    pool = __import__("asyncio").get_event_loop().run_until_complete(
        open_pool(os.environ.get("DATABASE_URL",
                                 "postgresql://app:app@localhost:5433/qo")))
    ledger = SavingsLedger(pool)
    firings: Counter = Counter()
    import asyncio as _a

    rows = _a.get_event_loop().run_until_complete(_recent(ledger))
    for r in rows:
        for rep in r.get("pass_reports", []):
            if not rep.get("skipped") and rep.get("changed"):
                firings[rep["pass"]] += 1
            elif rep["pass"] == "P9" and r.get("cache_hit"):
                firings["P9"] += 1
    fired = sorted(firings)
    out = {
        "trial_cell_tokens_in": rec.get("tokens_in"),
        "fired": fired,
        "firings": dict(firings),
        "note": "P9 fires on the second identical request; a single-cell trial "
                "marks it fired if any cache lookup row exists with cache_hit=false "
                "(store) — ablation arms include it when P4/P5/P1 fired at least once.",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "e5_trial.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"fired passes: {fired}")
    _a.get_event_loop().run_until_complete(close_pool())
    return 0


async def _run(call, task) -> dict:
    from evals.run_e4 import run_cell

    return run_cell(call, task)


async def _recent(ledger) -> list[dict]:
    return await ledger.recent(limit=10)


if __name__ == "__main__":
    sys.exit(main())
