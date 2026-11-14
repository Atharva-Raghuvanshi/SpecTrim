"""E3b (WP10): re-run downstream lift on 3 non-trivial multi-file tasks.

Builder model fixed for both arms; resumable per (task, rep, arm); the spec
context is generated once per task through the full pipeline (120b) and the
builder runs on 20b. Multi-file output via '### FILE: name.py' markers.
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
from checks import check_generated_multi, parse_files  # noqa: E402

from evals.agents.builder import build_once  # noqa: E402
from evals.quota import QuotaTracker  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e3b.json"
RESULTS = Path(__file__).resolve().parent / "results"
SPECS = RESULTS / "e3b_specs.json"


def load_or_make_specs(gateway: LiteLLMGateway, quota: QuotaTracker, builder: str) -> dict:
    if SPECS.is_file():
        return json.loads(SPECS.read_text(encoding="utf-8"))
    from app.pipeline.inmemory import run_pipeline

    specs: dict[str, str] = {}
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    for t in tasks:
        try:
            pkg, _ = run_pipeline(
                gateway, f"ses_e3b_{t['id'].lower()}", t["prompt"],
                answers=["Use sensible defaults."] * 8, max_questions=3,
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
        except Exception as exc:  # noqa: BLE001
            specs[t["id"]] = ""
            print(f"spec gen failed for {t['id']}: {type(exc).__name__}", flush=True)
        quota.check_tpm_and_sleep("openai/gpt-oss-120b", 6000)
    SPECS.write_text(json.dumps(specs, indent=1), encoding="utf-8")
    return specs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--builder", default="groq/openai/gpt-oss-20b")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--max-tokens", type=float, default=170000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=45.0, dest="pace_s")
    args = parser.parse_args()

    settings = get_settings()
    quota = QuotaTracker()
    gateway = LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config),
        max_retries=6, base_delay=2.0,
    )
    out_path = RESULTS / "e3b_groq_openai_gpt-oss-20b.json"
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    results: dict = (
        json.loads(out_path.read_text(encoding="utf-8"))
        if out_path.is_file() else {"builder_model": args.builder, "cells": {}}
    )
    results["builder_model"] = args.builder

    specs = load_or_make_specs(gateway, quota, args.builder)
    n_cells = len(tasks) * args.reps * 2
    print(f"E3b estimate: {n_cells} builder cells x ~4K tokens ~= "
          f"{n_cells * 4 // 1}K + ~{len(tasks) * 30}K spec-gen; budget 170K/day/model")
    if not quota.check_tpd(args.builder, n_cells * 4000):
        print("quota reached, resume tomorrow")
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return 0

    for t in tasks:
        for rep in range(args.reps):
            for arm in ("raw", "optimized"):
                cell = f"{t['id']}:rep{rep}:{arm}"
                if cell in results["cells"]:
                    continue
                if not quota.check_tpd(args.builder, 4000):
                    print("quota reached, resume tomorrow")
                    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                    return 0
                spec_ctx = None if arm == "raw" else specs.get(t["id"], "")
                try:
                    quota.check_tpm_and_sleep(args.builder, 3000)
                    t0 = time.perf_counter()
                    src, res = build_once(gateway, t["prompt"], spec_ctx)
                    quota.record_usage(args.builder, res.usage.total_tokens or 0)
                    files = parse_files(src)
                    chk = check_generated_multi(t["id"], files)
                    rec = {
                        "pass": chk["pass"], "stage": chk["stage"],
                        "files": chk.get("files", []),
                                        "src_bytes": len(src),
                        "tokens": res.usage.total_tokens,
                    }
                except Exception as exc:  # noqa: BLE001
                    name = type(exc).__name__
                    rec = {"pass": None,
                           "infra_aborted": "RateLimit" in name or "429" in str(exc),
                           "error": f"{name}: {exc}"[:200]}
                results["cells"][cell] = rec
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"{cell}: {rec}", flush=True)
                time.sleep(args.pace_s)

    raw = [c["pass"] for k, c in results["cells"].items()
           if k.endswith(":raw") and c.get("pass") is not None]
    opt = [c["pass"] for k, c in results["cells"].items()
           if k.endswith(":optimized") and c.get("pass") is not None]
    results["summary"] = {
        "pass_rate_raw": sum(raw) / len(raw) if raw else None, "n_raw": len(raw),
        "pass_rate_optimized": sum(opt) / len(opt) if opt else None, "n_opt": len(opt),
    }
    print(json.dumps(results["summary"], indent=2))
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
