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
