"""Deterministic input hashing for stage idempotency (PRD 5.8)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_VERSION = "h1"


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def input_hash(
    stage_key: str,
    prompt_version: str,
    model: str,
    config_version: str,
    upstream: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> str:
    """Hash covers stage key, upstream output hashes, prompt version, model, config (PRD 5.8)."""
    material = {
        "hash_version": HASH_VERSION,
        "stage_key": stage_key,
        "prompt_version": prompt_version,
        "model": model,
        "config_version": config_version,
        "upstream": upstream,
        "payload": payload or {},
    }
    return hashlib.sha256(canonical(material)).hexdigest()
