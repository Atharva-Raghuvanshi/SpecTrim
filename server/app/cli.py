"""CLI: idea -> package in memory (M3.5). No DB, no server.

Usage:
  python -m app.cli "Build a habit tracker" [--auto-answer] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.gateway.config import load_models_config
from app.gateway.litellm_gateway import LiteLLMGateway
from app.pipeline.inmemory import NeedsAnswers, run_pipeline
from app.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="idea -> spec package (in memory)")
    parser.add_argument("idea", help="product idea text")
    parser.add_argument("--auto-answer", action="store_true", help="answer questions with defaults")
    parser.add_argument("--json", action="store_true", help="print full package JSON")
    args = parser.parse_args()

    settings = get_settings()
    gateway = LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config)
    )
    session_id = f"ses_{uuid.uuid4().hex[:12]}"
    try:
        pkg, trace = run_pipeline(
            gateway,
            session_id,
            args.idea,
            answers=(["Use sensible defaults."] * 8 if args.auto_answer else None),
            max_questions=settings.max_questions_per_round,
        )
    except NeedsAnswers as exc:
        questions = [q.model_dump() for q in exc.gate.questions]
        print(json.dumps({"session_id": session_id, "questions": questions}, indent=2))
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI reports, not crashes
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(pkg.model_dump_json(indent=2))
    else:
        print(f"session: {pkg.session_id} v{pkg.version}")
        v = pkg.validation
        print(f"validation: lint_ok={v.openapi_lint_ok} consistency_ok={v.consistency_ok}")
        for w in pkg.validation.warnings:
            print(f"  warning: {w}")
        print(f"features: {len(pkg.master_spec.features)} assumptions: {len(pkg.assumptions)}")
        ops = sum(len(s.endpoints) for s in pkg.sub_specs.values())
        print(f"contract ops: {ops} entities: {len(pkg.contract.entities)}")
    return 0 if not pkg.validation.warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
