.PHONY: help setup check test lint fmt ui build run ingest paths bench-api clean

# DB is optional: empty means the platform default store (`make paths` prints it).
DB ?=
DB_ARG = $(if $(DB),--db $(DB))

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

setup:  ## Install frontend (npm ci) and Python (uv sync) dependencies; builds the UI first
	cd frontend && npm ci && npm run build
	# uv sync installs the package, and the package build includes frontend/dist, so the UI is built first.
	uv sync

check: lint test  ## Gate for agents: ruff + pytest + frontend vitest, build and oxlint
	cd frontend && npm test && npm run build && npm run lint

test:  ## Python unit tests
	uv run python -m pytest tests/unit

lint:  ## Ruff lint and format check
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:  ## Apply ruff format and safe fixes
	uv run ruff check --fix src tests
	uv run ruff format src tests

ui:  ## Build the frontend into frontend/dist
	cd frontend && npm run build

build: ui  ## Build the wheel and sdist into dist/ (the wheel bundles the UI)
	uv build

run:  ## Serve the UI and API (DB=path, default the platform data folder)
	uv run botsworth $(DB_ARG) serve

ingest:  ## Read provider logs into the store (DB=path, ARGS="--provider codex")
	uv run botsworth $(DB_ARG) ingest $(ARGS)

paths:  ## Print the data folder and store file
	uv run botsworth paths

bench-api:  ## API-50 / CLI-24 latency, read-only (DB=path, default the platform data folder)
	uv run python tests/bench/bench_api.py $(if $(DB),--db $(DB))

clean:  ## Remove build outputs (dist/, frontend/dist, caches); never a store
	rm -rf dist frontend/dist .pytest_cache .ruff_cache
