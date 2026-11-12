# Query Optimizer Service

A **spec agent** plus a **token-optimizing proxy/MCP layer** that sits between coding agents (Claude Code, Cursor, custom agents) and LLMs.

Two products in one service:

1. **Spec agent (Release A):** turn a one-line idea into a validated spec package — master spec, decomposed sub-specs, and a versioned OpenAPI 3.1 contract — with blocking questions asked only when decisions are genuinely irreversible.
2. **Token optimizer (Release B/C):** an OpenAI-compatible proxy (`/v1/chat/completions`) that rewrites agent requests through an ordered chain of optimization passes, records real savings, and fails open.

## Quick start (10 minutes)

Requirements: Python 3.12, Docker Desktop, a Groq API key.

```bash
cd query-optimizer
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Windows Git Bash
# Linux/macOS: python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

cp .env.example .env          # put your GROQ_API_KEY in .env
make up                       # postgres + api (+ prometheus, grafana, caddy, worker, mcp)
make migrate
make test                     # 107 tests; DB tests skip if postgres is down
```

Try the spec agent end to end (real model):

```bash
.venv/Scripts/python -m app.cli "A recipe box where users save and tag recipes" --auto-answer --json
```

## Using the optimizer from any coding agent

Point your agent at the proxy — that is the only change:

```
base URL: http://localhost:8001        (instead of https://api.openai.com/v1)
```

```bash
curl http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-optimizer-mode: safe" \
  -d '{"model":"groq/openai/gpt-oss-20b","messages":[{"role":"user","content":"hi"}]}'
```

- Modes: `off` (passthrough + metrics), `safe` (lossless passes), `balanced` (default), `aggressive`.
- `x-optimizer-protect: <substring>` tags content passes must never touch.
- `POST /v1/optimize` — dry run: returns the rewritten payload and per-pass reports without calling a model.
- `GET /stats` — savings ledger: `tokens_baseline_est` vs provider-reported `tokens_actual`, cache hits, passthroughs.
- **Fail-open:** any optimizer failure forwards your original request unchanged.

### Passes (ordered chain)

| Pass | What it does | Ships in |
|---|---|---|
| P9 | Exact-match response cache (versioned; zero stale serves) | B ✅ |
| P4 | Tool-output compaction — ANSI stripped, deduped, **error lines always kept** | B ✅ |
| P5 | History compaction — decisions and open errors survive | C ✅ |
| P2 | Spec-as-context — only the relevant sub-spec per role | C ✅ |
| P3 | Context selection — BM25-lite repo ranking, file hints not dumps | C ✅ |
| P1 | Cache-aware assembly — stable prefix, byte-stable across calls | B ✅ |
| P7 | Output controls — per-stage max_tokens/stop, no-preamble style line | B ✅ |
| P6 | Edit-format enforcement — whole-file rewrites rejected and retried | C ✅ |
| P8 | Routing — cheap-first cascade; validator failure escalates to the strong model | C ✅ |
| P11 | Budget governor + per-pass circuit breakers | C ✅ |
| P10 | Prose compression | not implemented (aggressive-only; needs evaluation first) |

### MCP server

`python -m app.mcp_server` exposes five spec tools over stdio: `create_spec`, `answer_questions`, `revise_spec`, `get_contract`, `diff_versions`.

## API surface

`POST /sessions` (Idempotency-Key) · `GET /sessions/{id}` · `POST /sessions/{id}/answers` · `POST /sessions/{id}/revisions` · `GET /sessions/{id}/events` (SSE with Last-Event-ID replay) · `GET /sessions/{id}/diff` · `GET /sessions/{id}/export?format=json|markdown|openapi` · `POST /sessions/{id}/cancel` · `GET /metrics` · `GET /healthz` · proxy: `/v1/chat/completions`, `/v1/optimize`, `/stats`.

## Honest status

- Measured results: [evals/REPORT.md](evals/REPORT.md) — E1 validity **10/10 completed runs** (2 aborted by Groq free-tier 429s), E2 question recall **0.76** / precision **0.38** (precision limited by label vocabulary vs analyzer breadth — known, logged).
- E3 downstream lift and E4 before/after on a multi-turn agent session: harness slots exist, **not yet measured** (free-tier rate limits make paired runs infra-noisy).
- Single-tenant, single-region. `psycopg_pool` replaced by per-checkout connections on Windows (see docs/log.md).
- No secrets in code: everything from env; metric labels never contain session ids; savings are provider-reported usage, estimates labelled `_est`.

## Layout

```
server/app/           gateway/ (only place a provider SDK is imported)
                      pipeline/  stages/  schemas/  validators/  storage/  optimizer/
prompts/<stage>/vN.md  prompt version is part of each stage's input hash
config/models.yaml     stage -> model/temperature/max_tokens/fallbacks (pinned)
evals/                 30 labelled prompts, runner with budget guard, REPORT.md
observability/         prometheus.yml + provisioned Grafana dashboard
docs/                  PRD.md, PRD_v2.md, requirements.md, MILESTONES.md, log.md
```

Run `make lint` (ruff + mypy) and `make coverage` (≥80% gate on core packages — currently 93%).
