# Agent rules: Query Optimizer
Project: spec agent + token-optimizing proxy (FastAPI, Postgres, LiteLLM, Groq free tier).
Spec of record: docs/PRD.md and docs/PRD_v2.md. Progress log: docs/log.md. Milestones: docs/MILESTONES.md.

## Environment
- Windows 11, Git Bash. Use .venv/Scripts/python.exe. The project path contains a space; quote it.
- API on host port 8001, Postgres on 5433. Never touch agent-project-* containers.
- Commands: make up, make test, make lint, make coverage.

## Rules
- All LLM calls go through the gateway (LiteLLM). No provider SDK imports outside gateway/proxy_gateway (an isolation test enforces this).
- Tests use FakeGateway only. Real calls only in smoke and eval commands.
- Groq free tier per model (openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b): 30 RPM, 1K RPD, 8K TPM, 200K TPD. Use Groq only for project eval runs, never for the coding agent.
- Eval runners must be resumable per (task, rep, arm), quota-aware (evals/quota.py, stop at 85 percent of daily tokens), keep every request under 8K tokens, and tag failures infra_aborted or quality_fail. Print a token estimate before any run.
- One builder model per experiment: baseline and treatment arms use the same model. gpt-oss-120b is reserved for the spec pipeline and the P8 cascade.
- Integrity: report only measured numbers; label anything else NOT MEASURED; never edit evals/prompts/labels.jsonl; report n and variance; ship passes with no measured benefit disabled by default.
- Never read, print or commit .env or API keys.

## Protocol per work package
Implement, tests green, ruff and mypy clean, update docs/log.md (what was done, decisions, verify commands, open issues), commit as WP<n>: <summary>, then STOP. Do not start another work package. Never claim something passes without running it and pasting the key output into the log.
