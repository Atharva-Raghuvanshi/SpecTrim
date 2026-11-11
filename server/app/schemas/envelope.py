"""Message envelope (PRD 5.5): internal and MCP message framing."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MSG_ID_RE = re.compile(r"^msg_\d+$")
SCHEMA_VERSION = "1.0"


class MessageType(str, Enum):
    request = "request"
    ambiguity_report = "ambiguity_report"
    question_to_user = "question_to_user"
    answer = "answer"
    spec = "spec"
    sub_spec = "sub_spec"
    contract = "contract"
    change_request = "change_request"
    failure_report = "failure_report"


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    from_: str = Field(alias="from")
    to: str
    type: MessageType
    schema_version: str = SCHEMA_VERSION
    payload: dict[str, Any]

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not MSG_ID_RE.match(v):
            raise ValueError(f"id must match ^msg_\\d+$, got {v!r}")
        return v

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}, got {v!r}")
        return v

    @field_validator("from_", "to")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("from/to must be non-empty")
        return v

    @field_validator("payload")
    @classmethod
    def _payload_non_empty(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            raise ValueError("payload must be a non-empty object")
        return v
