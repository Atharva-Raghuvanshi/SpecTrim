"""Release C passes: P5 history compaction, P6 edit-format enforcement,
P3 context selection (BM25-lite), P2 spec-as-context. Pure functions."""
from __future__ import annotations

import re

from app.optimizer.passes import _last_user_index, _msg_tokens, _report
from app.optimizer.policy import Policy, content_is_protected

DECISION_MARKERS = re.compile(
    r"\b(decision|decided|we will|constraint|must|open error|failing|TODO)\b", re.IGNORECASE
)
WHOLE_FILE_MARKER = re.compile(r"```")


def p5_history_compaction(request: dict, policy: Policy) -> tuple[dict, dict]:
    """Keep system, the last user instruction, decisions/errors, and the last
    few turns; drop superseded tool results and duplicate turns (M12.2)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    if len(messages) <= 6:
        return request, _report("P5", before, before, False)
    last_user = _last_user_index(messages)
    [m for m in messages if m.get("role") == "system"]
    [m for i, m in enumerate(messages) if i >= len(messages) - 4 and i != last_user]
    kept_idx = {
        i for i, m in enumerate(messages)
        if m.get("role") == "system" or i == last_user or i >= len(messages) - 4
    }
    for i, m in enumerate(messages):  # decisions and open errors survive (M12.2 exit check)
        if i in kept_idx:
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        low = content.lower()  # cheap C substring prefilter, regex confirms
        keys = ("decision", "constraint", "must", "open error", "failing", "todo", "we will")
        if any(k in low for k in keys):
            if DECISION_MARKERS.search(content):
                kept_idx.add(i)
    compacted = [m for i, m in enumerate(messages) if i in kept_idx]
    changed = len(compacted) < len(messages)
    return ({**request, "messages": compacted} if changed else request,
            _report("P5", before, _msg_tokens(compacted), changed))


def p6_edit_format_enforcement(
    request: dict, policy: Policy, threshold_lines: int = 40
) -> tuple[dict, dict]:
    """Detect whole-file rewrites in the last assistant reply context; inject a
    search/replace format instruction and a retry marker (M12.3)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    last_user = _last_user_index(messages)
    changed = False
    out = [dict(m) for m in messages]
    for i, m in enumerate(out):
        if m.get("role") != "assistant" or i == last_user:
            continue
        content = m.get("content", "")
        too_big = isinstance(content, str) and content.count("\n") > threshold_lines
        if too_big and WHOLE_FILE_MARKER.search(content):
            if not content_is_protected(content, policy):
                marker = (
                    "\n\n[optimizer] This rewrite was rejected: "
                    "use SEARCH/REPLACE blocks, not whole-file output."
                )
                out[i]["content"] = content + marker
                changed = True
    instruction = (
        "Output edits as SEARCH/REPLACE blocks:\n"
        "<<<<<<< SEARCH\n<exact existing code>\n=======\n<replacement>\n>>>>>>> REPLACE\n"
        "Never rewrite a whole file."
    )
    if last_user >= 0:
        out.insert(last_user, {"role": "system", "content": instruction})
        changed = True
    return {**request, "messages": out}, _report("P6", before, _msg_tokens(out), changed)


def p3_context_selection(
    request: dict, policy: Policy, file_index: dict[str, str] | None = None
) -> tuple[dict, dict]:
    """BM25-lite: rank index files against the latest instruction; attach the
    top-k file paths as a context hint; never inline whole files (M12.1)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    if not file_index:
        return request, _report("P3", before, before, False, skipped="no repo index")
    last_user = _last_user_index(messages)
    query = str(messages[last_user].get("content", "")) if last_user >= 0 else ""
    terms = set(re.findall(r"[a-zA-Z_]{3,}", query.lower()))
    scores: list[tuple[float, str]] = []
    for path, text in file_index.items():
        tl = text.lower()
        score = sum(1.5 if term in tl.split() else (0.5 if term in tl else 0.0) for term in terms)
        if score:
            scores.append((score, path))
    top = [p for _, p in sorted(scores, reverse=True)[:5]]
    if not top:
        return request, _report("P3", before, before, False, skipped="no matches")
    hint = {"role": "system", "content": "Relevant files (request contents via tool): " + ", ".join(top)}
    out = messages + [hint] if last_user == len(messages) - 1 else messages[:-1] + [hint, messages[-1]]
    return {**request, "messages": out}, _report("P3", before, _msg_tokens(out), True)


def p2_spec_as_context(
    request: dict, policy: Policy, spec_package: dict | None = None, role: str = "backend"
) -> tuple[dict, dict]:
    """Replace idea-text blocks with the relevant sub-spec only (M12.4)."""
    messages = request.get("messages", [])
    before = _msg_tokens(messages)
    if not spec_package:
        return request, _report("P2", before, before, False, skipped="no spec")
    sub = spec_package.get("sub_specs", {}).get(role)
    if not sub:
        return request, _report("P2", before, before, False, skipped=f"no sub-spec for {role}")
    spec_text = (
        f"[SPEC v{spec_package.get('version')} {role}] "
        f"entities: {', '.join(sub.get('entities', []))}; "
        f"endpoints: {', '.join(e['op_id'] for e in sub.get('endpoints', []))}"
    )
    out: list[dict] = []
    changed = False
    for m in messages:
        c = m.get("content")
        is_idea = m.get("role") == "user" and isinstance(c, str) and "[IDEA]" in c
        if is_idea and not content_is_protected(c, policy):
            out.append({**m, "content": re.sub(r"\[IDEA\].*?(?=\n\[|$)", spec_text, c, flags=re.S)})
            changed = True
        else:
            out.append(m)
    return {**request, "messages": out}, _report("P2", before, _msg_tokens(out), changed)


def estimate_saved_tokens(reports: list[dict]) -> int:
    """Sum of per-pass token reductions; all values are estimates (*_est)."""
    total = 0
    for r in reports:
        if r.get("skipped"):
            continue
        saved = r.get("tokens_before_est", 0) - r.get("tokens_after_est", 0)
        if saved > 0:
            total += saved
    return total
