"""Phase 6: SSE replay (M6.1), exports (M6.3), MCP tool surface (M6.2)."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
import yaml

pytestmark = pytest.mark.usefixtures("db_pool")


async def _seed_events(pool, sid: str) -> list[int]:
    from app.storage.repos import EventsRepo, SessionsRepo

    await SessionsRepo(pool).create_idempotent("idea", f"k-{uuid.uuid4()}") if False else None
    from app.storage.repos import SessionsRepo

    sid_real, _ = await SessionsRepo(pool).create_idempotent("seed", f"k-{uuid.uuid4()}")
    repo = EventsRepo(pool)
    seqs = [await repo.append(sid_real, f"e{i}", {"n": i}) for i in range(5)]
    return seqs, sid_real


def _read_events(client, sid: str, expect: int, last_event_id: str | None = None) -> list[int]:
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    ids: list[int] = []
    with client.stream("GET", f"/sessions/{sid}/events", headers=headers) as r:
        for chunk in r.iter_bytes():
            for line in chunk.decode().splitlines():
                if line.startswith("id: "):
                    ids.append(int(line.split("id: ")[1]))
            if len(ids) >= expect:
                break  # bounded read: close the stream (server sees disconnect)
    return ids


def test_sse_replay_from_beginning(app_client) -> None:
    client, _ = app_client
    pool = client.app.state.pool
    _, sid = asyncio.get_event_loop().run_until_complete(_seed_events(pool, "x"))
    ids = _read_events(client, sid, expect=5)
    assert ids == [1, 2, 3, 4, 5]  # no gaps, no duplicates


def test_sse_replay_with_last_event_id(app_client) -> None:
    client, _ = app_client
    pool = client.app.state.pool
    _, sid = asyncio.get_event_loop().run_until_complete(_seed_events(pool, "x"))
    ids = _read_events(client, sid, expect=3, last_event_id="2")
    assert ids == [3, 4, 5]  # resumes with no gaps and no duplicates


def test_export_formats(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    sid = r.json()["id"]
    from tests.conftest import drain

    drain(client, sid)
    client.post(f"/sessions/{sid}/answers", json={"answers": [{"id": "Q1", "answer": "single"}]})
    drain(client, sid)

    js = client.get(f"/sessions/{sid}/export?format=json").json()
    assert js["session_id"] == sid and "contract" in js
    md = client.get(f"/sessions/{sid}/export?format=markdown").text
    assert "# Spec v1" in md and "## Features" in md and "## Contract v" in md
    y = client.get(f"/sessions/{sid}/export?format=openapi").text
    doc = yaml.safe_load(y)
    assert doc["openapi"] == "3.1.0"
    bad = client.get(f"/sessions/{sid}/export?format=pdf")
    assert bad.status_code == 422


def test_mcp_server_exposes_five_tools() -> None:
    from app.mcp_server import mcp

    tools = asyncio.new_event_loop().run_until_complete(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "create_spec",
        "answer_questions",
        "revise_spec",
        "get_contract",
        "diff_versions",
        "get_context",
        "savings_report",
    }


def test_questions_endpoint_lists_unanswered(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    sid = r.json()["id"]
    from tests.conftest import drain

    drain(client, sid)
    q = client.get(f"/sessions/{sid}/questions").json()
    assert q["questions"] and q["questions"][0]["id"] == "Q1"
    assert json.dumps(q)  # serializable
