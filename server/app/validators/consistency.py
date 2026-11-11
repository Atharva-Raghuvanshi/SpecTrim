"""Deterministic consistency checker (FR-12): pure code, no LLM."""
from __future__ import annotations

from typing import Any

from app.schemas.package import SpecPackage


def op_ids_of(openapi: dict[str, Any]) -> set[str]:
    """All operationIds declared in an OpenAPI document."""
    ids: set[str] = set()
    for path_item in (openapi.get("paths") or {}).values():
        for method in ("get", "post", "put", "patch", "delete"):
            op = path_item.get(method) if isinstance(path_item, dict) else None
            if isinstance(op, dict) and op.get("operationId"):
                ids.add(str(op["operationId"]))
    return ids


def check_consistency(pkg: SpecPackage) -> list[str]:
    """Deterministic checks; return warnings (empty = consistent)."""
    warnings: list[str] = []
    contract_ops = op_ids_of(pkg.contract.openapi)
    entity_names = {e.name for e in pkg.contract.entities}

    for component, sub in pkg.sub_specs.items():
        for page in sub.pages:
            for op in page.endpoints:
                if op not in contract_ops:
                    warnings.append(
                        f"consistency: {component} page '{page.name}' references unknown endpoint '{op}'"
                    )
        for ep in sub.endpoints:
            for entity in ep.uses_entities:
                if entity not in entity_names:
                    warnings.append(
                        f"consistency: {component} endpoint '{ep.op_id}' references unknown entity '{entity}'"
                    )
        for entity in sub.entities:
            if entity not in entity_names:
                warnings.append(
                    f"consistency: {component} sub-spec entity '{entity}' missing from contract"
                )

    for feature in pkg.master_spec.features:
        if not feature.acceptance:
            warnings.append(f"consistency: feature '{feature.id}' has no acceptance criteria")

    return warnings
