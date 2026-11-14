"""Result-file stamps (WP12): git commit + active pass-registry hash.

Cells with a different stamp than the current code must not be paired or
averaged together — the runner re-runs them.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def registry_hash() -> str:
    """Hash the active pass registry/config (order, floors, registered fns)."""
    from app.optimizer.chain import REGISTRY
    from app.optimizer.policy import PASS_MODE_FLOOR, PASS_ORDER

    material = {
        "pass_order": PASS_ORDER,
        "floors": PASS_MODE_FLOOR,
        "registered": sorted(REGISTRY.keys()),
    }
    blob = json.dumps(material, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def get_stamp() -> dict:
    return {"commit": git_commit(), "registry": registry_hash()}


def stamp_matches(cell_stamp: dict | None, current: dict) -> bool:
    return bool(cell_stamp) and cell_stamp.get("commit") == current.get("commit") \
        and cell_stamp.get("registry") == current.get("registry")
