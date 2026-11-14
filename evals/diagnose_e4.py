"""WP11 E4 diagnosis — offline, no LLM calls.

1. Per-cell table from the saved E4 JSON for the paired (task, rep) bases.
2. Mechanism proof: run_chain on a tool-loop message sequence shows P1's
   reordering moves tool messages before their assistant tool_calls message,
   which violates the OpenAI tool-message ordering contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.optimizer.chain import run_chain  # noqa: E402
from app.optimizer.policy import resolve_policy  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"
PAIRED_BASES = ["T1:rep0", "T1:rep1", "T2:rep0", "T2:rep1", "T4:rep0", "T4:rep1"]


def per_cell_table(d: dict) -> list[dict]:
    rows = []
    for base in PAIRED_BASES:
        for mode in ("off", "safe", "balanced"):
            c = d["cells"].get(f"{base}:{mode}")
            if c and c.get("pass") is not None:
                rows.append({
                    "cell": f"{base}:{mode}", "turns": c["turns"],
                    "tokens_in": c["tokens_in"], "tokens_out": c["tokens_out"],
                    "tokens_per_turn": round(c["tokens_in"] / max(c["turns"], 1)),
                    "pass": c["pass"],
                })
            else:
                rows.append({"cell": f"{base}:{mode}", "turns": None,
                             "tokens_in": None, "tokens_out": None,
                             "tokens_per_turn": None, "pass": None})
    return rows


def mechanism_demo() -> dict:
    """Reconstruct a mid-loop tool sequence and show P1's reordering."""
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "write the T1 calculator"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "run_tests", "arguments": "{}"}}]},
        {"role": "tool", "content": "2 passed\n1 FAILED", "tool_call_id": "call_1"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_2", "type": "function",
                         "function": {"name": "submit_code",
                                      "arguments": "{\"code\": \"...\"}"}}]},
        {"role": "user", "content": "continue"},
    ]
    ordered_messages, reports = run_chain({"messages": messages}, resolve_policy("safe", None))
    roles = [m["role"] for m in ordered_messages["messages"]]
    # contract check: every tool message must directly follow the assistant
    # message that carries its tool_call id
    violations = []
    for i, m in enumerate(ordered_messages["messages"]):
        if m["role"] == "tool":
            prev = ordered_messages["messages"][i - 1]
            prev_ids = [
                tc.get("id") for tc in (prev.get("tool_calls") or [])
            ] if prev.get("role") == "assistant" else []
            if m.get("tool_call_id") not in prev_ids:
                violations.append({
                    "index": i, "tool_call_id": m.get("tool_call_id"),
                    "prev_role": prev.get("role"),
                    "prev_tool_call_ids": prev_ids,
                })
    return {
        "input_roles": roles,
        "ordering_violations": violations,
        "valid_openai_ordering": not violations,
        "passes_applied": [
            {"pass": r["pass"], "changed": r["changed"], "skipped": r["skipped"]}
            for r in reports if not r.get("skipped")
        ],
    }


def main() -> int:
    files = [f for f in sorted(RESULTS.glob("e4_*.json")) if "diagnosis" not in f.name]
    d = json.loads(files[0].read_text(encoding="utf-8"))
    rows = per_cell_table(d)
    demo = mechanism_demo()
    out = {"per_cell": rows, "mechanism": demo}
    (RESULTS / "e4_diagnosis.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{'cell':24s} {'turns':>5s} {'tok_in':>7s} {'tok_out':>7s} {'tok/turn':>8s} pass")
    for r in rows:
        print(f"{r['cell']:24s} {str(r['turns']):>5s} {str(r['tokens_in']):>7s} "
              f"{str(r['tokens_out']):>7s} {str(r['tokens_per_turn']):>8s} {r['pass']}")
    print("\nmechanism demo (safe chain on a mid-loop tool sequence):")
    print("  reordered roles:", demo["input_roles"])
    print("  openai ordering valid:", demo["valid_openai_ordering"])
    print("  violations:", demo["ordering_violations"])
    print("saved -> e4_diagnosis.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
