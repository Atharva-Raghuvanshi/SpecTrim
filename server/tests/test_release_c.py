"""Release C: P5/P6/P3/P2 passes, P8 cascade, P11 governor + breakers."""
from __future__ import annotations

from app.optimizer.passes_c import (
    estimate_saved_tokens,
    p2_spec_as_context,
    p3_context_selection,
    p5_history_compaction,
    p6_edit_format_enforcement,
)
from app.optimizer.policy import resolve_policy
from app.optimizer.routing import CHEAP, STRONG, BreakerBoard, BudgetGovernor, route


def pol() -> object:
    return resolve_policy("balanced", None)


def test_p5_keeps_decisions_and_open_errors() -> None:
    msgs = (
        [{"role": "system", "content": "sys"}]
        + [{"role": "user", "content": f"old turn {i}: nothing important"} for i in range(10)]
        + [{"role": "tool", "content": "DECISION: we will use Postgres row-level locks"}]
        + [{"role": "tool", "content": "open error: TypeError in parser.py"}]
        + [{"role": "assistant", "content": "noted"}]
        + [{"role": "user", "content": "continue"}]
    )
    out, report = p5_history_compaction({"messages": msgs}, pol())
    text = " ".join(str(m["content"]) for m in out["messages"])
    assert "DECISION: we will use Postgres row-level locks" in text
    assert "open error: TypeError in parser.py" in text
    assert "old turn 1" not in text  # superseded turns dropped
    assert report["tokens_after_est"] < report["tokens_before_est"]


def test_p6_rejects_whole_file_rewrite_and_instructs_retry() -> None:
    whole_file = "```python\n" + "\n".join(f"line {n} = def f{n}(): pass" for n in range(60)) + "\n```"
    msgs = [
        {"role": "assistant", "content": whole_file},
        {"role": "user", "content": "apply that edit"},
    ]
    out, report = p6_edit_format_enforcement({"messages": msgs}, pol())
    text = " ".join(str(m["content"]) for m in out["messages"])
    assert "rejected: use SEARCH/REPLACE blocks" in text  # retry marker injected
    assert "SEARCH" in text and "REPLACE" in text
    assert report["pass"] == "P6"


def test_p6_leaves_proper_diff_output_alone() -> None:
    msgs = [
        {"role": "assistant", "content": "<<<<<<< SEARCH\nfoo()\n=======\nbar()\n>>>>>>> REPLACE"},
        {"role": "user", "content": "next"},
    ]
    out, report = p6_edit_format_enforcement({"messages": msgs}, pol())
    assert "rejected" not in " ".join(str(m["content"]) for m in out["messages"])


def test_p3_bm25_finds_relevant_file() -> None:
    index = {
        "auth/login.py": "login authenticate user password session",
        "billing/invoice.py": "invoice charge payment amount",
        "ui/button.py": "button click styling",
    }
    req = {"messages": [{"role": "user", "content": "fix the invoice charge amount calculation"}]}
    out, report = p3_context_selection(req, pol(), index)
    text = str(out["messages"][-1]["content"])
    assert "billing/invoice.py" in text  # relevant file found on a known task
    assert "auth/login.py" not in text.split(",")[0] or "invoice" in text
    assert report["changed"] is True


def test_p2_sends_only_relevant_subspec() -> None:
    pkg = {
        "version": 2,
        "sub_specs": {
            "backend": {"entities": ["Habit"], "endpoints": [{"op_id": "createHabit"}]},
            "frontend": {"entities": [], "endpoints": []},
        },
    }
    req = {"messages": [{"role": "user", "content": "[IDEA] habit tracker with lots of detail here"}]}
    out, report = p2_spec_as_context(req, pol(), pkg, role="backend")
    content = str(out["messages"][0]["content"])
    assert "createHabit" in content and "[SPEC v2 backend]" in content
    assert "habit tracker with lots of detail" not in content  # raw idea replaced


def test_p8_cascade_escalates_on_validator_failure() -> None:
    assert route([{"role": "user", "content": "schema validation failed: bad id"}]) == STRONG
    assert route([{"role": "user", "content": "tests failed on module x"}]) == STRONG
    assert route([{"role": "user", "content": "list my todos"}]) == CHEAP  # cheap-first


def test_p8_hard_steps_go_strong() -> None:
    assert route([{"role": "user", "content": "design the payment migration"}]) == STRONG


def test_p11_governor_degrades_before_refusing() -> None:
    gov = BudgetGovernor(session_budget_usd=0.50)
    gov.observe(0.30)
    assert gov.allow() and gov.degraded is False
    gov.observe(0.15)  # 0.45 spent >= 80% of 0.50
    assert gov.degraded is True
    assert gov.model_override(STRONG) == CHEAP  # degrade, don't refuse
    assert gov.allow() is True  # still under budget
    gov.observe(0.10)  # now over budget
    assert gov.allow() is False


def test_breaker_opens_past_threshold() -> None:
    board = BreakerBoard(threshold=3)
    b = board.breaker("P4")
    assert b.allow()
    for _ in range(3):
        b.record_failure()
    assert not b.allow()  # breaker opens past the failure threshold
    assert "P4" in board.disabled_passes()


def test_breaker_half_open_after_window() -> None:
    board = BreakerBoard(threshold=1)
    b = board.breaker("P1")
    b.window_s = 0.0
    b.record_failure()
    import time

    time.sleep(0.05)
    assert b.allow()  # half-open retry after the window


def test_estimate_saved_tokens_sums_positive_only() -> None:
    reports = [
        {"tokens_before_est": 100, "tokens_after_est": 60, "skipped": ""},
        {"tokens_before_est": 50, "tokens_after_est": 70, "skipped": ""},
        {"tokens_before_est": 10, "tokens_after_est": 5, "skipped": "mode"},
    ]
    assert estimate_saved_tokens(reports) == 40
