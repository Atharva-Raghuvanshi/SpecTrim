"""Worker entrypoint for Compose: run the claim loop forever."""
from __future__ import annotations

import asyncio

from app import winloop  # noqa: F401
from app.gateway.config import load_models_config
from app.gateway.litellm_gateway import LiteLLMGateway
from app.settings import get_settings
from app.storage.db import open_pool
from app.worker import Worker  # type: ignore[arg-type]


async def main() -> None:
    settings = get_settings()
    pool = await open_pool(settings.database_url)
    gateway = LiteLLMGateway(load_models_config(settings.models_config, settings.active_config))
    worker = Worker(pool, gateway, settings)
    print(f"{worker.worker_id} starting", flush=True)
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
