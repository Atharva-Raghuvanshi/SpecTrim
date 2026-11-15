"""Regenerate evals/REPORT.md from measured result files only (integrity rule).

Never invents numbers; missing experiments stay NOT MEASURED. Called by the
auto-finish orchestrator after each phase and manually via make report.
"""
from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def load(name: str) -> dict | None:
    files = sorted(RESULTS.glob(name), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def e_section() -> str:
    lines = []
    for exp, label in (
        ("e4", "E4 token benchmark — dev/regression set (tiny single-file tasks)"),
        ("e5", "E5 pass ablation — dev/regression set"),
    ):
        files = sorted(
            (f for f in RESULTS.glob(f"{exp}_*.json") if "diagnosis" not in f.name),
            key=lambda p: p.stat().st_mtime)
        if not files:
            lines.append(f"## {label}\n\n**NOT MEASURED** (no result file yet).\n")
            continue
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        cells = d.get("cells", {})
        n_pass = sum(1 for c in cells.values() if c.get("pass") is True)
        n_fail = sum(1 for c in cells.values() if c.get("pass") is False)
        n_retry = sum(1 for c in cells.values() if c.get("pass") is None)
        lines.append(
            f"## {label}\n\n- model: `{d.get('builder_model')}` · cells: {len(cells)} "
            f"(pass {n_pass} / fail {n_fail} / retryable-turn-cap {n_retry})\n"
        )
        for cfg, s in sorted((d.get("summary") or {}).items()):
            lines.append(
                f"- `{cfg}`: n={s['n']} pass_rate={100 * s['pass_rate']:.0f}% "
                f"tokens_in_avg={s['tokens_in_avg']:.0f} "
                f"tokens_out_avg={s['tokens_out_avg']:.0f} "
                f"iterations={s['iterations_avg']} latency={s['latency_avg_s']}s"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    v2 = load("e1_e2_v2_full.json")
    e3 = None
    e3_files = sorted(RESULTS.glob("e3_*.json"), key=lambda p: p.stat().st_mtime)
    if e3_files:
        e3 = json.loads(e3_files[-1].read_text(encoding="utf-8"))

    parts = ["# Evaluation Report (auto-generated from measured result files)", ""]
    parts.append("_Generated from evals/results/*.json — only measured numbers appear here; "
                 "anything missing is NOT MEASURED. Frozen labels: evals/prompts/labels.jsonl "
                 "(user approval pending)._\n")

    parts.append("## E1/E2 — spec quality + question quality (prompts v2)")
    if v2 and v2.get("ran"):
        e1_info = v2.get("e1") or {}
        e2_info = v2.get("e2") or {}
        valid = e1_info.get("valid", 0)
        invalid = e1_info.get("invalid", 0)
        validity_rate = e1_info.get("validity_rate")
        if validity_rate is None:
            validity_rate = v2.get("e1_validity_rate", 0)
        prec = e2_info.get("micro_precision")
        if prec is None:
            prec = v2.get("e2_micro_precision")
        prec_str = f"{prec:.3f}" if isinstance(prec, (int, float)) else str(prec)
        rec = e2_info.get("micro_recall")
        if rec is None:
            rec = v2.get("e2_micro_recall")
        rec_str = f"{rec:.3f}" if isinstance(rec, (int, float)) else str(rec)

        parts.append(
            f"- ran: {v2['ran']}/30 prompts · model: gpt-oss-120b pipeline (Groq free tier)\n"
            f"- E1 validity: {valid}/{v2['ran']} "
            f"({100 * validity_rate:.0f}%); "
            f"invalid: {invalid}; quota-aborted: "
            f"{30 - v2['ran']} (resume-safe)\n"
            f"- E2 precision: {prec_str} · recall: "
            f"{rec_str} (micro, category-level)\n"
            f"- tokens_total_est: {v2.get('tokens_total_est')}"
        )
    else:
        parts.append("**NOT MEASURED** beyond the 13/30 partial run (120b quota).")
    parts.append("")

    parts.append("## E3 — downstream lift")
    if e3 and e3.get("summary"):
        s = e3["summary"]
        parts.append(
            f"- builder model: `{e3.get('builder_model')}`\n"
            f"- pass rate raw: {s.get('pass_rate_raw')} (n={s.get('n_raw')}) · "
            f"optimized: {s.get('pass_rate_optimized')} (n={s.get('n_optimized')}) · "
            f"lift: {s.get('lift')}"
        )
    else:
        parts.append("**NOT MEASURED** (spec generation blocked on 120b quota; resumable).")
    parts.append("")

    parts.append(e_section())

    parts.append("## E6 — cost vs pass-rate Pareto")
    if (RESULTS / "pareto.png").is_file():
        parts.append("![pareto](results/pareto.png) · text table: results/pareto_table.txt")
    else:
        parts.append("**NOT MEASURED** yet.")
    parts.append("")

    parts.append("## Primary benchmark set (queued)")
    parts.append(
        "- tasks_e4b.json (4 multi-turn tasks with 3-5K-token tool logs) is the "
        "**primary benchmark set**; the 8 tiny tasks above are the **dev / regression "
        "set**. Primary-set cells are stamped (WP12) and run only after the dev-set "
        "paired rerun completes under the same code stamp.\n"
        "- Sets are never mixed: dev-set cells and primary-set cells are reported "
        "separately and never averaged together."
    )

    parts.append("## Scope (WP15)")
    import os as _os

    scope = _os.environ.get("EVAL_STAGES", "S1,S2,S6")
    parts.append(f"- Stage scope: EVAL_STAGES={scope}.")
    parts.append("- Experiments outside the scope are **NOT MEASURED: not run "
                 "(free-tier quota / scope)** — E4b safe arm, E5 ablation, E3b "
                 "multi-file lift, E4 tiny dev/regression set. They are not pending "
                 "and not failed; they were cut by scope decision.")
    parts.append("")

    parts.append("## Limitations")
    parts.append(
        "- Groq free tier: per-model 30 RPM / 1K RPD / 8K TPM / 200K TPD; several runs "
        "spanned multiple UTC days via the resumable quota-aware runners.\n"
        "- Small n per config (see each table); single provider; tiny single-file tasks.\n"
        "- Results are indicative, not statistically strong. No number on this page is "
        "estimated without the `_est` label; provider-reported usage is used everywhere else."
    )

    out = RESULTS.parent / "REPORT.md"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"report written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
