"""M5.2: revision flow produces v2 with a diff; M5.1: no duplicate versions."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_pool")

REVISED_MASTER = """
{"summary": "habit tracker with reminders", "user_flows": ["add habit"],
 "features": [{"id": "F1", "title": "Add habit", "acceptance": ["POST /habits works"]},
              {"id": "F2", "title": "Reminders", "acceptance": ["reminder scheduled"]}],
 "non_functional": []}
"""


@pytest.fixture()
def ready_session(app_client):
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "habit tracker"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    sid = r.json()["id"]
    from tests.conftest import drain as _drain

    _drain(client, sid)
    client.post(f"/sessions/{sid}/answers", json={"answers": [{"id": "Q1", "answer": "single"}]})
    _drain(client, sid)
    session = client.get(f"/sessions/{sid}").json()
    assert session["status"] == "ready", session
    return client, sid


def test_change_request_yields_v2_with_diff(ready_session) -> None:
    client, sid = ready_session
    import asyncio

    from app.gateway.fake import FakeGateway
    from app.pipeline.orchestrator import PipelineContext, run_revision_job

    gateway: FakeGateway = client.app.state.gateway
    gateway.set_response("revision", REVISED_MASTER)
    ctx = PipelineContext(client.app.state.pool, gateway, 3)
    final = asyncio.get_event_loop().run_until_complete(
        run_revision_job(ctx, sid, "add reminders", "change_request")
    )
    assert final == "ready"
    session = client.get(f"/sessions/{sid}").json()
    assert session["current_version"] == 2
    from app.storage.repos import VersionsRepo

    pkg = asyncio.get_event_loop().run_until_complete(
        VersionsRepo(client.app.state.pool).get(sid, 2)
    )
    assert pkg["diff_from_previous"]["from"] == 1
    assert pkg["diff_from_previous"]["to"] == 2


def test_diff_endpoint_between_versions(ready_session) -> None:
    client, sid = ready_session
    import asyncio

    from app.gateway.fake import FakeGateway
    from app.pipeline.orchestrator import PipelineContext, run_revision_job

    gateway: FakeGateway = client.app.state.gateway
    gateway.set_response("revision", REVISED_MASTER)
    ctx = PipelineContext(client.app.state.pool, gateway, 3)
    asyncio.get_event_loop().run_until_complete(
        run_revision_job(ctx, sid, "add reminders", "change_request")
    )
    d = client.get(f"/sessions/{sid}/diff").json()
    assert d["from"] == 1 and d["to"] == 2
    assert "breaking" in d and "changes" in d


def test_revision_requires_ready_state(app_client) -> None:
    client, _ = app_client
    r = client.post(
        "/sessions", json={"idea": "x"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    sid = r.json()["id"]
    resp = client.post(
        f"/sessions/{sid}/revisions", json={"type": "change_request", "feedback": "nope"}
    )
    assert resp.status_code == 409
