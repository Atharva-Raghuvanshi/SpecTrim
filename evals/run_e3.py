"""E3 downstream lift (WP5): 8 tasks x 2 reps x 2 arms (raw vs spec package).

Builder model is fixed for both arms. Resumable per (task, rep, arm); every
cell persisted immediately; quota tracked via evals.quota.QuotaTracker.

Usage:
  python -m evals.run_e3 --builder groq/openai/gpt-oss-20b --max-tokens 170000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import winloop  # noqa: E402,F401
from app.gateway.config import load_models_config  # noqa: E402
from app.gateway.litellm_gateway import LiteLLMGateway  # noqa: E402
from app.settings import get_settings  # noqa: E402
from checks import check_generated  # noqa: E402

from evals.agents.builder import build_once  # noqa: E402
from evals.quota import QuotaTracker  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e3.json"
RESULTS = Path(__file__).resolve().parent / "results"
SPECS = RESULTS / "e3_specs.json"


def load_or_make_specs(gateway: LiteLLMGateway, quota: QuotaTracker, builder: str) -> dict:
    """Task -> spec package context (raw text of the frozen smoke packages)."""
    if SPECS.is_file():
        return json.loads(SPECS.read_text(encoding="utf-8"))
    # Generate one small spec per task through the full in-memory pipeline.
    from app.pipeline.inmemory import run_pipeline

    specs: dict[str, str] = {}
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    for t in tasks:
        try:
            pkg, _ = run_pipeline(
                gateway, f"ses_e3_{t['id'].lower()}", t["prompt"],
                answers=["Use sensible defaults."] * 8,
                max_questions=3,
            )
            specs[t["id"]] = json.dumps({
                "summary": pkg.master_spec.summary,
                "features": pkg.master_spec.model_dump()["features"],
                "endpoints": [
                    {"op_id": e.op_id, "method": e.method, "path": e.path,
                     "uses_entities": e.uses_entities}
                    for sub in pkg.sub_specs.values() for e in sub.endpoints
                ],
                "entities": [e.model_dump() for e in pkg.contract.entities],
            }, indent=1)
        except Exception as exc:  # noqa: BLE001 - fall back to prompt-only context
            specs[t["id"]] = ""
            print(f"spec gen failed for {t['id']}: {type(exc).__name__}", flush=True)
        quota.check_tpm_and_sleep(builder, 4000)
    SPECS.write_text(json.dumps(specs, indent=1), encoding="utf-8")
    return specs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder", default="groq/openai/gpt-oss-20b")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--max-tokens", type=float, default=170000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=2.0, dest="pace_s")
    args = parser.parse_args()

    settings = get_settings()
    quota = QuotaTracker()
    gateway = LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config),
        max_retries=6, base_delay=2.0,
    )
    out_path = RESULTS / f"e3_{args.builder.replace('/', '_')}.json"
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    results: dict = (
        json.loads(out_path.read_text(encoding="utf-8"))
        if out_path.is_file() else {"builder_model": args.builder, "cells": {}}
    )
    results["builder_model"] = args.builder
    specs = load_or_make_specs(gateway, quota, args.builder)

    est = print_estimate(tasks, args.reps)
    if not quota.check_tpd(args.builder, est):
        print("quota reached, resume tomorrow")
        return 0

    for t in tasks:
        for rep in range(args.reps):
            for arm in ("raw", "optimized"):
                cell = f"{t['id']}:rep{rep}:{arm}"
                if cell in results["cells"]:
                    continue
                if not quota.check_tpd(args.builder, est // len(tasks)):
                    print("quota reached, resume tomorrow")
                    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                    return 0
                spec_ctx = None if arm == "raw" else specs.get(t["id"], "")
                if arm == "optimized" and not spec_ctx and SPECS.is_file():
                    spec_ctx = specs.get(t["id"], "")
                try:
                    t0 = time.perf_counter()
                    quota.check_tpm_and_sleep(args.builder, 3000)
                    src, res = build_once(gateway, t["prompt"], spec_ctx)
                    quota.record_usage(args.builder, res.usage.total_tokens or 0)
                    chk = check_generated(t["id"], src)
                    rec = {
                        "pass": chk["pass"], "stage": chk["stage"],
                        "latency_s": round(time.perf_counter() - t0, 1),
                        "src_bytes": len(src),
                        "tokens_est": (
                            (len(t["prompt"] + (spec_ctx or "")) // 4)
                            + (res.usage.total_tokens or 0)
                        ),
                    }
                except Exception as exc:  # noqa: BLE001
                    name = type(exc).__name__
                    kind = "rate limited" if "429" in str(exc) or "RateLimit" in name else "error"
                    rec = {"pass": None,
                           "infra_aborted": kind == "rate limited",
                           "error": f"{type(exc).__name__}: {exc}"[:200]}
                results["cells"][cell] = rec
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"{cell}: {rec}", flush=True)
                time.sleep(args.pace_s)

    summarize(results)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


def print_estimate(tasks: list, reps: int) -> int:
    cells = len(tasks) * reps * 2
    est_total = cells * 3500  # rough per-cell estimate
    print(f"E3 estimate: {cells} cells x ~3.5K tokens ~= {est_total // 1000}K "
          f"(budget 170K/day) -> ~{max(1, est_total // 170000)} day(s)")
    return est_total


def summarize(results: dict) -> None:
    arms: dict[str, list] = {"raw": [], "optimized": []}
    for cell, rec in results["cells"].items():
        if rec.get("pass") is None:
            continue
        arm = cell.rsplit(":", 1)[1]
        arms[arm].append(rec["pass"])
    stats = {a: (sum(v) / len(v) if v else None) for a, v in arms.items()}
    results["summary"] = {
        "pass_rate_raw": stats["raw"], "pass_rate_optimized": stats["optimized"],
        "n_raw": len(arms["raw"]), "n_optimized": len(arms["optimized"]),
        "lift": (
            stats["optimized"] - stats["raw"]
            if stats["raw"] is not None and stats["optimized"] is not None else None
        ),
    }
    print(json.dumps(results["summary"], indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
