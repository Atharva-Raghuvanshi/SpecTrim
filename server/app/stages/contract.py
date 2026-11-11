"""Contract Generator stage (FR-10): LLM proposes entities+endpoints, the
OpenAPI 3.1 document is built deterministically so it always lints clean."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render
from app.schemas.package import Contract, ContractEntity


class EndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(min_length=1)
    method: str = Field(pattern=r"^(get|post|put|patch|delete)$")
    path: str = Field(pattern=r"^/")
    uses_entities: list[str] = []
    description: str = ""


class ContractDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    entities: list[ContractEntity]
    endpoints: list[EndpointDraft] = []


def build_openapi(draft: ContractDraft, version: int) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for ep in draft.endpoints:
        path_item = paths.setdefault(ep.path, {})
        if not path_item:
            params = re.findall(r"\{(\w+)\}", ep.path)
            if params:
                path_item["parameters"] = [
                    {"name": p, "in": "path", "required": True, "schema": {"type": "string"}}
                    for p in params
                ]
        path_item[ep.method] = {
            "operationId": ep.op_id,
            "summary": ep.description or ep.op_id,
            "responses": {"200": {"description": "Success"}},
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": draft.title, "version": str(version)},
        "paths": paths,
    }


def run_contract(
    gateway: Gateway, master_spec: dict, version: int = 1
) -> tuple[Contract, CompletionResult]:
    _, prompt = render("contract", master_spec=json.dumps(master_spec, indent=2))
    draft, res = complete_validated(
        gateway, "contract", [{"role": "user", "content": prompt}], ContractDraft
    )
    contract = Contract(
        version=version, openapi=build_openapi(draft, version), entities=draft.entities
    )
    return contract, res
