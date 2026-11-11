"""Spec package schemas (PRD 5.5): the validated output of the pipeline."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SESSION_ID_RE = re.compile(r"^ses_[a-z0-9]+$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Feature(Strict):
    id: str = Field(pattern=r"^F\d+$")
    title: str = Field(min_length=1)
    acceptance: list[str] = Field(min_length=1)


class MasterSpec(Strict):
    summary: str = Field(min_length=1)
    user_flows: list[str] = []
    features: list[Feature]
    non_functional: list[str] = []


class Assumption(Strict):
    id: str = Field(pattern=r"^A\d+$")
    text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    category: str = Field(min_length=1)
    overridable: bool = True


class Question(Strict):
    id: str = Field(pattern=r"^Q\d+$")
    text: str = Field(min_length=1)
    category: str = Field(min_length=1)
    answer: str | None = None


class PageRef(Strict):
    name: str = Field(min_length=1)
    endpoints: list[str] = []  # operationIds in the contract


class EndpointSpec(Strict):
    op_id: str = Field(min_length=1)
    method: str = Field(pattern=r"^(get|post|put|patch|delete)$")
    path: str = Field(pattern=r"^/")
    uses_entities: list[str] = []
    description: str = ""


class SubSpec(Strict):
    component: str = Field(pattern=r"^(frontend|backend|database|supporting)$")
    pages: list[PageRef] = []
    endpoints: list[EndpointSpec] = []
    entities: list[str] = []
    contract_version: int = Field(ge=1)


class FieldSpec(Strict):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    required: bool = True


class ContractEntity(Strict):
    name: str = Field(min_length=1)
    fields: list[FieldSpec] = Field(min_length=1)


class Contract(Strict):
    version: int = Field(ge=1)
    openapi: dict[str, Any]  # full OpenAPI 3.1 document
    entities: list[ContractEntity]


class ValidationReport(Strict):
    schema_ok: bool = True
    openapi_lint_ok: bool = True
    consistency_ok: bool = True
    warnings: list[str] = []


class SpecPackage(Strict):
    session_id: str
    version: int = Field(ge=1)
    master_spec: MasterSpec
    assumptions: list[Assumption] = []
    open_questions: list[Question] = []
    sub_specs: dict[str, SubSpec]
    contract: Contract
    validation: ValidationReport
    diff_from_previous: dict[str, Any] | None = None

    @field_validator("session_id")
    @classmethod
    def _session_shape(cls, v: str) -> str:
        if not SESSION_ID_RE.match(v):
            raise ValueError(f"session_id must match ^ses_[a-z0-9]+$, got {v!r}")
        return v
