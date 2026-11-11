"""Irreversibility Classifier stage (FR-5)."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render
from app.stages.analyzer import IdeaAnalysis


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^AMB\d+$")
    label_class: str = Field(alias="class", pattern=r"^(blocking|defaultable)$")
    category: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ClassifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[Label] = []


def run_classifier(
    gateway: Gateway, analysis: IdeaAnalysis
) -> tuple[ClassifierOutput, CompletionResult]:
    _, prompt = render(
        "classifier", ambiguities=json.dumps([a.model_dump() for a in analysis.ambiguities], indent=2)
    )
    return complete_validated(
        gateway, "classifier", [{"role": "user", "content": prompt}], ClassifierOutput
    )
