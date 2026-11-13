# Demo walkthrough (2 minutes)

Preconditions: repo cloned, `.env` with `GROQ_API_KEY`, `make up` healthy, `make migrate` done.

## 0:00 — Spec agent: idea → blocking question
```bash
.venv/Scripts/python -m app.cli "An expense splitter for roommates that records who owes whom" --json
```
Show: validation `lint_ok=True consistency_ok=True`, assumptions labelled overridable, and the
session stopping with blocking questions when run without `--auto-answer` (drop the flag).

## 0:30 — Durability: kill a worker, watch resume
```bash
# terminal 1: start a worker in the foreground against the local DB
set -a; . ./.env; set +a
PYTHONPATH=server DATABASE_URL=postgresql://app:app@localhost:5433/qo .venv/Scripts/python -m app.worker_main
# terminal 2: POST /sessions with a real idea (no auto-answer), then Ctrl+C the worker mid-run
# Restart the worker — the log shows done stages skipped (resume by input_hash), session finishes `ready`.
```

## 1:00 — SSE replay
```bash
curl -N "http://127.0.0.1:8001/sessions/<SID>/events"                 # all events
curl -N -H "Last-Event-ID: 3" "http://127.0.0.1:8001/sessions/<SID>/events"   # reconnect: no gaps
```

## 1:20 — Proxy before/after savings
```bash
curl -X POST http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" -H "x-optimizer-mode: safe" \
  -d '{"model":"groq/openai/gpt-oss-20b","messages":[{"role":"user","content":"Explain quicksort"}]}'
curl -X POST http://127.0.0.1:8001/v1/chat/completions ...  # same body again -> cache_hit: true
curl http://127.0.0.1:8001/stats    # tokens_baseline_est vs tokens_actual, cache_hits
```

## 1:45 — Grafana
Open http://localhost:3000 → Query Optimizer dashboard: tokens by stage, per-pass savings,
cache hit rate, proxy overhead p95, daily spend.
