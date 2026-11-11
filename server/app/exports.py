"""Export formats (M6.3): JSON, Markdown, openapi.yaml."""
from __future__ import annotations

from typing import Any


def to_markdown(package: dict[str, Any]) -> str:
    ms = package["master_spec"]
    lines = [f"# Spec v{package['version']}: {package['session_id']}", "", ms["summary"], ""]
    if ms.get("user_flows"):
        lines += ["## User flows", ""]
        lines += [f"- {f}" for f in ms["user_flows"]] + [""]
    lines += ["## Features", ""]
    for f in ms["features"]:
        lines.append(f"### {f['id']}: {f['title']}")
        lines += [f"- [ ] {a}" for a in f["acceptance"]] + [""]
    if package.get("assumptions"):
        lines += ["## Assumptions", ""]
        for a in package["assumptions"]:
            over = "overridable" if a.get("overridable") else "flagged"
            lines.append(f"- **{a['id']}** ({a['category']}, {over}): {a['text']} — {a['rationale']}")
        lines.append("")
    contract = package.get("contract", {})
    lines += [f"## Contract v{contract.get('version')}", ""]
    for e in contract.get("entities", []):
        fields = ", ".join(f["name"] for f in e["fields"])
        lines.append(f"- entity `{e['name']}`: {fields}")
    lines.append("")
    for path, item in (contract.get("openapi", {}).get("paths") or {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if isinstance(op, dict):
                lines.append(f"- `{method.upper()} {path}` → {op.get('operationId')}")
    if package.get("diff_from_previous"):
        d = package["diff_from_previous"]
        lines += ["", f"## Diff from v{d.get('from')} to v{d.get('to')}", ""]
        if d.get("breaking"):
            lines.append(f"**BREAKING CHANGES** ({sum(1 for c in d['changes'] if c['breaking'])})")
        lines += [f"- {'⚠️ ' if c['breaking'] else ''}{c['kind']}: {c}" for c in d["changes"]]
    return "\n".join(lines) + "\n"


def to_openapi_yaml(package: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(package["contract"]["openapi"], sort_keys=False)
