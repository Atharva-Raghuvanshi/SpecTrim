ifeq ($(OS),Windows_NT)
  PY := .venv/Scripts/python
else
  PY := .venv/bin/python
endif

.PHONY: venv up down logs ps test lint clean

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
	$(PY) -m pytest

lint:
	$(PY) -m ruff check .
	$(PY) -m mypy server/app

clean:
	rm -rf .venv .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
