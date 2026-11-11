# Milestones

Source: docs/PRD.md §10 (phases 1-9) and docs/PRD_v2.md §11 (phases 10-15).
Hours are PRD estimates. Each milestone fits one focused session.
Protocol: finish a milestone (exit check + tests green + lint clean + docs/log.md + commit as `M<id>: <summary>`), then STOP and wait for "next".

## Release A: spec agent core (~73 h)

### Phase 1: Foundation (6 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M1.1 | Repo scaffold, Compose with Postgres + API stub, Makefile/scripts, CI stub, docs/log.md | `make up` starts cleanly; /healthz returns OK |
| M1.2 | Gateway v0: LiteLLM wrapper, models.yaml, Groq config, usage/latency/cost capture, 429 backoff, FakeGateway | Fake-based tests pass; one real call works |

### Phase 2: Schemas and validators (8 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M2.1 | Message envelope and package schemas (Pydantic) | Bad id, unknown type, empty fields rejected |
| M2.2 | Repair-retry helper | Second failure becomes a permanent error (test) |
| M2.3 | OpenAPI lint + deterministic consistency checker | Known-good passes; seeded-bad fails with the right reason |

### Phase 3: Core pipeline (14 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M3.1 | Prompts v1 + Ambiguity Analyzer | Canned-output tests pass |
| M3.2 | Irreversibility classifier, rule table, question gate (cap 3) | Labelled examples classify correctly |
| M3.3 | Master spec writer + decomposer | Valid sub-specs reference the contract |
| M3.4 | Contract generator (OpenAPI 3.1, contract.vN) | Output passes lint and consistency checks |
| M3.5 | In-memory CLI: idea to package | Real-model smoke on 5 prompts produces valid packages |

### Phase 4: Durability (10 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M4.1 | Migrations and repositories | make migrate is idempotent; DB tests pass |
| M4.2 | Job queue + worker (SKIP LOCKED, leases, retries, dead jobs) | 4 workers x 50 jobs run once each; killed worker's job reclaimed |
| M4.3 | State machine and persisted events with seq | All allowed/forbidden transition tests pass |
| M4.4 | Stage persistence, input_hash, resume | Resume skips done stages; changed hash re-runs |
| M4.5 | Idempotent REST API: /sessions, answers, cancel | Concurrent duplicate POSTs give one session; crash test passes |

### Phase 5: Versioning and revision (7 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M5.1 | Immutable versions (ON CONFLICT DO NOTHING) | Replayed job creates no duplicate version |
| M5.2 | Revision stage (change_request / failure_report) | Change request yields v2 |
| M5.3 | Diff engine + breaking-change flags | Removed endpoint is flagged as breaking |

### Phase 6: Interfaces (6 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M6.1 | SSE with Last-Event-ID replay | Reconnect: no gaps, no duplicates |
| M6.2 | MCP server (five spec tools) | A real coding agent calls create_spec |
| M6.3 | Export: JSON, Markdown, openapi.yaml | Files validate |

### Phase 7: Observability and CI (6 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M7.1 | Structured logs with trace_id | One session filterable end to end |
| M7.2 | Prometheus metrics | Fake call increments tokens/cost/latency correctly |
| M7.3 | Grafana dashboard, CI gates, coverage >= 80% | CI green; panels populate |

### Phase 8: Spec evaluation (10 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M8.1 | 30 prompts with blocking-ambiguity labels (user approves, then freeze) | Frozen |
| M8.2 | Runner with --max-usd guard | Aborts at budget; JSON saved |
| M8.3 | E1 (validity), E2 (question precision/recall), E3 (downstream lift) report | Real numbers in evals/REPORT.md |

### Phase 9: Deploy (6 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M9.1 | Full Compose, healthchecks, .env.example | Fresh clone, make up works |
| M9.2 | Caddy TLS, bearer token, spend cap, rate limit | Cap blocks new sessions |
| M9.3 | Public demo + short video | Link works end to end |

**Tag release-a.** This is already a strong resume project on its own.

## Release B: first working layer (~16 h)

| ID | Deliverable | Exit check |
|---|---|---|
| M10.1 | OpenAI-compatible proxy endpoint, streaming passthrough | An agent works by changing only the base URL |
| M10.2 | Optimization chain interface, policy engine, modes, fail-open, /v1/optimize dry run | Injected pass failure still forwards the original request |
| M10.3 | Savings ledger + /stats | Baseline-est vs actual recorded per request |
| M11.1 | P9 exact-match response cache with invalidation | Invalidation tests: zero stale serves |
| M11.2 | P4 tool-output compaction | Error lines always preserved (test) |
| M11.3 | P7 output controls | max_tokens and stop sequences applied per stage |
| M11.4 | P1 cache-aware assembly | Prefix is byte-stable across calls (test) |

**Tag release-b** after M11.4: first measured before/after number.

## Release C: selective passes and proof (~26 h, +8-10 h optional)

| ID | Deliverable | Exit check |
|---|---|---|
| M12.1 | P3 repo map (tree-sitter) + BM25 retrieval | Relevant file found on a known task |
| M12.2 | P5 history compaction | Decisions and open errors survive compaction |
| M12.3 | P6 edit-format enforcement | Whole-file rewrite above threshold is rejected and retried |
| M12.4 | P2 spec-as-context integration | Only the relevant sub-spec is sent per role |
| M13.1 | P8 routing and cascade | Validator failure escalates to the stronger model |
| M13.2 | P11 budget governor + per-pass circuit breakers | Breaker opens past the failure threshold |
| M14.1 | E4 token benchmark across modes | Real tokens, cost, pass rate saved as JSON |
| M14.2 | E5 ablation + E6 Pareto chart | Each pass has a measured marginal effect |
| M15.1 | README, honest results, demo | A new reader runs it in 10 minutes |
| M16 (optional) | P12 handoff compaction + artifact store + multi-agent benchmark | Benchmark shows tokens, fix loops, pass rate with and without the layer |
