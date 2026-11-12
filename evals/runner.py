"""Eval runner (M8.2): E1 validity, E2 question precision/recall, budget guard.

Usage:
  python -m evals.runner --limit 10 --max-usd 1.0 --max-tokens 200000
Writes evals/results/e1_e2.json. Uses the real gateway; never run in CI.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.gateway.config import load_models_config  # noqa: E402
from app.gateway.litellm_gateway import LiteLLMGateway  # noqa: E402
from app.pipeline.inmemory import run_pipeline  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.stages.analyzer import run_analyzer  # noqa: E402
from app.stages.classifier import run_classifier  # noqa: E402
from app.stages.question_gate import run_question_gate  # noqa: E402

LABELS = Path(__file__).resolve().parent / "prompts" / "labels.jsonl"
RESULTS = Path(__file__).resolve().parent / "results"


def load_labels(limit: int) -> list[dict]:
    rows = [
        json.loads(line)
        for line in LABELS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return rows[:limit]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-usd", type=float, default=1.0, dest="max_usd")
    parser.add_argument("--max-tokens", type=float, default=200000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=30.0, dest="pace_s")
    args = parser.parse_args()

    settings = get_settings()
    gateway = LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config),
        max_retries=6, base_delay=2.0,  # ride out Groq free-tier RPM windows
    )
    RESULTS.mkdir(parents=True, exist_ok=True)

    rows = load_labels(args.limit)
    e1: dict[str, int] = {"valid": 0, "warnings": 0, "invalid": 0}
    e2_tp = e2_fp = e2_fn = 0
    total_tokens = 0.0
    total_cost = 0.0
    per_prompt: list[dict] = []
    started = time.time()

    for row in rows:
        if total_cost > args.max_usd or total_tokens > args.max_tokens:
            print(f"BUDGET ABORT: ${total_cost:.4f} / {int(total_tokens)} tok", file=sys.stderr)
            break
        rec: dict = {"id": row["id"]}
        try:
            analysis, res_a = run_analyzer(gateway, row["idea"])
            classifier_out, res_c = run_classifier(gateway, analysis)
            gate = run_question_gate(
                analysis, classifier_out.labels, max_questions=settings.max_questions_per_round
            )
            asked_cats = {q.category for q in gate.questions}
            expected = set(row["expected_blocking"])
            tp, fp = len(asked_cats & expected), len(asked_cats - expected)
            fn = len(expected - asked_cats)
            e2_tp += tp
            e2_fp += fp
            e2_fn += fn
            rec["questions"] = sorted(asked_cats)
            rec["tp_fp_fn"] = [tp, fp, fn]
            for res in (res_a, res_c):
                total_tokens += res.usage.total_tokens
                total_cost += res.cost_usd or 0.0
            try:
                pkg, trace = run_pipeline(
                    gateway,
                    f"ses_eval{row['id'].lower()}",
                    row["idea"],
                    answers=["Use sensible defaults."] * 8,
                    max_questions=settings.max_questions_per_round,
                )
                total_tokens += sum(
                    r.usage.total_tokens for r in trace.results.values()
                )
                total_cost += sum((r.cost_usd or 0.0) for r in trace.results.values())
                v = pkg.validation
                ok = v.schema_ok and v.openapi_lint_ok and v.consistency_ok
                if ok and not pkg.validation.warnings:
                    e1["valid"] += 1
                elif ok:
                    e1["warnings"] += 1
                    rec["warnings"] = pkg.validation.warnings[:3]
                else:
                    e1["invalid"] += 1
                rec["valid"] = ok
            except Exception as exc:  # noqa: BLE001
                e1["invalid"] += 1
                rec["valid"] = False
                rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
            rec["valid"] = False
            e1["invalid"] += 1
        per_prompt.append(rec)
        print(f"{row['id']}: {rec.get('valid', 'ERR')} (+{int(total_tokens)} tok)", flush=True)
        if args.pace_s:
            time.sleep(args.pace_s)

    precision = e2_tp / (e2_tp + e2_fp) if (e2_tp + e2_fp) else 0.0
    recall = e2_tp / (e2_tp + e2_fn) if (e2_tp + e2_fn) else 0.0
    report = {
        "ran": len(per_prompt),
        "e1": e1,
        "e1_validity_rate": e1["valid"] / len(per_prompt) if per_prompt else 0.0,
        "e2_micro_precision": precision,
        "e2_micro_recall": recall,
        "tokens_total_est": int(total_tokens),
        "cost_usd_reported": total_cost,
        "elapsed_s": round(time.time() - started, 1),
        "per_prompt": per_prompt,
    }
    out = RESULTS / "e1_e2.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_prompt"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
