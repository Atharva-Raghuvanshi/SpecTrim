"""OpenAPI linter (FR-13): thin wrapper over openapi-spec-validator."""
from __future__ import annotations

from typing import Any


def lint_openapi(openapi: dict[str, Any]) -> list[str]:
    """Validate an OpenAPI document; return a list of error strings (empty = valid)."""
    try:
        from openapi_spec_validator import validate

        validate(openapi)
    except Exception as exc:  # noqa: BLE001 - linter failure is data, not a crash
        return [f"openapi_lint: {exc}"]
    return []
