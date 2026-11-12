"""Optimization chain (M10.2): ordered passes, per-pass fail-open, reports."""
from __future__ import annotations

from typing import Any

from app.optimizer.passes import PASS_FUNCTIONS
from app.optimizer.policy import Policy

FALLBACK_REPORT = {"pass": "?", "tokens_before_est": 0, "tokens_after_est": 0,
                   "changed": False, "skipped": "error"}


def run_chain(request: dict, policy: Policy) -> tuple[dict, list[dict]]:
    """Run enabled passes in order. A pass error skips that pass only; the
    request keeps flowing (per-pass fail-open). Total failure is handled by
    the caller (router-level fail-open forwards the original request)."""
    reports: list[dict] = []
    current: dict[str, Any] = request
    for pass_id in ["P9", "P4", "P5", "P2", "P3", "P10", "P1", "P7", "P6"]:
        if not policy.allows(pass_id):
            reports.append(
                {"pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                 "changed": False, "skipped": "mode or kill switch"}
            )
            continue
        fn = PASS_FUNCTIONS.get(pass_id)
        if fn is None:  # P2/P3/P5/P6/P10 arrive in Release C
            reports.append(
                {"pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                 "changed": False, "skipped": "not implemented in this release"}
            )
            continue
        try:
            current, report = fn(current, policy)
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 - one pass must not break the chain
            reports.append({**FALLBACK_REPORT, "pass": pass_id,
                            "skipped": f"error: {type(exc).__name__}: {exc}"[:120]})
    return current, reports
