from app.stages.analyzer import Ambiguity, IdeaAnalysis
from app.stages.classifier import Label
from app.stages.question_gate import run_question_gate


def analysis() -> IdeaAnalysis:
    return IdeaAnalysis(
        goals=["g"],
        entities=["user"],
        features=["f"],
        constraints=[],
        ambiguities=[
            Ambiguity(id="AMB1", text="Auth model?", category="auth_model"),
            Ambiguity(id="AMB2", text="Multi-user?", category="data_ownership"),
            Ambiguity(id="AMB3", text="Payments?", category="payments"),
            Ambiguity(id="AMB4", text="Web or mobile?", category="target_platform"),
            Ambiguity(id="AMB5", text="Page size?", category="behavior_detail"),
            Ambiguity(id="AMB6", text="Colors?", category="styling"),
        ],
    )


def label(amb_id: str, klass: str, confidence: float = 0.9) -> Label:
    return Label(
        **{
            "id": amb_id,
            "class": klass,
            "category": "x",
            "reason": "rule",
            "confidence": confidence,
        }
    )


def test_blocking_become_questions_defaultable_become_assumptions() -> None:
    labels = [
        label("AMB1", "blocking"),
        label("AMB2", "blocking"),
        label("AMB5", "defaultable"),
    ]
    out = run_question_gate(analysis(), labels)
    assert [q.text for q in out.questions] == ["Auth model?", "Multi-user?"]
    assert [q.id for q in out.questions] == ["Q1", "Q2"]
    assert len(out.assumptions) == 1
    assert out.assumptions[0].overridable is True


def test_cap_limits_questions_excess_become_flagged_assumptions() -> None:
    labels = [
        label("AMB1", "blocking"),
        label("AMB2", "blocking"),
        label("AMB3", "blocking"),
        label("AMB4", "blocking", confidence=0.5),
        label("AMB5", "defaultable"),
    ]
    out = run_question_gate(analysis(), labels, max_questions=3)
    assert len(out.questions) == 3
    suppressed = [a for a in out.assumptions if not a.overridable]
    assert len(suppressed) == 1
    assert suppressed[0].text == "Web or mobile?"
    assert "cap" in suppressed[0].rationale


def test_low_confidence_blocking_loses_priority_over_high() -> None:
    labels = [
        label("AMB1", "blocking", confidence=0.55),
        label("AMB2", "blocking", confidence=0.95),
        label("AMB3", "blocking", confidence=0.85),
    ]
    out = run_question_gate(analysis(), labels, max_questions=2)
    # AMB2/AMB3 (high confidence) win the two slots; AMB1 is suppressed
    assert [q.text for q in out.questions] == ["Multi-user?", "Payments?"]


def test_unknown_label_id_is_ignored() -> None:
    out = run_question_gate(analysis(), [label("AMB99", "blocking")])
    assert out.questions == [] and out.assumptions == []
