"""Strict-JSON completion with one repair retry (FR-11 / PRD 5.8).

First failure appends the validation error and the model's own output as a
repair instruction; a second failure is a permanent GatewayError.
"""
from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import CompletionResult, Gateway, GatewayError

T = TypeVar("T", bound=BaseModel)

REPAIR_INSTRUCTION = (
    "Your previous reply was not valid against the required schema. "
    "Reply again with STRICT JSON only (no prose, no code fences) that satisfies "
    "this validation error:\n{error}\n\nYour previous reply was:\n{previous}"
)


def extract_json(text: str) -> object:
    """Parse JSON from a model reply, tolerating code fences or surrounding prose."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def complete_validated(
    gateway: Gateway,
    stage: str,
    messages: list[dict[str, str]],
    model_cls: type[T],
) -> tuple[T, CompletionResult]:
    """Run the stage; validate strict JSON; one repair retry, then permanent error."""
    first = gateway.complete(stage, messages)
    try:
        parsed = model_cls.model_validate(extract_json(first.text))
        return parsed, first
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        first_error = _error_text(exc)

    repair = messages + [
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": REPAIR_INSTRUCTION.format(error=first_error, previous=first.text)},
    ]
    second = gateway.complete(stage, repair)
    try:
        parsed = model_cls.model_validate(extract_json(second.text))
        return parsed, second
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise GatewayError(
            f"permanent: {stage} output failed schema validation after repair retry: {_error_text(exc)}"
        ) from exc


def _error_text(exc: Exception) -> str:
    return exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
