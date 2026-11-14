"""Final request-validity guard (WP12): structural checks on the internal
payload after every pass. A violation means a pass broke the request — the
chain reverts that pass and the router forwards the ORIGINAL request."""
from __future__ import annotations

VALID_ROLES = ("system", "user", "assistant", "tool")


def validate_internal(payload: dict) -> list[str]:
    """Validate an OpenAI-style payload. Returns a list of violations (empty = valid)."""
    violations: list[str] = []
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return ["messages missing or empty"]
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            violations.append(f"message {i}: not an object")
            continue
        role = m.get("role")
        if not role:
            violations.append(f"message {i}: empty role")
            continue
        if role not in VALID_ROLES:
            violations.append(f"message {i}: unknown role {role!r}")
            continue
        content = m.get("content")
        if role in ("system", "user", "tool"):
            if content is None or (isinstance(content, str) and not content.strip()):
                violations.append(f"message {i}: empty {role} content")
        if role == "tool":
            # walk back over consecutive tool messages to the owning assistant
            j = i - 1
            while j >= 0 and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
                j -= 1
            prev = messages[j] if j >= 0 else None
            prev_ids = [
                tc.get("id") for tc in (prev.get("tool_calls") or [])
            ] if isinstance(prev, dict) and prev.get("role") == "assistant" else []
            if m.get("tool_call_id") not in prev_ids:
                violations.append(
                    f"message {i}: tool_call_id {m.get('tool_call_id')!r} does not "
                    f"match the preceding assistant tool_calls {prev_ids}"
                )
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if isinstance(tc, dict) and tc.get("id"):
                    pass  # ids are checked from the tool side
                else:
                    violations.append(f"message {i}: tool_call missing id")
    return violations


def validate_anthropic_messages(body: dict) -> list[str]:
    """Anthropic raw-body alternation rules: first message user, strict
    user/assistant alternation, no empty content."""
    violations: list[str] = []
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return ["messages missing or empty"]
    expected = "user"
    for i, m in enumerate(messages):
        role = m.get("role")
        if role != expected:
            violations.append(f"message {i}: anthropic alternation expects {expected}, got {role!r}")
        expected = "assistant" if role == "user" else "user"
        content = m.get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            violations.append(f"message {i}: empty content")
    return violations
