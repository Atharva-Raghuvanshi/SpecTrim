"""Optimization passes (M11.x). Pure functions: request -> (request, report).

Rules (PRD v2 §5): never modify protected content; the last user message is
always protected; every pass is independently killable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from app.optimizer.policy import Policy, content_is_protected

ERROR_LINE = re.compile(
    r"\b(error|ERROR|Error|failed|FAILED|Failure|Traceback|Exception|FATAL|panic)\b|^\s*at .+\(.+:\d+\)"
)
# C-speed multiline scan: whole error lines in one pass (perf: P4 on big logs)
ERROR_LINES_ML = re.compile(
    r"^.*(?:error|ERROR|Error|failed|FAILED|Failure|Traceback|Exception|FATAL|panic).*$"
    r"|^\s*at .+\(.+:\d+\).*$",
    re.MULTILINE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # estimate; labelled _est everywhere


def _last_user_index(messages: list[dict]) -> int:
    idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            idx = i
    return idx


def _report(pass_id: str, before: int, after: int, changed: bool, skipped: str = "") -> dict:
    return {
        "pass": pass_id,
        "tokens_before_est": before,
        "tokens_after_est": after,
        "changed": changed,
        "skipped": skipped,
    }


def _msg_tokens(messages: list[dict]) -> int:
    return sum(_est_tokens(str(m.get("content", ""))) for m in messages)


def p4_tool_output_compaction(request: dict, policy: Policy) -> tuple[dict, dict]:
    """Strip ANSI, dedupe repeats, keep head+tail and ALL error lines (M11.2)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    changed = False
    out: list[dict] = []
    last_user = _last_user_index(messages)
    for i, m in enumerate(messages):
        content = m.get("content")
        if (
            m.get("role") != "tool"
            or not isinstance(content, str)
            or i == last_user
            or content_is_protected(content, policy)
        ):
            out.append(m)
            continue
        text = ANSI_RE.sub("", content)
        lines = text.splitlines()
        if len(lines) > 40:
            error_lines = [m.group(0) for m in ERROR_LINES_ML.finditer(text)]
            head, tail = lines[:10], lines[-10:]
            kept = list(dict.fromkeys(head + tail + error_lines))  # errors always kept
            new_text = "\n".join(kept) + f"\n[{len(lines) - len(kept)} lines compacted]"
        else:
            seen: set[str] = set()
            deduped: list[str] = []
            for line in lines:
                key = line.strip()
                if key and key in seen and not ERROR_LINE.search(line):
                    continue
                seen.add(key)
                deduped.append(line)
            new_text = "\n".join(deduped)
        if new_text != content:
            changed = True
        out.append({**m, "content": new_text})
    after = _msg_tokens(out)
    return ({**request, "messages": out} if changed else request), _report("P4", before, after, changed)


def _units(messages: list[dict]) -> list[list[dict]]:
    """Group messages into atomic units: every assistant-with-tool_calls keeps
    the tool messages that immediately follow it (WP12: tool pairs are never
    separated by reordering)."""
    units: list[list[dict]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            unit = [m]
            i += 1
            while i < len(messages) and messages[i].get("role") == "tool":
                unit.append(messages[i])
                i += 1
            units.append(unit)
        else:
            units.append([m])
            i += 1
    return units


def p1_cache_aware_assembly(request: dict, policy: Policy) -> tuple[dict, dict]:
    """Stable prefix first, volatile last; the latest user instruction is always
    the very last message; assistant+tool pairs are atomic (WP12); byte-stable
    for identical inputs (M11.4)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    last_user = _last_user_index(messages)
    units = _units(messages)
    reordered_units: list[list[dict]] = []
    tail: list[dict] = []
    for unit in units:
        first_role = unit[0].get("role")
        if first_role == "system":
            reordered_units.append(unit)
        elif unit is messages[last_user:last_user + 1] or (
            last_user >= 0 and last_user < len(messages)
            and messages[last_user] in unit
        ):
            tail = unit  # the unit holding the latest user instruction goes last
        else:
            reordered_units.append(unit)
    reordered = [m for u in reordered_units for m in u] + [m for u in [tail] for m in u]
    changed = reordered != messages
    request2 = {**request, "messages": reordered} if changed else request
    return request2, _report("P1", before, _msg_tokens(reordered), changed)


def canonical_prefix_bytes(request: dict) -> bytes:
    """Byte-stable serialization of the stable prefix (system + early context)."""
    messages = request.get("messages", [])
    last_user = _last_user_index(messages)
    prefix = [
        m for i, m in enumerate(messages)
        if m.get("role") == "system" or (i != last_user and m.get("role") in ("user", "assistant"))
    ]
    return json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()


def p7_output_controls(request: dict, policy: Policy, stage_cfg: dict | None = None) -> tuple[dict, dict]:
    """max_tokens / stop sequences per stage (M11.3); stage via metadata hint."""
    before = _est_tokens(json.dumps(request.get("metadata", {})))
    meta = request.get("metadata") or {}
    stage = str(meta.get("optimizer_stage", "default"))
    cfg = stage_cfg or {}
    max_tokens = cfg.get(stage, {}).get("max_tokens") or cfg.get("default", {}).get("max_tokens")
    stop = cfg.get(stage, {}).get("stop") or cfg.get("default", {}).get("stop")
    request2 = dict(request)
    changed = False
    if max_tokens and "max_tokens" not in request2:
        request2["max_tokens"] = max_tokens
        changed = True
    if stop and "stop" not in request2:
        request2["stop"] = stop
        changed = True
    style = "Respond directly: no preamble, no restating the plan."
    if isinstance(request2.get("messages"), list) and request2["messages"]:
        msgs = [dict(m) for m in request2["messages"]]
        first_system = next((m for m in msgs if m.get("role") == "system"), None)
        if first_system is None:
            msgs.insert(0, {"role": "system", "content": style})
        elif style not in str(first_system.get("content", "")):
            first_system["content"] = f"{style}\n{first_system['content']}"
        request2["messages"] = msgs
        changed = True
    return request2, _report("P7", before, before, changed)


def cache_key(model: str, request: dict, cache_version: int) -> str:
    """Key = model + normalized request + cache version + namespace.

    Namespace (QO_CACHE_NS env) is bumped when the optimizer/prompt/spec
    version changes: all old entries become unreachable (FR-37)."""
    material = {
        "ns": os.environ.get("QO_CACHE_NS", "v1"),
        "model": model,
        "cache_version": cache_version,
        "messages": request.get("messages", []),
        "tools": request.get("tools", []),
        "max_tokens": request.get("max_tokens"),
        "stop": request.get("stop"),
        "temperature": request.get("temperature"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


PASS_FUNCTIONS = {
    "P4": p4_tool_output_compaction,
    "P1": p1_cache_aware_assembly,
    "P7": p7_output_controls,
}
