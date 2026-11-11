"""Master Spec Writer stage (FR-8)."""
from __future__ import annotations

import json

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render
from app.schemas.package import MasterSpec


def run_spec_writer(
    gateway: Gateway,
    idea: str,
    answers: list[str],
    assumptions: list[dict],
) -> tuple[MasterSpec, CompletionResult]:
    _, prompt = render(
        "spec_writer",
        idea=idea,
        answers=json.dumps(answers, indent=2),
        assumptions=json.dumps(assumptions, indent=2),
    )
    return complete_validated(
        gateway, "spec_writer", [{"role": "user", "content": prompt}], MasterSpec
    )
