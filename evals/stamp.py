"""Result stamps (WP13): content hashes of what affects a result.

- Proxy experiments (E3b, E4, E4b, E5): pass registry + policy floors +
  model config + prompt versions + task-set version.
- E1/E2: prompt versions + model config + label-set version ONLY (the proxy
  registry does not affect them).
- The git commit is stored as provenance and is NEVER part of the comparison
  (a docs-only commit does not invalidate cells).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts"
LABELS = ROOT / "evals" / "prompts" / "labels.jsonl"
MODELS_YAML = ROOT / "config" / "models.yaml"


def _hash_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()[:12]


def _hash_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return _hash_bytes(path.read_bytes())


def prompts_hash() -> str:
    """Hash of all prompt files (contents + versions)."""
    blob = ""
    for p in sorted(PROMPTS_DIR.rglob("v*.md")):
        blob += f"{p.relative_to(ROOT)}:{_hash_bytes(p.read_bytes())}\n"
    return _hash_bytes(blob.encode())


def labels_hash() -> str:
    return _hash_file(LABELS)


def models_hash() -> str:
    return _hash_file(MODELS_YAML)


def tasks_hash(tasks_file: str | Path) -> str:
    return _hash_file(Path(tasks_file))


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def proxy_registry_hash() -> str:
    """Hash of the active proxy pass registry + policy floors."""
    from app.optimizer.chain import REGISTRY
    from app.optimizer.policy import PASS_MODE_FLOOR, PASS_ORDER

    material = {
        "pass_order": PASS_ORDER,
        "floors": PASS_MODE_FLOOR,
        "registered": sorted(REGISTRY.keys()),
    }
    return _hash_bytes(json.dumps(material, sort_keys=True).encode())


def result_stamp(kind: str, inputs: dict) -> dict:
    """Pure stamp builder. `inputs` carries the content hashes relevant to the
    experiment kind; the commit rides along as provenance only."""
    if kind == "e1e2":
        material = {
            "kind": kind,
            "prompts": inputs["prompts"],
            "models": inputs["models"],
            "labels": inputs["labels"],
        }
    else:  # proxy experiments: e4, e4b, e5, e3b
        material = {
            "kind": kind,
            "registry": inputs["registry"],
            "models": inputs["models"],
            "prompts": inputs["prompts"],
            "tasks": inputs["tasks"],
        }
    material["version"] = "wp13"
    stamp = {"content": _hash_bytes(json.dumps(material, sort_keys=True).encode())}
    stamp["provenance_commit"] = git_commit()  # provenance only, never compared
    stamp["inputs"] = material
    return stamp


def stamp_matches(cell_stamp: dict | None, current: dict) -> bool:
    """Compare ONLY the content hash; provenance commit is ignored."""
    return bool(cell_stamp) and cell_stamp.get("content") == current.get("content")
