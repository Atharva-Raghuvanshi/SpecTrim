"""SSE endpoint with Last-Event-ID replay (M6.1). Poll every 500ms (PRD Open Q4)."""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

POLL_INTERVAL_S = 0.5


async def event_stream(request: Request, session_id: str, events, last_seq: int):
    seq = last_seq
    while True:
        if await request.is_disconnected():
            return
        batch = await events.after(session_id, seq, limit=100)
        for ev in batch:
            payload = json.dumps({"type": ev["type"], "payload": ev["payload"]})
            yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {payload}\n\n"
            seq = ev["seq"]
        if not batch:
            await asyncio.sleep(POLL_INTERVAL_S)


def sse_response(request: Request, session_id: str, events, last_event_id: int | None):
    """Stream events after last_event_id (0 = from the beginning)."""
    return StreamingResponse(
        event_stream(request, session_id, events, last_event_id or 0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
