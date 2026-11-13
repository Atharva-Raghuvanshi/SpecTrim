"""MCP server with the five spec tools (M6.2). Proxies the REST API.

Run: python -m app.mcp_server   (stdio transport; API_BASE points at the API)
"""
from __future__ import annotations

import os

import httpx
from mcp.server.mcpserver import MCPServer

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8001")
BEARER = os.environ.get("API_BEARER_TOKEN", "")

mcp = MCPServer("query-optimizer")


def _headers() -> dict:
    h = {}
    if BEARER:
        h["Authorization"] = f"Bearer {BEARER}"
    return h


async def _call(method: str, path: str, **kwargs: object) -> dict:
    headers = dict(_headers())
    if "headers" in kwargs:
        extra = kwargs.pop("headers")
        if isinstance(extra, dict):
            headers.update(extra)
    async with httpx.AsyncClient(base_url=API_BASE, timeout=120) as client:
        resp = await client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]
        resp.raise_for_status()
        data: dict = resp.json()
        return data


@mcp.tool()
async def create_spec(idea: str) -> dict:
    """Create a spec session for a product idea; returns session id, status,
    and any blocking questions to answer."""
    import uuid

    idem = {"Idempotency-Key": f"mcp-{uuid.uuid4()}"}
    r = await _call("POST", "/sessions", json={"idea": idea}, headers=idem)
    session_id = r["id"]
    out: dict = {"session_id": session_id, "status": r["status"]}
    # give the worker a moment, then surface questions if any
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0.5)
        s = await _call("GET", f"/sessions/{session_id}")
        if s["status"] in ("awaiting_user", "ready", "failed"):
            out["status"] = s["status"]
            break
    if out.get("status") == "awaiting_user":
        detail = await _call("GET", f"/sessions/{session_id}/questions")
        out["questions"] = detail.get("questions", [])
        out["hint"] = "answer via answer_questions(session_id, answers)"
    return out


@mcp.tool()
async def answer_questions(session_id: str, answers: list[str]) -> dict:
    """Answer the blocking questions of a session, in question id order (Q1, Q2, ...)."""
    detail = await _call("GET", f"/sessions/{session_id}/questions")
    qids = [q["id"] for q in detail.get("questions", [])]
    if len(answers) < len(qids):
        return {"error": f"need {len(qids)} answers, got {len(answers)}", "question_ids": qids}
    payload = {"answers": [{"id": qid, "answer": a} for qid, a in zip(qids, answers)]}
    return await _call("POST", f"/sessions/{session_id}/answers", json=payload)


@mcp.tool()
async def revise_spec(session_id: str, feedback: str, feedback_type: str = "change_request") -> dict:
    """Request a revision of a ready spec (change_request or failure_report)."""
    body = {"type": feedback_type, "feedback": feedback}
    return await _call("POST", f"/sessions/{session_id}/revisions", json=body)


@mcp.tool()
async def get_contract(session_id: str, version: int | None = None) -> dict:
    """Fetch the OpenAPI contract for a session version (latest if omitted)."""
    if version is None:
        s = await _call("GET", f"/sessions/{session_id}")
        version = s["current_version"]
    pkg = await _call("GET", f"/sessions/{session_id}/export?format=json&version={version}")
    return {"version": version, "contract": pkg.get("contract", {})}


@mcp.tool()
async def diff_versions(session_id: str, from_version: int, to_version: int) -> dict:
    """Diff two spec versions; flags breaking changes."""
    return await _call(
        "GET", f"/sessions/{session_id}/diff?from_version={from_version}&to_version={to_version}"
    )


@mcp.tool()
async def get_context(session_id: str, task: str, role: str = "backend") -> dict:
    """Get the relevant spec context for a coding task: the sub-spec for the
    agent role plus entities and endpoints it touches (FR-45)."""
    s = await _call("GET", f"/sessions/{session_id}")
    version = s.get("current_version")
    if not version:
        return {"error": "session has no spec version yet", "status": s.get("status")}
    pkg = await _call("GET", f"/sessions/{session_id}/export?format=json&version={version}")
    sub = pkg.get("sub_specs", {}).get(role)
    if sub is None:
        return {"error": f"no sub-spec for role {role!r}", "roles": sorted(pkg.get("sub_specs", {}))}
    return {
        "session_id": session_id,
        "version": version,
        "role": role,
        "task": task,
        "summary": pkg.get("master_spec", {}).get("summary", ""),
        "entities": sub.get("entities", []),
        "endpoints": sub.get("endpoints", []),
        "pages": sub.get("pages", []),
        "contract_version": sub.get("contract_version"),
    }


@mcp.tool()
async def savings_report() -> dict:
    """Savings ledger summary: baseline-est vs actual tokens, cache hits, passthroughs (FR-45)."""
    return await _call("GET", "/stats")


def main() -> int:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)  # type: ignore[arg-type,call-overload]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
