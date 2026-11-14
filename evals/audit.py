"""WP10 verification & honesty audit — offline analysis, no LLM calls.

Produces evals/results/audit.json + printed report:
1. Reconciled n for E1/E2/E3/E4/E5 (exact prompt/cell IDs, consistent denominators)
2. E5 pass-firing audit from the savings ledger (which passes actually applied per arm)
3. E4 paired analysis (cells where off+safe+balanced all completed)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

RESULTS = Path(__file__).resolve().parent / "results"
AUDIT_WINDOW = ("2026-11-13T15:00:00+00:00", "2026-11-14T23:59:59+00:00")


def load(name: str) -> dict | None:
    files = sorted(RESULTS.glob(name))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def audit_e1_e2() -> dict:
    out: dict = {}
    v1 = load("e1_e2.json")
    v2 = load("e1_e2_v2_full.json")
    for label, d in (("e1_e2_v1", v1), ("e1_e2_v2", v2)):
        if not d:
            out[label] = "NOT MEASURED"
            continue
        per = d.get("per_prompt", [])
        valid_ids = [p["id"] for p in per if p.get("valid") is True]
        invalid_ids = [p["id"] for p in per if p.get("valid") is False and not p.get("error")]
        aborted_ids = [p["id"] for p in per if p.get("error")]
        completed = valid_ids + invalid_ids
        out[label] = {
            "attempted": d.get("ran"),
            "completed_n": len(completed),
            "valid_ids": valid_ids,
            "invalid_ids": invalid_ids,
            "quota_aborted_ids": aborted_ids,
            "validity_of_completed": (
                f"{len(valid_ids)}/{len(completed)} = "
                f"{100 * len(valid_ids) / len(completed):.0f}%"
                if completed else "n/a"
            ),
            "validity_of_attempted": (
                f"{len(valid_ids)}/{d.get('ran')} = "
                f"{100 * len(valid_ids) / max(d.get('ran'), 1):.0f}%"
            ),
            "e2_note": "P/R computed only over completed prompts with categories; "
                       "see per_prompt tp_fp_fn",
        }
    return out


def audit_e3() -> dict:
    files = sorted(RESULTS.glob("e3_groq_*.json"))
    if not files:
        return {"status": "NOT MEASURED"}
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    raw = {k: c for k, c in cells.items() if k.endswith(":raw")}
    opt = {k: c for k, c in cells.items() if k.endswith(":optimized")}
    raw_done = {k: c["pass"] for k, c in raw.items() if c.get("pass") is not None}
    opt_done = {k: c["pass"] for k, c in opt.items() if c.get("pass") is not None}
    paired = {}
    for k in sorted(raw_done):
        base = k.rsplit(":", 1)[0]
        ok = f"{base}:optimized"
        if ok in opt_done:
            paired[base] = (raw_done[k], opt_done[ok])
    wins = sum(1 for r, o in paired.values() if o and not r)
    losses = sum(1 for r, o in paired.values() if r and not o)
    ties = sum(1 for r, o in paired.values() if r == o)
    return {
        "builder_model": d.get("builder_model"),
        "cells_total": len(cells),
        "raw_completed_n": len(raw_done),
        "raw_completed_ids": sorted(raw_done),
        "optimized_completed_n": len(opt_done),
        "optimized_completed_ids": sorted(opt_done),
        "paired_n": len(paired),
        "paired_outcomes": {"optimized_won": wins, "raw_won": losses, "tie": ties},
        "raw_pass_rate": sum(raw_done.values()) / len(raw_done),
        "optimized_pass_rate": sum(opt_done.values()) / len(opt_done),
        "note": "n=11-12, Fisher's exact p≈0.64 — the raw-vs-optimized difference "
                "is NOT statistically significant; no claim either way.",
    }


def audit_e4_paired() -> dict:
    files = sorted(RESULTS.glob("e4_*.json"))
    if not files:
        return {"status": "NOT MEASURED"}
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    by_base: dict[str, dict[str, dict | None]] = {}
    for k, c in cells.items():
        base, cfg = k.rsplit(":", 1)
        by_base.setdefault(base, {})[cfg] = c if c.get("pass") is not None else None
    full = {
        b: modes
        for b, modes in by_base.items()
        if all(modes.get(m) for m in ("off", "safe", "balanced"))
    }
    out = {
        "cells_total": len(cells),
        "completed_total": sum(1 for c in cells.values() if c.get("pass") is not None),
        "completed_by_mode": {
            m: sum(1 for k, c in cells.items()
                   if k.endswith(":" + m) and c.get("pass") is not None)
            for m in ("off", "safe", "balanced")
        },
        "paired_bases": sorted(full),
        "paired_n": len(full),
    }
    if full:
        agg = {m: {"pass": 0, "n": 0, "tin": 0, "tout": 0,
                   "tin_failed": 0, "n_failed": 0, "tin_passed": 0, "n_passed": 0}
               for m in ("off", "safe", "balanced")}
        diff_tasks = 0
        for modes in full.values():
            outcomes = {m: modes[m]["pass"] for m in ("off", "safe", "balanced")}
            if len(set(outcomes.values())) > 1:
                diff_tasks += 1
            for m in ("off", "safe", "balanced"):
                rec = modes[m]
                agg[m]["n"] += 1
                agg[m]["pass"] += rec["pass"]
                agg[m]["tin"] += rec["tokens_in"]
                agg[m]["tout"] += rec["tokens_out"]
                if rec["pass"]:
                    agg[m]["n_passed"] += 1
                    agg[m]["tin_passed"] += rec["tokens_in"]
                else:
                    agg[m]["n_failed"] += 1
                    agg[m]["tin_failed"] += rec["tokens_in"]
        out["paired"] = {
            m: {
                "n": agg[m]["n"],
                "pass_rate": round(agg[m]["pass"] / agg[m]["n"], 3),
                "tokens_in_avg": round(agg[m]["tin"] / agg[m]["n"]),
                "tokens_out_avg": round(agg[m]["tout"] / agg[m]["n"]),
                "tokens_in_avg_passed": (
                    round(agg[m]["tin_passed"] / agg[m]["n_passed"], 0)
                    if agg[m]["n_passed"] else None),
                "tokens_in_avg_failed": (
                    round(agg[m]["tin_failed"] / agg[m]["n_failed"], 0)
                    if agg[m]["n_failed"] else None),
            }
            for m in ("off", "safe", "balanced")
        }
        out["tasks_with_different_outcomes"] = diff_tasks
        off, bal = out["paired"]["off"], out["paired"]["balanced"]
        if off["tokens_in_avg"]:
            out["balanced_tokens_in_delta_pct"] = round(
                100 * (bal["tokens_in_avg"] - off["tokens_in_avg"]) / off["tokens_in_avg"], 1)
    return out


def audit_e5_pass_firings() -> dict:
    """Count pass firings per arm from the savings ledger (real proxy rows)."""
    try:
        import asyncio
        import os

        from app import winloop  # noqa: F401
        from app.storage.db import close_pool, open_pool
        from app.storage.migrate import apply_migrations

        async def go() -> dict:
            apply_migrations(os.environ.get(
                "DATABASE_URL", "postgresql://app:app@localhost:5433/qo"))
            pool = await open_pool(os.environ.get(
                "DATABASE_URL", "postgresql://app:app@localhost:5433/qo"))
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT mode, pass_reports, cache_hit FROM proxy_requests "
                    "WHERE created_at BETWEEN %s AND %s ORDER BY created_at",
                    AUDIT_WINDOW,
                )
                rows = await cur.fetchall()
            await close_pool()
            arms: dict[str, dict] = {}
            for _mode, reports, hit in rows:
                for r in reports or []:
                    pid = r.get("pass")
                    if pid in ("P9", "P4", "P5", "P1"):
                        a = arms.setdefault(pid, {
                            "requests_seen": 0, "applied": 0, "skipped": 0, "cache_hits": 0})
                        a["requests_seen"] += 1
                        if r.get("skipped"):
                            a["skipped"] += 1
                        elif r.get("changed") or pid == "P9":
                            a["applied"] += 1
                # cache hits are counted separately (P9 lookup fires per request)
            return {"ledger_rows": len(rows), "pass_firings": arms,
                    "window": AUDIT_WINDOW}
        return asyncio.run(go())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "e1_e2": audit_e1_e2(),
        "e3": audit_e3(),
        "e4_paired": audit_e4_paired(),
        "e5_pass_firings": audit_e5_pass_firings(),
    }
    out = RESULTS / "audit.json"
    out.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
