"""Optimization chain (M10.2 + WP12): ordered passes, per-pass fail-open,
final request-validity guard. A pass whose output is invalid is reverted and
named; the caller then forwards the ORIGINAL request."""
from __future__ import annotations

from typing import Any

from app.optimizer.passes import PASS_FUNCTIONS
from app.optimizer.passes_c import (
    p2_spec_as_context,
    p3_context_selection,
    p5_history_compaction,
    p6_edit_format_enforcement,
)
from app.optimizer.policy import Policy
from app.optimizer.validate import validate_internal

FALLBACK_REPORT = {"pass": "?", "tokens_before_est": 0, "tokens_after_est": 0,
                   "changed": False, "skipped": "error"}

# Registry: every pass the policy may name, implemented and registered.
REGISTRY: dict[str, Any] = {
    **PASS_FUNCTIONS,
    "P5": p5_history_compaction,
    "P2": p2_spec_as_context,
    "P3": p3_context_selection,
    "P6": p6_edit_format_enforcement,
}
# P10 (prose compression) is intentionally unimplemented (PRD: aggressive-only,
# must be justified by evaluation first).
KNOWN_UNIMPLEMENTED = {"P10"}


def run_chain(
    request: dict, policy: Policy, validator: Any = validate_internal
) -> tuple[dict, list[dict]]:
    """Run enabled passes in order.

    - A pass error skips that pass only (per-pass fail-open).
    - WP12 validity guard: after each pass the payload is validated; on a
      violation the pass is reverted, named ('invalid_after_pass') and the
      chain stops applying further passes.
    - If the ORIGINAL request is already invalid, no passes run at all.
    """
    reports: list[dict] = []
    current: dict[str, Any] = request
    original_invalid = bool(validator(request))
    for pass_id in ["P9", "P4", "P5", "P2", "P3", "P10", "P1", "P7", "P6"]:
        if original_invalid:
            reports.append(
                {"pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                 "changed": False, "skipped": "invalid_original: skipping chain"}
            )
            continue
        if not policy.allows(pass_id):
            reports.append(
                {"pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                 "changed": False, "skipped": "mode or kill switch"}
            )
            continue
        fn = REGISTRY.get(pass_id)
        if fn is None:
            reports.append(
                {"pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                 "changed": False, "skipped": "not implemented in this release"}
            )
            continue
        snapshot = current
        try:
            current, report = fn(current, policy)
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 - one pass must not break the chain
            current = snapshot
            reports.append({**FALLBACK_REPORT, "pass": pass_id,
                            "skipped": f"error: {type(exc).__name__}: {exc}"[:120]})
            continue
        violations = validator(current)
        if violations:
            current = snapshot  # revert the offending pass
            marker = {
                "pass": pass_id, "tokens_before_est": 0, "tokens_after_est": 0,
                "changed": False,
                "skipped": f"invalid_after_pass: {violations[0]}"[:160],
                "invalid_after_pass": True,
            }
            if reports and reports[-1].get("pass") == pass_id:
                reports[-1]["invalid_after_pass"] = True  # stamp the pass's own report
            reports.append(marker)
            break  # a broken pass invalidates everything after it
    return current, reports
