"""Worker runner subprocess for the real-process crash/resume integration test (WP2).

Runs a Worker loop using a paced FakeGateway so that the test can reliably
kill this process mid-pipeline after stages 1..k have completed and been persisted.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from app import winloop  # noqa: F401
from app.gateway.base import CompletionResult, Gateway, Usage
from app.settings import Settings
from app.storage.db import open_pool
from app.storage.migrate import apply_migrations
from app.worker import Worker
from tests.test_stages import CONTRACT_JSON, MASTER_JSON, DECOMPose_JSON

STAGE_RESPONSES = {
    "analyzer": (
        '{"goals": ["track habits"], "entities": ["habit"], "features": ["add habit"], '
        '"constraints": [], "ambiguities": []}'
    ),
    "classifier": '{"labels": []}',
    "spec_writer": MASTER_JSON,
    "contract": CONTRACT_JSON,
    "decomposer": DECOMPose_JSON,
}


class PacedFakeGateway:
    model_name = "fake-model"

    def __init__(self, delay_s: float = 0.4) -> None:
        self.delay_s = delay_s

    def complete(self, stage: str, messages: list[dict[str, str]]) -> CompletionResult:
        if self.delay_s > 0:
            time.sleep(self.delay_s)
        text = STAGE_RESPONSES.get(stage, "{}")
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        prompt_tokens = max(1, prompt_chars // 4)
        completion_tokens = max(1, len(text) // 4)
        return CompletionResult(
            stage=stage,
            model=self.model_name,
            text=text,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            latency_ms=int(self.delay_s * 1000),
            cost_usd=0.0,
        )


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL") or (
        sys.argv[1] if len(sys.argv) > 1 else "postgresql://app:app@localhost:5433/qo"
    )
    delay_s = float(os.environ.get("STAGE_DELAY_S", "0.4"))
    apply_migrations(dsn)
    pool = await open_pool(dsn)
    gateway: Gateway = PacedFakeGateway(delay_s=delay_s)  # type: ignore[assignment]
    settings = Settings()
    settings.lease_seconds = 5
    worker = Worker(pool, gateway, settings)
    print(f"WORKER_STARTED {worker.worker_id}", flush=True)
    await worker.run_forever(poll_interval=0.05)


if __name__ == "__main__":
    asyncio.run(main())
