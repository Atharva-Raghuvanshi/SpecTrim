"""Revision stage (M5.2): apply change_request / failure_report feedback."""
from __future__ import annotations

import json

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render
from app.schemas.package import MasterSpec


def run_revision(
    gateway: Gateway,
    idea: str,
    previous_spec: MasterSpec | dict,
    feedback: str,
    feedback_type: str = "change_request",
) -> tuple[MasterSpec, CompletionResult]:
    prev = (
        previous_spec.model_dump()
        if isinstance(previous_spec, MasterSpec)
        else previous_spec
    )
    _, prompt = render(
        "revision",
        idea=idea,
        previous_spec=json.dumps(prev, indent=2),
        feedback=feedback,
        feedback_type=feedback_type,
    )
    return complete_validated(
        gateway, "revision", [{"role": "user", "content": prompt}], MasterSpec
    )
