"""WP13: stamp semantics — content-hash comparison, commit as provenance only."""
from __future__ import annotations

from evals.stamp import result_stamp, stamp_matches

BASE_INPUTS = {
    "prompts": "p1",
    "models": "m1",
    "labels": "l1",
    "registry": "r1",
    "tasks": "t1",
}


def test_same_inputs_same_stamp() -> None:
    a = result_stamp("e4", BASE_INPUTS)
    b = result_stamp("e4", dict(BASE_INPUTS))
    assert stamp_matches(a, b)


def test_docs_only_commit_does_not_invalidate() -> None:
    """A docs-only commit changes provenance_commit but no content hash:
    cells must stay valid."""
    a = result_stamp("e4", dict(BASE_INPUTS))
    b = result_stamp("e4", dict(BASE_INPUTS))
    b["provenance_commit"] = "fff0001"  # docs-only commit
    a["provenance_commit"] = "aaa0001"
    assert stamp_matches(a, b)


def test_policy_floor_change_invalidates_proxy_experiments() -> None:
    a = result_stamp("e4", dict(BASE_INPUTS))
    changed = dict(BASE_INPUTS, registry="r2")  # P1 floor moved: registry hash changes
    b = result_stamp("e4", changed)
    assert not stamp_matches(a, b)


def test_proxy_registry_change_does_not_invalidate_e1e2() -> None:
    a = result_stamp("e1e2", dict(BASE_INPUTS))
    b = result_stamp("e1e2", dict(BASE_INPUTS, registry="r2"))
    assert stamp_matches(a, b)


def test_label_change_invalidates_e1e2_only() -> None:
    a = result_stamp("e1e2", dict(BASE_INPUTS))
    b = result_stamp("e1e2", dict(BASE_INPUTS, labels="l2"))
    assert not stamp_matches(a, b)
    # labels do not affect proxy experiments
    c = result_stamp("e4", dict(BASE_INPUTS))
    d = result_stamp("e4", dict(BASE_INPUTS, labels="l2"))
    assert stamp_matches(c, d)


def test_prompt_change_invalidates_both() -> None:
    a_e1 = result_stamp("e1e2", dict(BASE_INPUTS))
    a_e4 = result_stamp("e4", dict(BASE_INPUTS))
    b_e1 = result_stamp("e1e2", dict(BASE_INPUTS, prompts="p2"))
    b_e4 = result_stamp("e4", dict(BASE_INPUTS, prompts="p2"))
    assert not stamp_matches(a_e1, b_e1)
    assert not stamp_matches(a_e4, b_e4)


def test_model_change_invalidates_both() -> None:
    a = result_stamp("e1e2", dict(BASE_INPUTS))
    b = result_stamp("e1e2", dict(BASE_INPUTS, models="m2"))
    assert not stamp_matches(a, b)


def test_task_change_invalidates_proxy_only() -> None:
    a = result_stamp("e4", dict(BASE_INPUTS))
    b = result_stamp("e4", dict(BASE_INPUTS, tasks="t2"))
    assert not stamp_matches(a, b)
    a_e1 = result_stamp("e1e2", dict(BASE_INPUTS))
    b_e1 = result_stamp("e1e2", dict(BASE_INPUTS, tasks="t2"))
    assert stamp_matches(a_e1, b_e1)


def test_kind_change_invalidates() -> None:
    a = result_stamp("e4", dict(BASE_INPUTS))
    b = result_stamp("e5", dict(BASE_INPUTS))
    assert not stamp_matches(a, b)
