# requirements.md: Query Optimizer Service

Technical requirements to build and run the project. Product requirements live in `PRD.md`.

---

## 1. Runtime and tooling

| Item | Requirement |
|---|---|
| Python | 3.12 |
| Package manager | `uv` (or pip + venv) |
| Database | Postgres 16 |
| Containers | Docker 24+ and Docker Compose v2 |
| OS | Linux, macOS, or Windows with WSL2 |
| Node (only if you build a UI) | 20 LTS |
| Make | GNU Make |

## 2. Python dependencies

| Purpose | Package |
|---|---|
| Web framework | `fastapi`, `uvicorn[standard]` |
| Validation | `pydantic>=2`, `pydantic-settings` |
| DB driver | `psycopg[binary,pool]>=3` (async) |
| Migrations | `alembic` (or plain SQL files plus a runner) |
| LLM gateway | `litellm` |
| MCP server | `mcp` (official Python SDK) |
| SSE | `sse-starlette` |
| OpenAPI validation | `openapi-spec-validator`, `jsonschema` |
| Diffing | `deepdiff` (plus custom breaking-change rules) |
| HTTP client (URL intake) | `httpx` |
| PDF text extraction | `pypdf` |
| HTML readability | `trafilatura` or `readability-lxml` |
| Logging | `structlog` |
| Metrics | `prometheus-client` |
| Retries/backoff helpers | `tenacity` (optional) |
| Config | `pyyaml` |

Dev dependencies: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `testcontainers` (optional, for Postgres in tests), `httpx` (test client).

## 3. External services

| Service | Needed for | Notes |
|---|---|---|
| LLM provider key(s) | Real runs and evals | Two providers via OpenRouter or direct; keys in env only |
| Prometheus + Grafana | Dashboard | Run via Compose |
| VPS (any small host) | Public demo | Plus a domain and Caddy for TLS |
| GitHub Actions | CI | Free tier is enough; CI makes no LLM calls |

## 4. Environment variables (`.env.example`)

```
DATABASE_URL=postgresql://app:app@postgres:5432/qo
API_BEARER_TOKEN=change-me
OPENROUTER_API_KEY=
PROVIDER_A_API_KEY=
PROVIDER_B_API_KEY=
MODELS_CONFIG=config/models.yaml
ACTIVE_CONFIG=single_model
SPEND_CAP_USD_PER_DAY=10
MAX_COST_USD_PER_SESSION=0.50
MAX_QUESTIONS_PER_ROUND=3
LEASE_SECONDS=60
LEASE_RENEW_SECONDS=20
LOG_LEVEL=INFO
```

Never commit a real `.env`.

## 5. Compose services

`api`, `worker` (2 replicas), `mcp`, `postgres`, `prometheus`, `grafana`. Each has a healthcheck and `restart: unless-stopped`. Migrations run on API startup.

## 6. Makefile targets

| Target | Action |
|---|---|
| `make up` | Start the full stack |
| `make down` | Stop and remove containers |
| `make migrate` | Apply migrations (idempotent) |
| `make test` | Run pytest with coverage |
| `make lint` | ruff + mypy |
| `make eval CONFIG=<name> REPS=3 MAX_USD=<n>` | Run evaluation with budget guard |
| `make demo` | Run a sample prompt end to end |

## 7. Build constraints (from the PRD)

- No provider SDK imports outside `server/app/gateway/`.
- Every LLM stage returns strict JSON validated by Pydantic; one repair retry only.
- All state transitions go through `transition(current, target)`.
- Prompts live in `prompts/<stage>/vN.md`; the prompt version is part of the input hash.
- Metric labels never include `session_id`.
- CI must pass with zero real LLM calls (`FakeGateway`).
- Coverage >= 80% on `pipeline/`, `schemas/`, `storage/`.

## 8. Hardware and cost estimate

| Item | Estimate |
|---|---|
| Dev machine | 8 GB RAM minimum (Postgres + Docker + IDE); 16 GB comfortable |
| LLM spend during development | roughly USD 15-40 using cheap models for iteration |
| Full evaluation (3 configs x 3 reps x 15-30 prompts) | roughly USD 20-60 depending on models; enforce with `--max-usd` |
| Demo hosting | roughly USD 5-10 per month for a small VPS |

These are rough planning figures, not measured; replace with real numbers from your own runs.

## 9. Skills you will need

Python async, SQL and transactions, REST design, Pydantic, Docker/Compose, prompt design for structured output, basic Prometheus/Grafana, pytest, Git, and enough OpenAPI knowledge to read and lint a spec. MCP is new to most people; budget a few hours to learn it.

## 10. Suggested build order

1. Repo, Compose, Postgres, gateway + FakeGateway
2. Schemas and validators (deterministic code first)
3. Pipeline stages with canned outputs, then real models
4. Queue, state machine, idempotency, resume, crash test
5. Versioning, revision, diff
6. SSE and MCP
7. Observability and CI
8. Evaluation E1-E3
9. Deploy, README, video

---

# Additions for PRD v2 (Token-Optimizing Layer)

## A. Extra Python dependencies

| Purpose | Package |
|---|---|
| Streaming proxy / SSE passthrough | `httpx` (streaming), `sse-starlette` |
| Token estimation (budgeting only, not truth) | `tiktoken` (OpenAI-style), provider token-count endpoints where available |
| Code parsing for repo map | `tree-sitter`, `tree-sitter-languages` (or per-language grammars) |
| Keyword retrieval | Postgres full-text search (`tsvector`) or `rank-bm25` |
| Embeddings (optional, P3) | provider embedding API via LiteLLM, or `sentence-transformers` (CPU) with `pgvector` |
| Prose compression (optional, P10) | `llmlingua` (heavy; aggressive mode only) |
| Diff/patch handling (P6) | `unidiff`, plus custom search-replace parser |
| Cache key hashing | stdlib `hashlib`, canonical JSON via `orjson` |
| Redaction | `detect-secrets` or regex rules |

Postgres extension (optional): `pgvector`.

## B. Extra environment variables

```
OPTIMIZER_DEFAULT_MODE=balanced        # off | safe | balanced | aggressive
OPTIMIZER_FAIL_OPEN=true
OPTIMIZER_CACHE_TTL_SECONDS=86400
OPTIMIZER_SEMANTIC_CACHE=false
OPTIMIZER_SHADOW_SAMPLE_RATE=0.0       # evaluation/staging only
OPTIMIZER_PASS_BREAKER_THRESHOLD=0.25
OPTIMIZER_PASS_BREAKER_WINDOW=50
SESSION_MAX_TOKENS=2000000
SESSION_MAX_USD=2.00
PROXY_UPSTREAM_TIMEOUT_S=120
LOG_RAW_PROMPTS=false
```

## C. Extra Compose services and Makefile targets

Services: `proxy` (may share the `api` image), optional `pgvector`-enabled Postgres image.

| Target | Action |
|---|---|
| `make proxy` | Start proxy locally on a fixed port |
| `make eval-tokens MODE=<mode> REPS=3 MAX_USD=<n>` | Run E4 token benchmark |
| `make ablate REPS=3 MAX_USD=<n>` | Run E5 pass ablation |
| `make index REPO=<path>` | Build the repo index for P3 |

## D. Extra build constraints

- Every pass: pure function, own unit tests, own kill switch, documented mode gating.
- Provider-reported usage is the only source of truth for reported savings; estimates are labelled `_est`.
- Never compress or alter protected content (latest user instruction, active error text, files under edit).
- Any optimizer exception must result in forwarding the original request (fail-open), covered by an injected-failure test.
- Cache keys include model, prompt version, spec version, and file hashes; invalidation tests are mandatory.
- Raw prompts are not logged or stored by default; ledger stores hashes and token counts.
- Lossy passes ship enabled only if E5 shows benefit without a pass-rate regression beyond tolerance.

## E. Extra cost estimate

| Item | Estimate |
|---|---|
| Added dev LLM spend (proxy testing, passes) | roughly USD 20-50 |
| E4-E6 (4 modes x 15 tasks x 3 reps, plus ablations of ~8 passes) | roughly USD 80-250 depending on model choice; enforce with `--max-usd`, start with cheap models and 1 rep to debug |
| Shadow sampling | extra spend by design; use only in small evaluation windows |

Rough planning figures, not measured. Replace them with real numbers from your runs.

## F. Extra skills

HTTP proxying and streaming, provider API differences (OpenAI vs Anthropic message formats and caching), tree-sitter basics, information retrieval basics (BM25), prompt caching mechanics, and experiment design (ablations, variance).
