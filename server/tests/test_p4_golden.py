"""WP14: P4 golden regression corpus — any future optimization must produce
byte-identical output on these fixtures (else bump the pass version)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.optimizer.passes import p4_tool_output_compaction
from app.optimizer.policy import resolve_policy

GOLDEN = Path(__file__).resolve().parent / "golden" / "p4_golden.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_exists(golden: dict) -> None:
    assert set(golden) == {"ansi_small", "big_with_errors", "protected"}


@pytest.mark.parametrize("name", ["ansi_small", "big_with_errors", "protected"])
def test_p4_output_byte_identical(golden: dict, name: str) -> None:
    g = golden[name]
    req = {"messages": [dict(m) for m in g["input"]]}
    pol = resolve_policy("safe", "step 5" if name == "protected" else None)
    out, rep = p4_tool_output_compaction(req, pol)
    assert out["messages"] == g["output"]
    assert rep["changed"] == g["report"]["changed"]
    assert rep["tokens_after_est"] == g["report"]["tokens_after_est"]
    assert rep["tokens_before_est"] == g["report"]["tokens_before_est"]
