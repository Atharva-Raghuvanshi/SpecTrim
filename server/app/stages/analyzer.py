"""Ambiguity Analyzer stage (FR-4)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render


class Ambiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^AMB\d+$")
    text: str = Field(min_length=1)
    category: str = Field(min_length=1)


class IdeaAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goals: list[str] = []
    entities: list[str] = []
    features: list[str] = []
    constraints: list[str] = []
    ambiguities: list[Ambiguity] = []


def run_analyzer(gateway: Gateway, idea: str) -> tuple[IdeaAnalysis, CompletionResult]:
    _, prompt = render("analyzer", idea=idea)
    return complete_validated(
        gateway, "analyzer", [{"role": "user", "content": prompt}], IdeaAnalysis
    )
