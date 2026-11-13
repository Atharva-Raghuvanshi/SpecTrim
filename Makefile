ifeq ($(OS),Windows_NT)
  PY ?= .venv/Scripts/python
else
  PY ?= .venv/bin/python
endif

.PHONY: venv up down logs ps test lint migrate coverage smoke-real test-crash quota-status eval-e12 demo-proxy demo-mcp clean

venv:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

ps:
	docker compose ps

test:
	@docker compose stop worker >/dev/null 2>&1 || true
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m mypy server/app

migrate:
	$(PY) -m app.storage.migrate

coverage:
	$(PY) -m pytest -q --cov=app.pipeline --cov=app.schemas --cov=app.storage --cov=app.diffing --cov=app.validators --cov-report=term --cov-fail-under=80

test-crash:
	@docker compose stop worker > /dev/null 2>&1 || true
	$(PY) -m pytest -q server/tests/test_crash_resume.py -v

quota-status:
	$(PY) -m evals.quota

VERSION ?= v2
PROMPTS ?= 5

eval-e12:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) -m evals.runner --version $(VERSION) --prompts $(PROMPTS)

smoke-real:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	$(PY) -m app.gateway.smoke

demo-proxy:
	$(PY) -m evals.agents.tool_agent

demo-mcp:
	$(PY) -m evals.agents.mcp_client

clean:
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
