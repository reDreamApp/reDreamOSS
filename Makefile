.DEFAULT_GOAL := help
SHELL := /bin/bash

# ---- meta ---------------------------------------------------------------

.PHONY: help
help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---- server: ai side (apps/server, redream_ai) --------------------------

.PHONY: ai-test ai-lint ai-fmt
ai-test: ## run AI-side tests (redream_ai)
	cd apps/server && uv run pytest tests/ai

ai-lint: ## lint AI side (redream_ai)
	cd apps/server && uv run ruff check src/redream_ai tests/ai evals && uv run mypy src/redream_ai

ai-fmt: ## format AI side (redream_ai)
	cd apps/server && uv run ruff format src/redream_ai tests/ai evals && uv run ruff check --fix src/redream_ai tests/ai evals

# ---- server: api side (apps/server, redream_api) ------------------------

.PHONY: api-dev api-test api-lint api-fmt
api-dev: ## run the server (backend + in-process AI) locally on :8001
	cd apps/server && uv run $(if $(wildcard apps/server/.env),--env-file .env,) uvicorn redream_api.main:app --reload --port 8001

api-test: ## run backend tests
	cd apps/server && uv run pytest tests/api

api-lint: ## lint backend
	cd apps/server && uv run ruff check src/redream_api tests/api && uv run mypy src/redream_api

api-fmt: ## format backend
	cd apps/server && uv run ruff format src/redream_api tests/api && uv run ruff check --fix src/redream_api tests/api

# ---- flutter app --------------------------------------------------------

.PHONY: app-run app-test app-analyze app-fmt
app-run: ## flutter run via FVM
	cd apps/app && fvm flutter run

app-test: ## flutter test
	cd apps/app && fvm flutter test

app-analyze: ## flutter analyze
	cd apps/app && fvm flutter analyze

app-fmt: ## dart format + dart fix
	cd apps/app && fvm dart fix --apply && fvm dart format .

# ---- combined -----------------------------------------------------------

.PHONY: test lint fmt
test: ai-test api-test app-test ## run every test suite

lint: ai-lint api-lint app-analyze ## lint everything

fmt: ai-fmt api-fmt app-fmt ## format everything

# ---- db (plain Postgres 16+, apps/server/migrations) --------------------

.PHONY: db-migrate db-test
db-migrate: ## apply pending migrations to DATABASE_URL (env or apps/server/.env)
	cd apps/server && uv run $(if $(wildcard apps/server/.env),--env-file .env,) python -m redream_api.db.migrate

db-test: ## runner, schema drift + smoke tests on Postgres (TEST_DATABASE_URL: an empty database)
	cd apps/server && REQUIRE_PG=1 uv run pytest tests/pg

# ---- infra (production-equivalent compose) ------------------------------

.PHONY: up down logs
up: ## docker compose up the full stack
	cd infra && docker compose up -d --build

down: ## docker compose down
	cd infra && docker compose down

logs: ## tail docker compose logs
	cd infra && docker compose logs -f --tail=200
