# Evaluation Report (auto-generated from measured result files)

_Generated from evals/results/*.json — only measured numbers appear here; anything missing is NOT MEASURED. Frozen labels: evals/prompts/labels.jsonl (user approval pending)._

## E1/E2 — spec quality + question quality (prompts v2)
- ran: 22/30 prompts · model: gpt-oss-120b pipeline (Groq free tier)
- E1 validity: 22/22 (0%); invalid: 0; quota-aborted: 8 (resume-safe)
- E2 precision: None · recall: None (micro, category-level)
- tokens_total_est: 302801

## E3 — downstream lift
- builder model: `groq/openai/gpt-oss-20b`
- pass rate raw: 0.8333333333333334 (n=12) · optimized: 0.7272727272727273 (n=11) · lift: -0.10606060606060608

## E4 token benchmark — dev/regression set (tiny single-file tasks)

- model: `groq/qwen/qwen3.8-27b` · cells: 32 (pass 18 / fail 7 / retryable-turn-cap 7)

- `balanced`: n=8 pass_rate=75% tokens_in_avg=1670 tokens_out_avg=159 iterations=2.75 latency=0.09s
- `off`: n=7 pass_rate=71% tokens_in_avg=1067 tokens_out_avg=231 iterations=1.8571428571428572 latency=1.07s
- `safe`: n=10 pass_rate=70% tokens_in_avg=2302 tokens_out_avg=187 iterations=3.5 latency=0.12s

## E5 pass ablation — dev/regression set

- model: `groq/openai/gpt-oss-20b` · cells: 0 (pass 0 / fail 0 / retryable-turn-cap 0)

- `balanced`: n=4 pass_rate=100% tokens_in_avg=282 tokens_out_avg=816 iterations=1.0 latency=0.67s
- `balanced_no_P5`: n=4 pass_rate=100% tokens_in_avg=282 tokens_out_avg=816 iterations=1.0 latency=0.09s
- `balanced_no_P9`: n=4 pass_rate=100% tokens_in_avg=282 tokens_out_avg=816 iterations=1.0 latency=0.08s

## E6 — cost vs pass-rate Pareto
![pareto](results/pareto.png) · text table: results/pareto_table.txt

## Primary benchmark set (queued)
- tasks_e4b.json (4 multi-turn tasks with 3-5K-token tool logs) is the **primary benchmark set**; the 8 tiny tasks above are the **dev / regression set**. Primary-set cells are stamped (WP12) and run only after the dev-set paired rerun completes under the same code stamp.- Sets are never mixed: dev-set cells and primary-set cells are reported separately and never averaged together.
## Scope (WP15)
- Stage scope: EVAL_STAGES=S1,S2,S6.
- Experiments outside the scope are **NOT MEASURED: not run (free-tier quota / scope)** — E4b safe arm, E5 ablation, E3b multi-file lift, E4 tiny dev/regression set. They are not pending and not failed; they were cut by scope decision.

## Limitations
- Groq free tier: per-model 30 RPM / 1K RPD / 8K TPM / 200K TPD; several runs spanned multiple UTC days via the resumable quota-aware runners.
- Small n per config (see each table); single provider; tiny single-file tasks.
- Results are indicative, not statistically strong. No number on this page is estimated without the `_est` label; provider-reported usage is used everywhere else.