"""Deterministic question gate (FR-6, PRD 5.4). No LLM.

Blocking ambiguities become questions, capped per round. Low-confidence
blocking items only make the cut if the cap allows; everything else becomes
a flagged assumption. Defaultable items always become overridable assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.package import Assumption, Question
from app.stages.analyzer import IdeaAnalysis
from app.stages.classifier import Label

LOW_CONFIDENCE = 0.6


@dataclass
class GateOutcome:
    questions: list[Question] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)

    @property
    def suppressed_ids(self) -> list[str]:
        return [a.id.replace("A", "AMB", 1) for a in self.assumptions if a.category == "suppressed_blocking"]


def run_question_gate(
    analysis: IdeaAnalysis, labels: list[Label], max_questions: int = 3
) -> GateOutcome:
    by_id = {a.id: a for a in analysis.ambiguities}
    blocking = sorted(
        (lb for lb in labels if lb.label_class == "blocking"),
        key=lambda lb: (-lb.confidence, lb.id),
    )
    defaultable = [lb for lb in labels if lb.label_class == "defaultable"]

    outcome = GateOutcome()
    q_n = 0
    a_n = 0
    for label in blocking:
        amb = by_id.get(label.id)
        if amb is None:
            continue
        if len(outcome.questions) < max_questions:
            q_n += 1
            outcome.questions.append(
                Question(id=f"Q{q_n}", text=amb.text, category=label.category)
            )
        else:  # cap reached: blocking item becomes a flagged, non-overridable assumption
            a_n += 1
            outcome.assumptions.append(
                Assumption(
                    id=f"A{a_n}",
                    text=amb.text,
                    rationale=(
                        f"blocking but question cap reached; "
                        f"confidence {label.confidence:.2f}: {label.reason}"
                    ),
                    category="suppressed_blocking",
                    overridable=False,
                )
            )
    for label in defaultable:
        amb = by_id.get(label.id)
        if amb is None:
            continue
        a_n += 1
        outcome.assumptions.append(
            Assumption(
                id=f"A{a_n}",
                text=amb.text,
                rationale=label.reason,
                category=label.category,
                overridable=True,
            )
        )
    return outcome
