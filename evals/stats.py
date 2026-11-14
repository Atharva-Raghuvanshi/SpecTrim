"""Small exact stats helpers (WP13 S6): Wilson intervals + Fisher exact p.

No scipy — n is small (<= 48 cells per experiment).
"""
from __future__ import annotations

import math
from typing import Any


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion, as percentages."""
    if n == 0:
        return (0.0, 100.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = max(0.0, (centre - spread) / denom)
    hi = min(1.0, (centre + spread) / denom)
    return (round(100 * lo, 1), round(100 * hi, 1))


def _hypergeom_pmf(k: int, n1: int, n2: int, k_total: int) -> float:
    """P(draw k successes) in a population of n1 successes / n2 failures."""
    from math import comb

    if k < 0 or k > n1 or k_total - k > n2:
        return 0.0
    return comb(n1, k) * comb(n2, k_total - k) / comb(n1 + n2, k_total)


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]] (a = group1 successes)."""
    row1, col1 = a + b, a + c
    n = a + b + c + d
    if n == 0:
        return 1.0
    lo = max(0, col1 - (n - row1))
    hi = min(row1, col1)
    if lo > hi:
        return 1.0
    observed = a
    total = sum(
        _hypergeom_pmf(k, row1, n - row1, col1) for k in range(lo, hi + 1)
    )
    if total == 0:
        return 1.0
    return min(1.0, sum(
        _hypergeom_pmf(k, row1, n - row1, col1)
        for k in range(lo, hi + 1) if _hypergeom_pmf(k, row1, n - row1, col1) <=
        _hypergeom_pmf(observed, row1, n - row1, col1) + 1e-15
    ) / total)


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test p-value (ties excluded)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = max(wins, losses)
    total = sum(math.comb(n, i) for i in range(n + 1))
    tail = sum(math.comb(n, i) for i in range(k, n + 1))
    return min(1.0, 2 * tail / total if tail * 2 <= total else 1.0)


def paired_summary(cells: dict[str, dict], configs: list[str]) -> dict[str, Any]:
    """Paired analysis over bases where every config completed.

    Returns per-config stats (with Wilson intervals), per-task outcomes,
    sign-test / Fisher p for the primary comparison (last config vs first),
    and tokens for failed vs passed runs separately."""
    bases: dict[str, dict[str, dict]] = {}
    for cell, rec in cells.items():
        if rec.get("pass") is None:
            continue
        base, cfg = cell.rsplit(":", 1)
        if cfg in configs:
            bases.setdefault(base, {})[cfg] = rec
    full = {b: m for b, m in bases.items() if len(m) == len(configs)}

    per_config: dict[str, Any] = {}
    for cfg in configs:
        recs = [m[cfg] for m in full.values() if cfg in m]
        n = len(recs)
        k = sum(1 for r in recs if r["pass"])
        lo, hi = wilson(k, n)
        tin_p = [r["tokens_in"] for r in recs if r["pass"]]
        tin_f = [r["tokens_in"] for r in recs if not r["pass"]]
        per_config[cfg] = {
            "n": n,
            "pass_rate": round(k / n, 3) if n else None,
            "pass_rate_wilson95": [lo, hi] if n else None,
            "tokens_in_avg": round(sum(r["tokens_in"] for r in recs) / n) if n else None,
            "tokens_out_avg": round(sum(r["tokens_out"] for r in recs) / n) if n else None,
            "tokens_in_avg_passed": round(sum(tin_p) / len(tin_p)) if tin_p else None,
            "tokens_in_avg_failed": round(sum(tin_f) / len(tin_f)) if tin_f else None,
            "iterations_avg": (round(sum(r["turns"] for r in recs) / n, 2) if n else None),
        }

    outcomes: dict[str, dict[str, bool | None]] = {
        b: {cfg: (m[cfg]["pass"] if cfg in m else None) for cfg in configs}
        for b, m in full.items()
    }
    diff_tasks = sum(1 for o in outcomes.values() if len({v for v in o.values() if v is not None}) > 1)

    result: dict[str, Any] = {
        "paired_n": len(full),
        "per_config": per_config,
        "per_task_outcomes": outcomes,
        "tasks_with_different_outcomes": diff_tasks,
    }

    if len(configs) >= 2 and full:
        first, last = configs[0], configs[-1]
        wins = sum(1 for o in outcomes.values()
                   if o[last] and not o[first])
        losses = sum(1 for o in outcomes.values()
                     if o[first] and not o[last])
        a = sum(1 for o in outcomes.values() if o[last] and not o[first])
        b_ = sum(1 for o in outcomes.values() if o[last] and o[first])
        c = sum(1 for o in outcomes.values() if o[first] and not o[last])
        d_ = sum(1 for o in outcomes.values() if o[first] and o[last])
        result["comparison"] = {
            "from": first, "to": last,
            "last_won": wins, "first_won": losses, "tie": len(full) - wins - losses,
            "sign_test_p": round(sign_test(wins, losses), 4),
            "fisher_p": round(fisher_exact_2x2(a, b_, c, d_), 4),
        }
        ti = per_config[last]["tokens_in_avg"]
        tf = per_config[first]["tokens_in_avg"]
        if ti is not None and tf:
            result["comparison"]["tokens_in_delta_pct"] = round(100 * (ti - tf) / tf, 1)
    return result
