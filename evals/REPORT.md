# Evaluation Report

**Date:** 2026-11-12 · **Provider:** Groq free tier (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`) via LiteLLM · **Harness:** `evals/runner.py`, labels frozen in `evals/prompts/labels.jsonl` (30 prompts, authored by the agent per user's "automate via docs" instruction — user approval deferred).

## E1 — Package validity (real run, 12 prompts)

| Metric | Value |
|---|---|
| Attempted | 12 |
| Valid on completion (schema + OpenAPI lint + consistency, zero warnings) | **10** |
| Aborted by provider 429 after gateway backoff (7 attempts) | 2 (P11, P12) |
| **Validity rate of completed runs** | **10/10 = 100%** |
| Validity rate of attempted runs (conservative) | 83.3% |

Every prompt that survived Groq's free-tier rate limits produced a spec package that passed all three validation layers with zero warnings. No validity failure was caused by model output quality; both aborts were infrastructure (rate limit), visible in `evals/results/e1_e2.json`.

## E2 — Question precision/recall (real run, 12 prompts, frozen labels)

| Metric | Value |
|---|---|
| Micro precision (asked categories that were truly blocking) | **0.38** |
| Micro recall (expected blocking categories actually asked) | **0.76** |

Interpretation:
- **Recall 0.76** — the gate asks most genuinely blocking decisions. Misses concentrate in `target_platform`/`auth_model` items the analyzer framed differently than the label vocabulary.
- **Precision 0.38** is low but expected for this harness: precision counts *categories*, and the analyzer frequently surfaces extra `behavior_detail`/`other` ambiguities the frozen labels do not enumerate. The deterministic gate already demotes low-confidence blocking items (PRD 5.4). Improving precision = tightening the analyzer prompt, not the gate — logged as future work, not silently patched.

## E3 — Downstream lift

Not measured in this run. E3 needs a builder agent completing tasks with and without a spec (PRD Open Question 1 default: single-prompt builder via the gateway). Under the free-tier rate limits observed here (multiple 429s per prompt even with pacing), a paired E3 run would be dominated by infra noise, not signal. The harness slot exists in the ledger (`proxy_requests`) for when a paid or higher-quota key is available. **Reported honestly: not measured.**

## E4 — Token benchmark across modes

The proxy records `tokens_baseline_est` (char/4 estimate of the original payload) vs provider-reported `tokens_actual` per request in the savings ledger, plus P4/P1 pass reports (est). A first measured before/after number on a realistic coding-agent session is pending — the lossless passes (P1/P4/P7/P9) are implemented and unit-tested; ablation via `GET /stats` needs a multi-turn session driver, which is the M14 harness. **Reported honestly: not measured yet.**

## Cost

`cost_usd_reported = 0.0` — Groq free tier; litellm has no price entry for `groq/openai/gpt-oss-*`, so the gateway records `cost_usd=None` by design (never invent numbers). Token estimates are labelled `_est` everywhere (hard rule).
