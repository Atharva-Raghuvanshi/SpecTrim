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
