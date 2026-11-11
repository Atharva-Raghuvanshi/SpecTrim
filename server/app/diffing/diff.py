"""Version diff engine with breaking-change flags (M5.3).

Breaking: endpoint removed, endpoint method/path changed, entity removed,
required field removed, required field type changed. Everything else is
non-breaking but still reported.
"""
from __future__ import annotations

from typing import Any


def _ops(openapi: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """op_id -> (method, path)."""
    out: dict[str, tuple[str, str]] = {}
    for path, item in (openapi.get("paths") or {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method) if isinstance(item, dict) else None
            if isinstance(op, dict) and op.get("operationId"):
                out[str(op["operationId"])] = (method, path)
    return out


def _entities(package: dict) -> dict[str, dict[str, dict]]:
    """name -> {field_name: {type, required}}."""
    out: dict[str, dict[str, dict]] = {}
    for e in package.get("contract", {}).get("entities", []):
        out[e["name"]] = {
            f["name"]: {"type": f["type"], "required": f.get("required", True)}
            for f in e.get("fields", [])
        }
    return out


def diff_packages(old: dict, new: dict) -> dict:
    changes: list[dict] = []
    old_ops, new_ops = _ops(old["contract"]["openapi"]), _ops(new["contract"]["openapi"])

    for op_id, (method, path) in sorted(old_ops.items()):
        if op_id not in new_ops:
            changes.append({"kind": "endpoint_removed", "op_id": op_id, "breaking": True})
        elif new_ops[op_id] != (method, path):
            changes.append(
                {
                    "kind": "endpoint_changed",
                    "op_id": op_id,
                    "from": f"{method} {path}",
                    "to": f"{new_ops[op_id][0]} {new_ops[op_id][1]}",
                    "breaking": True,
                }
            )
    for op_id in sorted(set(new_ops) - set(old_ops)):
        changes.append({"kind": "endpoint_added", "op_id": op_id, "breaking": False})

    old_e, new_e = _entities(old), _entities(new)
    for name, fields in sorted(old_e.items()):
        if name not in new_e:
            changes.append({"kind": "entity_removed", "entity": name, "breaking": True})
            continue
        for fname, meta in sorted(fields.items()):
            if fname not in new_e[name]:
                if meta["required"]:
                    changes.append(
                        {"kind": "field_removed", "entity": name, "field": fname, "breaking": True}
                    )
                continue
            if new_e[name][fname]["type"] != meta["type"] and meta["required"]:
                changes.append(
                    {
                        "kind": "field_type_changed",
                        "entity": name,
                        "field": fname,
                        "from": meta["type"],
                        "to": new_e[name][fname]["type"],
                        "breaking": True,
                    }
                )
    for name in sorted(set(new_e) - set(old_e)):
        changes.append({"kind": "entity_added", "entity": name, "breaking": False})

    old_f = {f["id"] for f in old.get("master_spec", {}).get("features", [])}
    new_f = {f["id"] for f in new.get("master_spec", {}).get("features", [])}
    for fid in sorted(new_f - old_f):
        changes.append({"kind": "feature_added", "feature": fid, "breaking": False})
    for fid in sorted(old_f - new_f):
        changes.append({"kind": "feature_removed", "feature": fid, "breaking": False})

    return {
        "from": old.get("version"),
        "to": new.get("version"),
        "breaking": any(c["breaking"] for c in changes),
        "changes": changes,
    }
