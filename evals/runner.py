"""Eval runner (M8.2, WP3): E1 validity, E2 question precision/recall, budget guard.

Features:
  - Resumable per prompt (persists after each prompt; skips completed prompts).
  - Infra_aborted vs quality_fail classification.
  - Quota-aware (integrates QuotaTracker; sleeps near TPM limit, halts at 85% TPD).
  - Pre-run token estimate.
  - CLI options: --version, --prompts, --limit, --max-usd, --max-tokens, --pace-s.

Usage:
  python -m evals.runner --version v2 --prompts 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.gateway.base import GatewayRateLimited  # noqa: E402
from app.gateway.config import load_models_config  # noqa: E402
from app.gateway.litellm_gateway import LiteLLMGateway  # noqa: E402
from app.pipeline.inmemory import run_pipeline  # noqa: E402
from app.settings import get_settings  # noqa: E402
from app.stages.analyzer import run_analyzer  # noqa: E402
from app.stages.classifier import run_classifier  # noqa: E402
from app.stages.question_gate import run_question_gate  # noqa: E402

from evals.quota import QuotaTracker  # noqa: E402
from evals.stamp import labels_hash, models_hash, prompts_hash, result_stamp, stamp_matches  # noqa: E402

LABELS_FILE = Path(__file__).resolve().parent / "prompts" / "labels.jsonl"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
EST_TOKENS_PER_PROMPT = 4500

INFRA_KEYWORDS = (
    "ratelimit",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "timeout",
    "connection",
    "quota",
    "overloaded",
    "gatewayratelimited",
)


def is_infra_error(exc: Exception) -> bool:
    if isinstance(exc, GatewayRateLimited):
        return True
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(k in msg for k in INFRA_KEYWORDS)


def load_all_labels() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in LABELS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def filter_prompts(prompts_arg: str | None, limit: int) -> list[dict[str, Any]]:
    all_rows = load_all_labels()
    if not prompts_arg:
        return all_rows[:limit]

    # Check if prompts_arg is a count (e.g. '5')
    if prompts_arg.isdigit():
        return all_rows[: int(prompts_arg)]

    # Comma-separated list of IDs (e.g. 'P01,P02,P03')
    requested_ids = {p.strip().upper() for p in prompts_arg.split(",") if p.strip()}
    return [row for row in all_rows if row["id"].upper() in requested_ids]


def main() -> int:
    parser = argparse.ArgumentParser(description="Spec Eval Runner (E1 validity, E2 precision/recall)")
    parser.add_argument("--version", type=str, default="v2", help="Prompt version (v1 or v2)")
    parser.add_argument("--prompts", type=str, default=None, help="Number of prompts or comma-separated IDs")
    parser.add_argument("--limit", type=int, default=30, help="Max prompts to run if --prompts is omitted")
    parser.add_argument("--max-usd", type=float, default=1.0, dest="max_usd")
    parser.add_argument("--max-tokens", type=float, default=200000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=30.0, dest="pace_s")
    parser.add_argument("--output", type=str, default=None, help="Override output json path")
    parser.add_argument("--no-resume", action="store_true", help="Do not resume from existing results file")
    args = parser.parse_args()

    # Set prompt version environment variable so pipeline/prompts.py picks up v1 or v2
    os.environ["PROMPT_VERSION"] = args.version

    settings = get_settings()
    gateway = LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config),
        max_retries=6,
        base_delay=2.0,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    quota = QuotaTracker()
    stamp = result_stamp("e1e2", {
        "prompts": prompts_hash(), "models": models_hash(), "labels": labels_hash(),
    })

    # Determine output file
    if args.output:
        out_path = Path(args.output)
    elif args.version == "v2":
        out_path = RESULTS_DIR / "e1_e2_v2.json"
    else:
        out_path = RESULTS_DIR / "e1_e2.json"

    rows = filter_prompts(args.prompts, args.limit)

    # Load existing results for resumability
    existing_records: dict[str, dict[str, Any]] = {}
    if not args.no_resume and out_path.is_file():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            for r in prev.get("per_prompt", []):
                # Resume completed runs whose stamp matches the current content hashes
                if not r.get("infra_aborted") and stamp_matches(r.get("stamp"), stamp):
                    existing_records[r["id"]] = r
        except Exception:
            pass

    to_run = [r for r in rows if r["id"] not in existing_records]
    est_tokens = len(to_run) * EST_TOKENS_PER_PROMPT

    print("====================================================================")
    print(f"Spec Evaluation Runner [Version: {args.version}]")
    print(
        f"Total target prompts: {len(rows)} | Completed: {len(existing_records)} | To run: {len(to_run)}"
    )
    print(f"Estimated token consumption: ~{est_tokens:,} tokens (max budget: {args.max_tokens:,})")
    print(f"Output destination: {out_path}")
    print("====================================================================", flush=True)

    if not to_run and existing_records:
        print("All requested prompts already completed in output file. Recalculating report.")

    per_prompt: list[dict[str, Any]] = [
        existing_records[r["id"]] for r in rows if r["id"] in existing_records
    ]
    total_tokens = sum(r.get("tokens", 0) for r in per_prompt)
    total_cost = sum(r.get("cost_usd", 0.0) for r in per_prompt)
    started = time.time()

    for row in to_run:
        if total_cost > args.max_usd or total_tokens > args.max_tokens:
            print(f"BUDGET ABORT: ${total_cost:.4f} / {int(total_tokens)} tok", file=sys.stderr)
            break

        # Check daily quota (stop cleanly at 85% of 200K TPD)
        if not quota.check_tpd("groq/openai/gpt-oss-120b", estimated_tokens=EST_TOKENS_PER_PROMPT):
            print(f"QUOTA STOP: Daily limit reached before {row['id']}. Resume tomorrow.", file=sys.stderr)
            break

        # Check TPM limit and sleep if projected > 80%
        quota.check_tpm_and_sleep("groq/openai/gpt-oss-120b", estimated_tokens=EST_TOKENS_PER_PROMPT)

        rec: dict[str, Any] = {
            "id": row["id"],
            "version": args.version,
            "status": "pending",
            "infra_aborted": False,
            "quality_fail": False,
            "tokens": 0,
            "cost_usd": 0.0,
        }
        prompt_tokens = 0
        prompt_cost = 0.0

        try:
            # 1. Analyzer
            analysis, res_a = run_analyzer(gateway, row["idea"])
            prompt_tokens += res_a.usage.total_tokens
            prompt_cost += res_a.cost_usd or 0.0
            quota.record_usage(res_a.model, res_a.usage.total_tokens)

            # 2. Classifier
            classifier_out, res_c = run_classifier(gateway, analysis)
            prompt_tokens += res_c.usage.total_tokens
            prompt_cost += res_c.cost_usd or 0.0
            quota.record_usage(res_c.model, res_c.usage.total_tokens)

            # 3. Question gate
            gate = run_question_gate(
                analysis, classifier_out.labels, max_questions=settings.max_questions_per_round
            )
            asked_cats = {q.category for q in gate.questions}
            expected = set(row["expected_blocking"])
            tp = len(asked_cats & expected)
            fp = len(asked_cats - expected)
            fn = len(expected - asked_cats)
            rec["questions"] = sorted(asked_cats)
            rec["tp_fp_fn"] = [tp, fp, fn]

            # 4. Pipeline execution (spec package + validation)
            pkg, trace = run_pipeline(
                gateway,
                f"ses_eval{row['id'].lower()}",
                row["idea"],
                answers=["Use sensible defaults."] * 8,
                max_questions=settings.max_questions_per_round,
            )
            for r in trace.results.values():
                prompt_tokens += r.usage.total_tokens
                prompt_cost += r.cost_usd or 0.0
                quota.record_usage(r.model, r.usage.total_tokens)

            v = pkg.validation
            ok = v.schema_ok and v.openapi_lint_ok and v.consistency_ok
            rec["valid"] = ok
            rec["tokens"] = prompt_tokens
            rec["cost_usd"] = prompt_cost
            total_tokens += prompt_tokens
            total_cost += prompt_cost

            if ok:
                rec["status"] = "ok"
                rec["quality_fail"] = False
            else:
                rec["status"] = "quality_fail"
                rec["quality_fail"] = True
                rec["warnings"] = pkg.validation.warnings[:3]

        except Exception as exc:  # noqa: BLE001
            rec["valid"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"[:250]
            if is_infra_error(exc):
                rec["status"] = "infra_aborted"
                rec["infra_aborted"] = True
                rec["quality_fail"] = False
                print(f"{row['id']}: INFRA_ABORTED ({rec['error']})", file=sys.stderr)
            else:
                rec["status"] = "quality_fail"
                rec["quality_fail"] = True
                rec["infra_aborted"] = False
                print(f"{row['id']}: QUALITY_FAIL ({rec['error']})", file=sys.stderr)

        per_prompt.append(rec)
        print(
            f"[{row['id']}] status={rec['status']} valid={rec.get('valid')} "
            f"asked={rec.get('questions')} (+{prompt_tokens} tok)",
            flush=True,
        )

        # Incrementally persist after each prompt
        _save_report(out_path, per_prompt, total_tokens, total_cost, started)

        # Rate-limiting pace sleep between prompts
        if args.pace_s and row != to_run[-1]:
            time.sleep(args.pace_s)

    report = _save_report(out_path, per_prompt, total_tokens, total_cost, started)
    print("\n==================== EVALUATION SUMMARY ====================")
    print(json.dumps({k: v for k, v in report.items() if k != "per_prompt"}, indent=2))
    print(f"Results written to: {out_path}")
    print("============================================================")
    return 0


def _save_report(
    out_path: Path,
    per_prompt: list[dict[str, Any]],
    total_tokens: float,
    total_cost: float,
    started: float,
) -> dict[str, Any]:
    # Calculate E1 and E2 metrics
    completed = [r for r in per_prompt if not r.get("infra_aborted")]
    valid_count = sum(1 for r in completed if r.get("valid") is True)
    invalid_count = sum(1 for r in completed if r.get("valid") is False)
    infra_count = sum(1 for r in per_prompt if r.get("infra_aborted") is True)
    quality_fail_count = sum(1 for r in per_prompt if r.get("quality_fail") is True)

    total_tp = sum(r.get("tp_fp_fn", [0, 0, 0])[0] for r in completed)
    total_fp = sum(r.get("tp_fp_fn", [0, 0, 0])[1] for r in completed)
    total_fn = sum(r.get("tp_fp_fn", [0, 0, 0])[2] for r in completed)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    validity_rate = valid_count / len(completed) if completed else 0.0

    report: dict[str, Any] = {
        "ran": len(per_prompt),
        "completed": len(completed),
        "infra_aborted": infra_count,
        "quality_fail": quality_fail_count,
        "e1": {
            "valid": valid_count,
            "invalid": invalid_count,
            "validity_rate": validity_rate,
        },
        "e2": {
            "micro_tp": total_tp,
            "micro_fp": total_fp,
            "micro_fn": total_fn,
            "micro_precision": precision,
            "micro_recall": recall,
        },
        "tokens_total_est": int(total_tokens),
        "cost_usd_reported": total_cost,
        "elapsed_s": round(time.time() - started, 1),
        "per_prompt": per_prompt,
    }
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
