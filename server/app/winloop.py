"""Force SelectorEventLoop on Windows: psycopg async mode is incompatible
with the default ProactorEventLoop. Import this before any asyncio use."""
from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
