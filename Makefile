# Recipes must run in both sh and cmd.exe (GNU make on Windows uses whichever it finds):
# only uv, npm, npx and `uv run --no-project python -c "..."` with single-quoted Python strings.
# No rm, cp, mkdir -p, cd x &&, export, $(shell ...), backslash continuations or %.
.DEFAULT_GOAL := help
.PHONY: help install dev dev-backend dev-frontend test lint format check types clean reset

# Run a second instance on other ports and its own state:
# make dev BACKEND_PORT=8001 FRONTEND_PORT=5174 STATE_DIR=.state-dev2
# (vite.config.ts reads both ports from the environment).
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 5173
export BACKEND_PORT FRONTEND_PORT

help:
	@echo Targets:
	@echo   install       install backend and frontend dependencies
	@echo   dev           run backend and frontend together
	@echo   dev-backend   run the API only
	@echo   dev-frontend  run the Vite dev server only
	@echo   test          run backend and frontend tests
	@echo   lint          ruff, type check and oxlint
	@echo   format        format and autofix backend code
	@echo   check         lint and test
	@echo   e2e           Playwright smoke test against the mock, run e2e-install once first
	@echo   e2e-install   download the Chromium build Playwright uses
	@echo   types         generate TS types from the OpenAPI schema
	@echo   smoke-llm     tool-calling round trip against the real LLM, needs LLM_API_KEY
	@echo   eval-llm      tests marked llm, against the real LLM
	@echo   clean         remove caches and build output
	@echo   reset         delete the runtime state (STATE_DIR), stop the app first

install:
	uv --directory backend sync
	npm --prefix frontend install

dev:
	npx --yes concurrently -k -n api,web "uv --directory backend run uvicorn app.main:app --reload --port $(BACKEND_PORT)" "npm --prefix frontend run dev"

dev-backend:
	uv --directory backend run uvicorn app.main:app --reload --port $(BACKEND_PORT)

dev-frontend:
	npm --prefix frontend run dev

test:
	uv --directory backend run pytest
	npm --prefix frontend test

lint:
	uv --directory backend run ruff check .
	uv --directory backend run ruff format --check .
	npm --prefix frontend run typecheck
	npm --prefix frontend run lint

format:
	uv --directory backend run ruff format .
	uv --directory backend run ruff check --fix .

check: lint test

# End-to-end smoke test (D39): starts its own backend (mock) and frontend on ports 8100 / 5180.
# Not part of check: it needs a browser download and two servers.
.PHONY: e2e e2e-install

e2e:
	npm --prefix frontend run e2e

e2e-install:
	npm --prefix frontend exec -- playwright install chromium

types:
	uv --directory backend run python -c "import json; from app.main import app; open('../frontend/openapi.json', 'w').write(json.dumps(app.openapi()))"
	npx --yes openapi-typescript frontend/openapi.json -o frontend/src/lib/api-types.ts

clean:
	uv run --no-project python -c "import glob, shutil; [shutil.rmtree(p, ignore_errors=True) for p in glob.glob('backend/**/__pycache__', recursive=True) + ['backend/.pytest_cache', 'backend/.ruff_cache', 'frontend/dist']]"

reset:
	uv run --no-project python -c "import os, shutil; shutil.rmtree(os.environ.get('STATE_DIR', '.state'), ignore_errors=True)"

.PHONY: smoke-llm eval-llm

smoke-llm:
	uv --directory backend run python -m scripts.smoke_llm

eval-llm:
	uv --directory backend run pytest -m llm -s
