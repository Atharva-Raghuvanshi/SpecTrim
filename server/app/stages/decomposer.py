"""Decomposer stage (FR-9): sub-specs per component referencing the contract.

Backend endpoint method/path are normalized deterministically from the
contract so sub-specs can never drift from it.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.gateway.base import CompletionResult, Gateway
from app.gateway.repair import complete_validated
from app.pipeline.prompts import render
from app.schemas.package import Contract, MasterSpec, SubSpec
from app.validators.consistency import op_ids_of


class SubSpecsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub_specs: dict[str, SubSpec]


def run_decomposer(
    gateway: Gateway, master_spec: MasterSpec, contract: Contract
) -> tuple[dict[str, SubSpec], CompletionResult]:
    ops = op_ids_of(contract.openapi)
    _, prompt = render(
        "decomposer",
        master_spec=master_spec.model_dump_json(),
        op_ids=", ".join(sorted(ops)),
        entities=", ".join(e.name for e in contract.entities),
        contract_version=str(contract.version),
    )
    out, res = complete_validated(
        gateway, "decomposer", [{"role": "user", "content": prompt}], SubSpecsOutput
    )
    # deterministic normalization: copy method/path from the contract by op_id
    contract_ops: dict[str, tuple[str, str]] = {}
    for path, item in contract.openapi.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if isinstance(op, dict) and op.get("operationId"):
                contract_ops[str(op["operationId"])] = (method, path)
    for sub in out.sub_specs.values():
        for ep in sub.endpoints:
            if ep.op_id in contract_ops:
                ep.method, ep.path = contract_ops[ep.op_id]
    return out.sub_specs, res
