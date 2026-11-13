"""UI API (WP9): read-only JSON endpoints under /ui/api/.

Demo data (UI_DEMO=1) comes ONLY from the proxy_demo_requests table and is
always flagged demo=true; /stats and measured-benchmark numbers never include it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.optimizer.cache import SavingsLedger

EVALS_RESULTS = Path(__file__).resolve().parents[2] / "evals" / "results"


def _demo_enabled() -> bool:
    return os.environ.get("UI_DEMO", "0") == "1"


def _validate_range(rng: str | None) -> str:
    if rng not in ("1h", "24h", "7d"):
        raise HTTPException(422, "range must be one of 1h, 24h, 7d")
    return rng


def build_ui_router(app):  # type: ignore[no-untyped-def]
    router = APIRouter(prefix="/ui/api")

    def _ledger(request: Request, demo: bool) -> SavingsLedger:
        return SavingsLedger(request.app.state.pool)

    @router.get("/summary")
    async def summary(request: Request, range: str = "24h") -> dict:
        rng = _validate_range(range)
        demo = _demo_enabled()
        ledger = SavingsLedger(request.app.state.pool)
        out = await ledger.summary(rng, demo=demo)
        out["demo"] = demo
        return out

    @router.get("/series")
    async def series(request: Request, range: str = "24h") -> dict:
        rng = _validate_range(range)
        demo = _demo_enabled()
        ledger = SavingsLedger(request.app.state.pool)
        return {"range": rng, "demo": demo,
                "points": await ledger.series(rng, demo=demo)}

    @router.get("/passes")
    async def passes(request: Request, range: str = "24h") -> dict:
        rng = _validate_range(range)
        demo = _demo_enabled()
        ledger = SavingsLedger(request.app.state.pool)
        return {"range": rng, "demo": demo,
                "passes": await ledger.passes(rng, demo=demo)}

    @router.get("/requests")
    async def requests(request: Request, range: str = "24h",
                       limit: int = 50, offset: int = 0) -> dict:
        rng = _validate_range(range)
        demo = _demo_enabled()
        ledger = SavingsLedger(request.app.state.pool)
        out = await ledger.recent(demo=demo, limit=min(limit, 200), offset=max(0, offset))
        out["range"] = rng
        out["demo"] = demo
        return out

    @router.get("/benchmarks")
    async def benchmarks(request: Request) -> dict:
        """Latest measured benchmark cells from evals/results (never demo data)."""
        out: dict = {"e4": None, "e5": None}
        for exp in ("e4", "e5"):
            files = sorted(EVALS_RESULTS.glob(f"{exp}_*.json"))
            if not files:
                continue
            data = json.loads(files[-1].read_text(encoding="utf-8"))
            out[exp] = {
                "file": files[-1].name,
                "builder_model": data.get("builder_model"),
                "summary": data.get("summary"),
                "n_cells": len(data.get("cells", {})),
            }
        if out["e4"] is None and out["e5"] is None:
            return {"measured": False, "note": "NOT MEASURED", **out}
        return {"measured": True, **out}

    @router.get("/sessions")
    async def sessions(request: Request, limit: int = 50) -> dict:
        pool = request.app.state.pool
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT id, status, current_version, idea, created_at FROM sessions "
                "ORDER BY created_at DESC LIMIT %s",
                (min(limit, 200),),
            )
            rows = await cur.fetchall()
        return {"sessions": [
            {"id": r[0], "status": r[1], "current_version": r[2],
             "idea": r[3][:80], "created_at": r[4].isoformat()} for r in rows
        ]}

    @router.get("/sessions/{session_id}")
    async def session_detail(request: Request, session_id: str, version: int | None = None) -> dict:
        pool = request.app.state.pool
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT status, current_version FROM sessions WHERE id = %s", (session_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(404, "session not found")
            version = version or row[1] or 1
            await cur.execute(
                "SELECT package FROM versions WHERE session_id = %s AND version = %s",
                (session_id, version),
            )
            pkg = await cur.fetchone()
        if pkg is None:
            raise HTTPException(404, "version not found")
        package = pkg[0]
        return {
            "session_id": session_id, "status": row[0], "version": version,
            "master_spec": package.get("master_spec", {}),
            "assumptions": package.get("assumptions", []),
            "sub_specs": package.get("sub_specs", {}),
            "contract": package.get("contract", {}),
            "validation": package.get("validation", {}),
            "diff_from_previous": package.get("diff_from_previous"),
        }

    @router.get("/sessions/{session_id}/openapi.yaml")
    async def session_openapi(request: Request, session_id: str, version: int | None = None):
        pool = request.app.state.pool
        import yaml

        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT status, current_version FROM sessions WHERE id = %s", (session_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(404, "session not found")
            version = version or row[1] or 1
            await cur.execute(
                "SELECT package FROM versions WHERE session_id = %s AND version = %s",
                (session_id, version),
            )
            pkg = await cur.fetchone()
        if pkg is None:
            raise HTTPException(404, "version not found")
        from fastapi import Response

        return Response(
            yaml.safe_dump(pkg[0].get("contract", {}).get("openapi", {}), sort_keys=False),
            media_type="application/yaml",
        )

    @router.get("/health")
    async def health(request: Request) -> dict:
        try:
            pool = request.app.state.pool
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1")
            return {"ok": True}
        except Exception:
            return {"ok": False}

    return router
