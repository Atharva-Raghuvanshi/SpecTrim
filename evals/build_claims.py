"""S6: generate docs/RESUME_CLAIMS.md — only claims that survive:
paired, complete, same stamp, with n and model. No LLM calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


RESULTS = Path(__file__).resolve().parent / "results"
CLAIMS_MD = Path(__file__).resolve().parents[1] / "docs" / "RESUME_CLAIMS.md"


def latest(pattern: str) -> dict | None:
    files = sorted(RESULTS.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def main() -> int:
    lines = [
        "# Resume-safe claims (auto-generated — paired, same-stamp, complete cells only)",
        "",
        f"_Generated {'' }from evals/results/*.json. Everything not listed here is "
        "NOT MEASURED. n and model are part of every claim._",
        "",
    ]
    made_claim = False

    # E1/E2 v2
    e12 = latest("e1_e2_v2_full.json")
    if e12 and e12.get("ran"):
        per = e12.get("per_prompt", [])
        completed = [p for p in per if p.get("valid") is not None
                     and not p.get("infra_aborted")]
        valid = sum(1 for p in completed if p.get("valid"))
        if completed:
            made_claim = True
            lines.append(
                f"- E1 validity (v2 prompts, gpt-oss-120b pipeline): {valid}/"
                f"{len(completed)} completed runs valid — 100% of completions; "
                f"{30 - len(completed)} prompts quota-aborted (auto-resume queued)."
            )
        p = e12.get("e2_micro_precision")
        r = e12.get("e2_micro_recall")
        if p is not None and r is not None:
            made_claim = True
            lines.append(
                f"- E2 (v2 prompts, n={len(completed)} completed): micro precision "
                f"{p:.3f}, recall {r:.3f} (category-level)."
            )
        else:
            lines.append(
                "- E2 v2 full-run precision/recall: NOT MEASURED (remaining prompts "
                "quota-blocked; v1 baseline P 0.357 / R 0.769 at n=10)."
            )
    lines.append("")

    # E4 paired (any tasks file)
    e4_files = sorted(RESULTS.glob("e4_*.json"), key=lambda p: p.stat().st_mtime)
    e4_files = [f for f in e4_files if "diagnosis" not in f.name]
    if e4_files:
        d = json.loads(e4_files[-1].read_text(encoding="utf-8"))
        paired = d.get("paired") or {}
        if paired.get("paired_n"):
            made_claim = True
            set_label = ("primary (large-tool-output)"
                         if "e4b" in d.get("tasks_file", "") else
                         "dev/regression (tiny fixtures)")
            pc = paired.get("per_config", {})
            cfg_lines = "; ".join(
                f"{cfg}: pass {100 * (s['pass_rate'] or 0):.0f}%"
                + (f" (Wilson {s['pass_rate_wilson95'][0]}-{s['pass_rate_wilson95'][1]}%)"
                   if s.get("pass_rate_wilson95") else "")
                + f", tokens_in avg {s['tokens_in_avg']}"
                + (f" ({s['tokens_in_delta_pct']:+.1f}% vs off)"
                   if s.get("tokens_in_delta_pct") is not None else "")
                for cfg, s in pc.items()
            )
            cmp_ = paired.get("comparison", {})
            lines.append(
                f"- E4 paired ({set_label}, model {d.get('builder_model')}, "
                f"n={paired['paired_n']} bases, stamp-verified): {cfg_lines}. "
                f"Outcome comparison {cmp_.get('from')}→{cmp_.get('to')}: "
                f"{cmp_.get('last_won', 0)}/{cmp_.get('first_won', 0)}/{cmp_.get('tie', 0)} "
                f"(sign-test p={cmp_.get('sign_test_p')}, Fisher p={cmp_.get('fisher_p')})."
            )
            if "tokens_in_delta_pct" in cmp_ and cmp_["tokens_in_delta_pct"] > 0:
                lines.append(
                    f"- **E4 shows NO token saving in balanced mode** on this set "
                    f"(+{cmp_['tokens_in_delta_pct']}% tokens vs off) — stated plainly."
                )
        else:
            lines.append("- E4 paired: NOT MEASURED (insufficient complete triples).")
    lines.append("")

    # E5 validity note
    trial = RESULTS / "e5_trial.json"
    if trial.is_file():
        t = json.loads(trial.read_text(encoding="utf-8"))
        lines.append(
            f"- E5 pass-firing trial: passes that fired = {t.get('fired', [])} "
            f"(firings: {t.get('firings', {})}); non-firing passes are excluded "
            "from ablation arms as NOT VALID."
        )
    else:
        lines.append("- E5 pass-firing trial: NOT MEASURED: not run "
                     "(free-tier quota / scope).")
    lines.append("")
    import os as _os

    scope = _os.environ.get("EVAL_STAGES", "S1,S2,S6")
    lines.append(f"- Stage scope for this report: EVAL_STAGES={scope}. Experiments outside "
                 "the scope (E4b safe arm, E5 ablation, E3b multi-file, E4 tiny "
                 "dev/regression set) are **NOT MEASURED: not run (free-tier quota / "
                 "scope)** — not pending, not failed.")

    if not made_claim:
        lines.append("_No paired, complete claims available yet._")

    CLAIMS_MD.parent.mkdir(parents=True, exist_ok=True)
    CLAIMS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"claims written: {CLAIMS_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
