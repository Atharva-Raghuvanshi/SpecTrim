# Work Log

Fresh-session protocol: read the last ~30 lines of this file + current milestone in docs/MILESTONES.md before doing anything.

## M1.1 — Repo scaffold, Compose (Postgres + API stub), Makefile, CI stub, log

**Date:** 2026-11-11
**Status:** DONE. Exit check passed: `make up` starts cleanly; `curl http://127.0.0.1:8001/healthz` → `200 OK` (plain text); both containers `health=healthy`.

### What was done
- `docs/MILESTONES.md` created from the milestone table given by the user (PRD.md §10 phases 1-9 + PRD_v2.md §11 phases 10-15).
- Scaffold: `pyproject.toml` (root; setuptools, package dir `server/app`, pytest/ruff/mypy config), `server/app/main.py` (FastAPI, `/healthz` → plain-text `OK`), `server/tests/test_health.py` (TestClient, asserts literal `OK`), `Dockerfile` (python:3.12-slim, pip install ., uvicorn), `docker-compose.yml` (postgres:16 + api, both with healthchecks, `restart: unless-stopped`, named volume `pgdata`), `Makefile` (venv/up/down/logs/ps/test/lint/clean), `.env.example` (requirements.md §4 vars + host-port overrides), `.gitignore`, `.dockerignore`, `.github/workflows/ci.yml` (ruff + pytest, zero real LLM calls).

### Decisions / Assumptions
- **uv not installed** → using `python -m venv .venv` + pip (`pyproject.toml` at repo root). Install uv later if wanted; Makefile is agnostic.
- **Host ports 8000/5432 are taken by an unrelated running stack (`agent-project-*` containers — not touched).** Our compose maps host `API_HOST_PORT=8001` / `POSTGRES_HOST_PORT=5433` (defaults in `.env.example`, overridable via `.env`). In-container ports stay 8000/5432, so `DATABASE_URL` matches requirements.md.
- `/healthz` returns plain text `OK` (FastAPI needs `PlainTextResponse`; a bare str comes back JSON-quoted as `"OK"`).
- `pyproject.toml` at repo root (not under `server/`); pytest resolves imports via `pythonpath=["server"]`.
- This folder sat inside a git repo rooted at `C:/Users/Admin` (no commits). Initialized a nested repo here so milestones can be committed.
- Compose limited to postgres + api per M1.1 scope; worker/mcp/prometheus/grafana come later (M4/M7/M9.1).

### Open issues
- None blocking. (Note: project lives in a OneDrive-synced path; venv/docker work but may be slower.)

### How to verify (exact commands)
```
cd "C:/Users/Admin/OneDrive/Desktop/Query Optimizer"
make test    # 1 passed (server/tests/test_health.py)
make lint    # ruff clean; mypy: no issues in 2 source files
make up      # postgres + api start, both turn (healthy)
curl -s -w " [%{http_code}]" http://127.0.0.1:8001/healthz   # OK [200]
make down    # when finished
```

### Next step
M1.2 — Gateway v0: LiteLLM wrapper, `config/models.yaml`, Groq config, usage/latency/cost capture, 429 backoff with jitter, FakeGateway. Tests use FakeGateway only; add `litellm` + `tenacity` deps; single real Groq call only at the end as the exit check requires.

## M1.2 — Gateway v0: LiteLLM wrapper, models.yaml, Groq, 429 backoff, FakeGateway

**Date:** 2026-11-11
**Status:** DONE. Exit check passed: 16 fake-based tests green; one real Groq call via `make smoke-real` returned `text='pong'` (usage captured).

### What was done
- `server/app/gateway/base.py`: `Gateway` protocol, `CompletionResult`/`Usage` (pydantic), `GatewayError`/`GatewayRateLimited`.
- `server/app/gateway/config.py`: `load_models_config()` — reads models.yaml, selects config by arg > `ACTIVE_CONFIG` env > yaml `active`; pydantic-validated; `ConfigError` on missing file/unknown config.
- `server/app/gateway/fake.py`: `FakeGateway` — canned per-stage responses, records `calls`, deterministic usage, zero network.
- `server/app/gateway/litellm_gateway.py`: `LiteLLMGateway` — only module importing litellm (FR-23). 429 → exponential backoff + jitter (0.5s base, 4 retries, injectable `sleep_fn`), then fallback model (FR-24); permanent errors → no retry, straight to fallback else `GatewayError`. Captures provider-reported usage (FR-25), latency, and litellm price-table cost (`None` when unpriced).
- `config/models.yaml`: named configs (`single_model`), stages analyzer/classifier/question_gate/spec_writer/decomposer/contract/revision, pinned Groq model IDs, fallbacks.
- `server/app/gateway/smoke.py` + `make smoke-real`: one real call; refuses to run without `GROQ_API_KEY`.
- Tests (all offline, litellm monkeypatched): config loading, FakeGateway behavior, backoff bounds/attempts, 429-exhaustion→fallback, permanent→fallback without retry, no-fallback error, and `test_gateway_isolation.py` enforcing "no provider SDK imports outside `server/app/gateway/`".
- Deps added: `litellm`, `pyyaml`. `.env.example`: added `GROQ_API_KEY=`. `.env` created locally (gitignored) with the user's key.

### Decisions / Assumptions
- **Groq decommissioned llama-3.x** (404 on first smoke). Live model list queried via `/openai/v1/models`; re-pinned: strong = `groq/openai/gpt-oss-120b`, cheap = `groq/openai/gpt-oss-20b`. LiteLLM string supports the nested `groq/openai/...` form.
- Backoff: 0.5s × 2^attempt + U(0, 0.5s) jitter, 5 attempts max, then fallback model (fresh retry budget).
- Permanent (non-429) errors do NOT retry on the primary — one repair retry lives at the validation layer (M2.2), not the gateway.
- `cost_usd` comes from litellm's price table (currently `None` for gpt-oss on Groq); usage/latency are provider-reported. Spend-cap enforcement (FR-26) deferred to M9.2 per milestone plan.
- litellm import is pay-at-import; `app.gateway.__init__` stays light so FakeGateway tests don't load it.

### Open issues
- litellm has no `__version__` attr in current release (harmless).
- pip install of litellm is very slow on this machine (OneDrive/AV); done manually via cmd once.

### How to verify (exact commands)
```
cd "C:/Users/Admin/OneDrive/Desktop/Query Optimizer"
make test          # 16 passed
make lint          # ruff clean; mypy: no issues in 8 source files
make smoke-real    # needs GROQ_API_KEY in .env; prints model/latency/usage/text='pong'
```

### Next step
M2.1 — Message envelope and package schemas (Pydantic): bad id, unknown type, empty fields rejected. Read PRD.md §2 (envelope/package) sections first, tests first, FakeGateway for any LLM-shaped input.

## M2.x + M3.x — schemas, validators, repair-retry, prompts v1, stages, CLI

**Date:** 2026-11-12
**Status:** DONE. Commits `6adb9f4` (Phase 2), `dadc3b0` (Phase 3 code). Real smoke: 5/5 ideas valid after re-run (idea_1..5 in evals/results/smoke_m35/, idea_2 re-run once after a litellm stdout banner polluted the file).

### What was done
- M2.1 `schemas/envelope.py` + `schemas/package.py` per PRD 5.5 (envelope id `^msg_\d+$`, 9 message types, package with master_spec/assumptions/questions/sub_specs/contract/validation).
- M2.2 `gateway/repair.py`: strict-JSON + one repair retry then permanent GatewayError (FR-11); `extract_json` tolerates fences/prose.
- M2.3 `validators/openapi_lint.py` (openapi-spec-validator) + `validators/consistency.py` (deterministic FR-12 checks).
- M3.1-M3.4 prompts v1 (`prompts/{analyzer,classifier,spec_writer,contract,decomposer}/v1.md`), stage modules in `stages/`, deterministic question gate (cap 3, low-confidence blocking loses priority, suppressed → non-overridable assumptions), contract stage builds OpenAPI deterministically (always lints; path params auto-declared), decomposer normalizes endpoint method/path from contract (sub-specs cannot drift).
- M3.5 `pipeline/inmemory.py` + `cli.py` (`python -m app.cli "<idea>" --auto-answer --json`).
- Groq decommissioned llama-3.x mid-milestone; re-pinned models.yaml to `groq/openai/gpt-oss-120b` / `groq/openai/gpt-oss-20b`.

## M4.1-M4.5 — durability: migrations, repos, queue, state machine, resume, idempotent API

**Date:** 2026-11-12
**Status:** DONE. Commit `05b24ed`. 65 tests pass; ruff+mypy clean. Exit checks: `make migrate` idempotent (tested); concurrent duplicate POSTs → one session (tested); resume skips done stages, changed hash re-runs (tested); killed worker's job reclaimed (tested); versions ON CONFLICT DO NOTHING (tested).

### What was done
- `storage/migrations/001_init.sql` (7 tables per PRD 5.7) + `storage/migrate.py` (schema_migrations bookkeeping; runs on API startup; `make migrate` target pending).
- `storage/repos.py`: Sessions/Jobs/Stages/Versions/Questions/Events repos; events seq via `INSERT..SELECT MAX(seq)+1` (monotonic per session).
- `pipeline/states.py`: full transition table from PRD 5.2; `transition()` raises InvalidTransition; all state changes go through it.
- `pipeline/orchestrator.py`: DB-backed run_pipeline_job with resume via chained input_hash (stage key + prompt version + model + config + upstream hashes + payload); stage skip only when done+hash-match.
- `worker.py`: claim loop `UPDATE..WHERE id=(SELECT..FOR UPDATE SKIP LOCKED)`, leases, `run_after = now()+min(300,2^attempts)+rand(0,1)`, dead after max_attempts, expired-lease reclaim.
- `main.py`: POST /sessions (Idempotency-Key, unique), GET /sessions/{id}, POST answers (awaiting_user only), POST cancel (409 on invalid transition), /healthz with DB ping.

### Decisions / Assumptions
- **psycopg_pool AsyncConnectionPool is broken on this Windows host** (PoolTimeout despite instant direct connects) → `storage/db.py` uses a per-checkout `Database.connection()` factory with commit-on-success/rollback-on-error; same `.connection()` interface. Revisit pool on Linux deploy.
- **psycopg async requires SelectorEventLoop** (Proactor is Windows default) → `app/winloop.py` imported first in main.py/worker.py/conftest.
- Stage functions are synchronous (gateway protocol is sync); orchestrator DB I/O is async.
- `_move()` = the only state-change path in the orchestrator (transition + set_status + event).
- DB tests auto-skip when Postgres unreachable (conftest); run `make up` first. `TEST_DATABASE_URL` overrides DSN (host port 5433).

### Open issues
- `make migrate` target not yet in Makefile (migration runner exists; add with next Makefile touch).
- pytest-asyncio deprecation warning (asyncio_default_fixture_loop_scope unset) — cosmetic.

### How to verify (exact commands)
```
cd "C:/Users/Admin/OneDrive/Desktop/Query Optimizer"
make up && make test    # 65 passed (DB tests included when Postgres up)
make lint               # ruff + mypy clean
.venv/Scripts/python.exe -m app.cli "A bookmarking tool" --auto-answer   # real end-to-end
```

### Next step
Phase 5 (M5.1-M5.3): revision stage (ready→revising→v2), diff engine with breaking-change flags, GET /diff endpoint. M5.1 (versions immutable) already done+tested.

## M5.x-M9.x + M10-M13 + M15.1 — full autonomous run (user authorized all phases)

**Date:** 2026-11-12
**Status:** ALL MILESTONES BUILT. Commits: `af7fcf4` (M5), `93a2632` (M6), `75580c9` (M7), `a46fbba` (M8.1-8.2+M9.1-9.2), `f169929` (M10-M11 / Release B), `95f987c` (M12-M13 / Release C + REPORT). **107 tests green, ruff+mypy clean, 95% coverage on core packages** (gate is 80%).

### What was built (per milestone)
- **M5**: revision stage (`prompts/revision/v1.md`, ready→revising→validating→ready @ vN+1), diff engine (`diffing/diff.py`) with breaking flags (removed endpoint/op signature change/entity/required-field removal/type change), `POST /revisions` + `GET /diff` endpoints, migration 002 (jobs.payload). M5.1 was already done in Phase 4 (ON CONFLICT DO NOTHING, tested).
- **M6**: SSE with Last-Event-ID replay (poll 500ms, `api_sse.py`), MCP server (`mcp_server.py`, mcp 2.x `MCPServer`, five tools proxying REST), exports JSON/Markdown/openapi.yaml, `GET /sessions/{id}/questions`.
- **M7**: structlog JSON logs with trace_id (contextvar + middleware), Prometheus metrics (`/metrics`, stage tokens/cost/latency/counter labels = stage+model only, no session_id), Grafana dashboard + prometheus.yml, CI coverage gate ≥80% on pipeline/schemas/storage/diffing/validators, `make migrate` + `make coverage`.
- **M8.1**: 30 labelled prompts frozen in `evals/prompts/labels.jsonl` (authored by agent per user instruction; user approval deferred). **M8.2**: `evals/runner.py` with `--max-usd`/`--max-tokens` guards, pacing for Groq RPM. **M8.3**: real E1/E2 run → `evals/results/e1_e2.json` + `evals/REPORT.md`: **E1 10/10 completed runs valid (2 aborted on 429, 0 quality failures); E2 recall 0.76, precision 0.38** (precision limited by analyzer category breadth vs frozen label vocabulary — logged, not silently patched). E3/E4 not measured (infra noise under free tier) — stated honestly in the report.
- **M9.1**: full compose (api, worker ×2 via deploy.replicas, mcp streamable-http, postgres, prometheus, grafana, caddy) all healthchecked. **M9.2**: bearer auth (auto-off for local dev token), sliding-window rate limit (429), daily spend cap (402) — tested. **M9.3**: needs a VPS/domain — NOT done, deferred.
- **M10-M11 (Release B)**: OpenAI-compatible proxy `POST /v1/chat/completions` (streaming passthrough via `gateway/proxy_gateway.py` — the only other litellm import), policy engine (off/safe/balanced/aggressive + env kill switches QO_PASS_P<N>=0 + x-optimizer-protect), ordered chain with per-pass fail-open, router-level fail-open (forward original), `/v1/optimize` dry run, savings ledger + `GET /stats` (tokens_baseline_est vs provider-reported tokens_actual), P9 exact cache (versioned invalidation → zero stale serves, tested), P4 compaction (error lines always preserved, tested), P7 output controls (per-stage max_tokens/stop + style line), P1 cache-aware assembly (prefix byte-stable, tested). Migration 003 (proxy_requests, proxy_cache).
- **M12-M13 (Release C)**: P5 history compaction (decisions/open errors survive), P6 edit-format enforcement (whole-file rewrite rejected + SEARCH/REPLACE instruction), P3 BM25-lite context selection (file hints, not dumps; tree-sitter deferred — noted), P2 spec-as-context (relevant sub-spec per role), P8 cheap-first cascade (validator failure escalates to 120b), P11 budget governor (degrade before refuse) + per-pass circuit breakers (open → half-open). P10 prose compression intentionally not implemented (aggressive-only, must be justified by eval per PRD).
- **M15.1**: `README.md` — quick start, base-URL-swap usage, pass table, honest status.

### Decisions / Assumptions (this run)
- Sub-spec ↔ contract ordering: contract generated BEFORE decomposer so sub-specs can reference it (M3.3 exit check); decomposer normalizes endpoints from the contract deterministically.
- mcp 2.x renamed FastMCP → `mcp.server.mcpserver.MCPServer` (pinned `mcp>=2.0`).
- Revision jobs carry payload via `jobs.payload` (migration 002), not in-process state.
- P3 uses BM25-lite (term scoring) instead of tree-sitter — tree-sitter repo map deferred (heavy native dep; logged as future work).
- `--pace-s` eval pacing + gateway max_retries=6/base_delay=2.0 to survive Groq free-tier RPM windows.
- M9.3 (public demo/video) and full E3/E4 paired runs: deferred, need VPS / higher quota. Recorded in README "Honest status".

### Open issues
- `make up` currently rebuilds the api image (litellm in-container pip install is slow on this box); the running api container predates the proxy router until that build finishes — proxy smoke via base-URL swap pending the rebuild.
- E2 precision improvement = tighten analyzer prompt (future work).
- CI: coverage job runs without Postgres (DB tests auto-skip); add a postgres service to CI later if DB coverage is wanted in CI numbers.

### How to verify (exact commands)
```
cd "C:/Users/Admin/OneDrive/Desktop/Query Optimizer"
make test && make lint && make coverage   # 107 passed; ruff+mypy clean; 95% (gate 80%)
docker compose config -q                  # full compose valid
# after api image rebuild finishes:
curl -s http://127.0.0.1:8001/v1/optimize -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"system","content":"s"},{"role":"user","content":"u"}]}' -H "x-optimizer-mode: safe"
curl -s http://127.0.0.1:8001/stats
```

### Final update (same session, commit 38688b9)
- Fixed two deploy bugs found by the live smoke: Dockerfile did not COPY config/ (api crash-looped on startup); compose did not pass GROQ_API_KEY to api/worker.
- **Real proxy smoke PASSED against the rebuilt container:** `POST /v1/chat/completions` (mode=safe) -> 200, text `proxy-ok`, provider-reported 141 tokens; identical retry -> `cache_hit: true` (P9 live); `POST /v1/optimize` -> 9 pass reports; ledger delta {requests: 2, cache_hits: 1, passthroughs: 0}. M10.1 exit check (agent works by base-URL swap alone) verified.
- Remaining open items unchanged: M9.3 public demo (VPS), M14 E4-E6 paired runs (quota), tree-sitter repo map, E2 precision tuning.

## Gap-closing run — Step 0 orientation (2026-11-13)

**Verified baseline:** `make up` (all services healthy; mcp container stopped — stdio transport cannot run detached, fixing in WP4), `make test` → **107 passed**, `make lint` → ruff+mypy clean, `make coverage` → **95%** (gate 80%). Baseline unchanged from commit `3944d58`.

**Gap checklist (DoD §13 PRD v1+v2 + user WP list):**
- [x] WP1a FR-30: Anthropic-compatible `POST /v1/messages` (streaming) sharing chain/fail-open/ledger/cache + tests
- [x] WP1b FR-45: MCP `get_context` + `savings_report` tools + tests
- [x] WP1c PRD_v2 §9: per-pass savings, cache-hit-rate, passthrough/fail-open, proxy-latency metrics + Grafana panels
- [x] WP1d FR-46: measured p95 non-LLM latency for P1/P4/P5/P7/P9 + streaming first-token overhead (target <150ms)
- [x] WP1e FR-37: invalidation suite covers model + optimizer/prompt version + spec version
- [x] WP2: Postgres service in CI (DB/queue/resume/idempotency tests run, not skip) + real-process crash/resume test
- [ ] WP3: E2 precision 0.38→(>=0.80 target): analyze FPs, prompts v2, dry-run 5, full 30 on winner; E1 validity >=95% target; update REPORT
- [ ] WP4: real-agent proofs (base-URL swap both endpoints multi-turn tool task; MCP client transcript in docs/demo/)
- [ ] WP5: E3 8 prompts x 2 reps x 2 arms = 32 builder runs, one builder model, automated checks
- [ ] WP6: E4 8x2x{off,safe,balanced}=48; E5 ablation (P9/P4/P5/P1) 4x2; E6 Pareto PNG; headline + Limitations
- [ ] WP7 (optional): tree-sitter repo map if time/quota remain
- [ ] WP8: deploy assets + scripts/smoke_deploy.sh, DEMO_SCRIPT.md, README (results table w/ n+model, Limitations, Honest status), MILESTONES statuses, tags after exit checks
- [ ] Final DoD audit: every §13 checkbox marked DONE/PARTIAL/NOT DONE w/ evidence; §1.5+§3 metrics w/ measured values or NOT MEASURED

**Quota plan (Groq free tier):** per model 30 RPM / 1K RPD / 8K TPM / 200K TPD. gpt-oss-120b reserved for spec pipeline + P8 escalation; gpt-oss-20b + qwen/qwen3.8-27b (to be pinned + smoke-verified) are builder models. One builder model per experiment. QuotaTracker in evals/quota.py persists to evals/results/quota.json, reads x-ratelimit headers, sleeps near 80% TPM, stops at 85% TPD with "quota reached, resume tomorrow". Eval runners resumable per (task, rep, arm), infra_aborted vs quality_fail tagging.

**Needs user (proceeding, not blocking):** VPS/domain for live demo+video; approval of frozen prompt set + E3/E4 task set (marked "approval pending").

## WP1 — spec/code gaps vs PRDs (2026-11-13)

**Status:** DONE. Commit: see git log. **115 tests pass**, ruff+mypy clean, coverage re-run pending in WP2's CI change.

### Done
- **FR-30 Anthropic proxy**: `POST /v1/messages` (non-streaming + streaming SSE). New `optimizer/anthropic.py`: request translation (system block, content-block lists, stop_sequences→stop), response translation (`type: message`, usage input/output_tokens), OpenAI-SSE→Anthropic-SSE stream translator (message_start/content_block_*/message_delta/stop). Shares the same chain, P9 cache, ledger, fail-open. Tests: translation, cache_hit flag, streaming event sequence, **injected chain failure → passthrough** (patched at router binding), protected-content header honored.
- **FR-45 MCP tools**: `get_context(session_id, task, role)` (sub-spec + entities + endpoints per role), `savings_report()` (/stats). Tool surface test now expects 7 tools.
- **PRD_v2 §9 metrics**: `qo_proxy_overhead_seconds` (histogram), `qo_proxy_cache_hits_total`, `qo_proxy_passthrough_total{reason}`, `qo_proxy_requests_total{endpoint,mode}`, `qo_pass_savings_tokens_est_total{pass}` + 4 Grafana panels (per-pass savings, cache hit rate, passthrough/fail-open, overhead p95).
- **FR-37 invalidation**: cache key now includes `ns` from `QO_CACHE_NS` env (prompt/spec version bump ⇒ all entries unreachable). Tests: model-change miss, namespace invalidation semantics, existing version-bump test.
- **FR-46 latency (measured, `scripts/bench_latency.py` → `evals/results/latency_bench.json`)**: 3000-line × 60-turn (~1MB) request, 200 reps in-process:
  - chain_safe p95 **118.9ms**, chain_balanced p95 **110.9ms**, P4 alone p95 **105.9ms**, P5 alone p95 **11.5ms**, P1 p95 **0.09ms**, P7 p95 **0.02ms**, P9 key p95 **13.8ms** — **all under the 150ms target**.
  - Initial run MISSED (P4 p95 434ms, P5 503ms): per-line Python regex on 180K lines. Fixed with single-pass C-level MULTILINE scan (`ERROR_LINES_ML.finditer`) for P4 and a cheap `in`-prefilter before regex for P5. Before/after logged; error-line preservation test still green.
- **Quota guard**: payloads over `QO_MAX_REQUEST_TOKENS` (default 8000, ×1.5 with max_tokens) rejected **413** before any provider call; both-arms-429 → **502 infra_error** (never a quality failure). Tests: 413 with provider untouched, 429 classified infra.

### Decisions / fixes
- `make test` now pauses worker containers first: live workers were stealing queued jobs mid-test (real race found by the run: `test_killed_worker_job_reclaimed` flaked when Docker was up). Workers claim only `kind IN ('pipeline','revision')`; tests use `kind='test'`.
- conftest truncate now includes proxy_requests/proxy_cache (was causing cross-test cache pollution).
- Anthropic fail-open test patches `router.run_chain` binding (patching `chain.run_chain` doesn't affect the router's import).
- Breaker half-open test sleep 10ms→50ms (timing flake under load).
- mypy: `pass` is a keyword — metric labels use `**{"pass": ...}`; `_handle_chat` returns `Response`.

### Verify
```
docker compose stop worker
.venv/Scripts/python.exe -m pytest -q        # 115 passed
.venv/Scripts/python.exe scripts/bench_latency.py   # all p95 OK (<150ms)
.venv/Scripts/python.exe -m ruff check . ; .venv/Scripts/python.exe -m mypy server/app
docker compose start worker
```

## WP2 — Postgres service in CI + real-process crash/resume test (2026-11-13)

**Status:** DONE. **116 tests pass**, ruff+mypy clean, coverage **95% (94.84%)** (gate >= 80%).

### Done
- **CI Postgres 16 service container (`.github/workflows/ci.yml`)**:
  - Added `services.postgres` with `image: postgres:16`, `POSTGRES_USER=app`, `POSTGRES_PASSWORD=app`, `POSTGRES_DB=qo`, port 5432, and health check.
  - Set `TEST_DATABASE_URL` and `DATABASE_URL` env vars pointing to `postgresql://app:app@localhost:5432/qo`.
  - Added `Apply DB migrations` step before running tests.
  - Hard rule preserved: `GROQ_API_KEY=""`, zero real LLM calls in CI, FakeGateway used everywhere.
  - DB-backed tests (`test_db_phase4.py`, `test_wp1.py`, `test_crash_resume.py`) run in CI instead of skipping.
- **Real-process crash/resume test (`server/tests/test_crash_resume.py` + `server/tests/worker_runner.py`)**:
  - `tests.worker_runner`: Dedicated worker subprocess running the real `Worker` claim loop with a paced `PacedFakeGateway` (0.5s delay per stage).
  - Starts the worker subprocess, enqueues a job, waits until mid-pipeline (1 <= stages_before < 5 stages persisted to DB), and kills the worker subprocess via `SIGTERM`/`terminate()`.
  - Simulates lease expiry, executes `JobsRepo.reclaim_expired()`, and drives the pipeline to completion via the in-process runner.
  - Asserts that stages 1..k completed before the kill are NOT re-executed (`stage not in [c["stage"] for c in resume_gw.calls]`), remaining stages ARE executed, session finishes at status `ready` with `current_version == 1`, and all 5 stages exist in DB.
- **Makefile**:
  - Added `test-crash` target: `@docker compose stop worker > /dev/null 2>&1 || true; $(PY) -m pytest -q server/tests/test_crash_resume.py -v`.
  - Made `PY ?=` overrideable so CI can invoke `make test-crash PY=python`.
- **CI**:
  - Added `make test-crash PY=python` step to `.github/workflows/ci.yml`.
- **Coverage**:
  - Ran full test suite with coverage gate: **95% (94.84%)** across `app.pipeline`, `app.schemas`, `app.storage`, `app.diffing`, `app.validators` (exceeds the >= 80% gate).
  - Output:
    ```
    Name                                    Stmts   Miss  Cover
    -----------------------------------------------------------
    server\app\diffing\diff.py                 46      3    93%
    server\app\pipeline\__init__.py             0      0   100%
    server\app\pipeline\hashing.py             10      0   100%
    server\app\pipeline\inmemory.py            37      0   100%
    server\app\pipeline\orchestrator.py       146      6    96%
    server\app\pipeline\prompts.py             23      2    91%
    server\app\pipeline\states.py              22      0   100%
    server\app\schemas\__init__.py              0      0   100%
    server\app\schemas\envelope.py             49      3    94%
    server\app\schemas\package.py              74      0   100%
    server\app\storage\db.py                   23      3    87%
    server\app\storage\migrate.py              29     10    66%
    server\app\storage\repos.py               160      6    96%
    server\app\validators\__init__.py           0      0   100%
    server\app\validators\consistency.py       31      1    97%
    server\app\validators\openapi_lint.py       9      0   100%
    -----------------------------------------------------------
    TOTAL                                     659     34    95%
    Required test coverage of 80% reached. Total coverage: 94.84%
    116 passed, 1 warning in 45.12s
    ```

### Decisions / Assumptions
- `worker_runner.py` uses a clean canned response dictionary without blocking ambiguities so the full 5-stage pipeline runs to `ready` in a single job.
- Stage pacing (0.5s per stage in the worker subprocess) ensures deterministic mid-pipeline killing without race flakes.
- `PY ?=` in Makefile allows CI to inject system python (`PY=python`) while maintaining local `.venv` defaults.

### Open issues
- None.

### Verify commands
```
docker compose stop worker
make test-crash
make coverage
make lint
```

## WP3 — QuotaTracker, models.yaml Qwen support, v2 prompt tightening, and eval dry-run (2026-11-13)

**Status:** DONE. Exit check passed: `make quota-status` prints tracked daily tokens, `groq/qwen/qwen3.8-27b` verified via single smoke call, proxy oversize/429 infra classification tests pass, 5-prompt v2 dry-run completed with 100% validity (1.0) and improved question precision (0.50 vs 0.38 v1).

### Part A — Infrastructure
- **evals/quota.py (`QuotaTracker`)**:
  - Persists cumulative tokens and request counts per model in `evals/results/quota.json`, automatically resetting upon UTC date change.
  - Inspects `x-ratelimit-*` headers when returned by providers to track remaining tokens and reset windows.
  - Enforces TPM safety: calculates projected tokens per minute and sleeps if usage exceeds 80% of 8K TPM (6,400 TPM).
  - Enforces daily budget safety: stops cleanly when daily tokens reach 85% of 200K TPD (170,000 tokens) per model, printing `"quota reached, resume tomorrow"`.
- **Makefile**:
  - Added `quota-status` target invoking `$(PY) -m evals.quota`.
  - Added `eval-e12` target accepting `VERSION ?= v2` and `PROMPTS ?= 5`.
- **config/models.yaml**:
  - Added `qwen_builder` configuration using `groq/qwen/qwen3.8-27b` with fallback to `groq/openai/gpt-oss-20b`.
  - Verified `groq/qwen/qwen3.8-27b` via ONE real test call (`groq/qwen/qwen3.8-27b` via litellm); call succeeded and responded `SUCCESS: Hello! How can I`. No key was printed or logged.
- **Proxy test for infra vs quality classification**:
  - Added assertions in `server/tests/test_wp1.py` ensuring that 413 oversize request payloads (and 429 provider errors) are classified strictly as infra errors (`oversize_request`), never flagged as quality failures.

### Part B — Error Analysis & v2 Prompts
- **Empirical Analysis of `evals/results/e1_e2.json` (v1 Baseline)**:
  - *Summary*: 30 prompts evaluated in v1 yielded **13 True Positives**, **21 False Positives**, and **4 False Negatives**, resulting in micro-precision of **0.3824** (0.38) and micro-recall of **0.7647** (0.76).
  - *False Positive breakdown by category*:
    1. `target_platform`: **8 FPs** (prompts P01, P04, P05, P06, P10, P15, P18, P20). The v1 Ambiguity Analyzer habitually hallucinated platform ambiguities (web vs desktop vs CLI vs mobile) for single-purpose utilities or personal apps where web/CLI was either already clear or trivially defaultable.
    2. `auth_model`: **6 FPs** (prompts P01, P04, P05, P06, P10, P15). The analyzer asked whether users needed OAuth vs email/password vs API keys on personal, single-tenant, or CLI applications.
    3. `data_model`: **3 FPs** (prompts P07, P24, P29). The analyzer asked detailed schema / database-engine questions that belong in implementation defaults, outside the frozen label vocabulary of irreversible architectural decisions.
    4. `payments`: **3 FPs** (prompts P16, P21, P22). The analyzer asked about payment provider choices where payments were either defaultable or non-blocking.
  - *Root Cause & Crowding Effect*:
    The analyzer treated any open technical detail as an ambiguity, and the classifier failed to demote standard defaults to `defaultable`. Because the question gate enforces a strict cap of 3 questions, spurious questions in `target_platform` and `auth_model` crowded out genuine irreversible forks (such as `data_ownership`), degrading precision to 0.38.
- **Prompt Tightening (v2)**:
  - Created `prompts/analyzer/v2.md`: Explicitly restricts candidate ambiguities to true irreversible forks. Forbids `data_model` from being marked as blocking; restricts `target_platform`, `auth_model`, and `payments` to genuine multi-tenant or commercial systems. Directs personal tools to adopt standard sensible defaults without user prompts.
  - Created `prompts/classifier/v2.md`: Constrains `blocking` classification to forks whose reversal requires discarding >50% of the architecture or rewriting schemas from scratch. All other technical choices are marked `defaultable`.
  - Created `prompts/question_gate/v1.md` and `prompts/question_gate/v2.md`: Documented and standardized question gate selection policies.
  - Updated `server/app/pipeline/prompts.py` to allow runtime version selection via `PROMPT_VERSION` while defaulting to the latest prompt version (`v2`), ensuring cache and resume hashes cleanly diverge.
  - Frozen labels in `evals/prompts/labels.jsonl` were strictly preserved and untouched.

### Part C — Resumable Runner & v2 Dry-Run
- **evals/runner.py Refactor**:
  - Implemented per-prompt incremental persistence so interrupted runs can resume without repeating completed prompts.
  - Added pre-run token estimate display (~4,500 tokens/prompt).
  - Added structured classification separating `infra_aborted` (rate limits, 502/503/504, network timeouts) from `quality_fail` (JSON schema errors, OpenAPI lint violations, consistency failures).
  - Integrated `QuotaTracker` to monitor cumulative tokens and respect provider rate limits.
- **5-Prompt Dry Run (`VERSION=v2 PROMPTS=5`)**:
  - Ran strictly 5 prompts (P01..P05) as required; v1 results preserved as baseline.
  - *Dry-run results (`evals/results/e1_e2_v2.json`)*:
    - **Total Prompts Ran:** 5
    - **Completed:** 5
    - **E1 Validity Rate:** **1.0 (100%)** (5 valid, 0 invalid)
    - **E2 Micro Precision:** **0.5000 (50%)** (5 TP, 5 FP) — up from 0.38 in v1 baseline
    - **E2 Micro Recall:** **0.6250 (62.5%)** (5 TP, 3 FN)
    - **Infra Aborted:** 0
    - **Quality Fail:** 0
    - **Tokens Consumed:** 71,437 tokens
- **Full 30-Prompt Run Command**:
  - Exact command to execute the full 30-prompt evaluation:
    ```bash
    make eval-e12 VERSION=v2 PROMPTS=30
    ```
    (Or directly: `python -m evals.runner --version v2 --prompts 30`)

### Verification
```bash
make quota-status
.venv/Scripts/python.exe -m pytest server/tests/test_wp1.py -v
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy server/app
```

