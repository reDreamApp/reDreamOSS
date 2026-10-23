.DEFAULT_GOAL := help
SHELL := /bin/bash

# ---- meta ---------------------------------------------------------------

.PHONY: help
help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# The settings file (.env at the repo root, from `make init-env`), read by the
# local server targets when it exists; run from apps/server, hence ../../.
ENV_FILE := $(if $(wildcard .env),--env-file ../../.env,)

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
api-dev: ## run the server (backend + in-process AI) locally on :8001 (settings: .env)
	cd apps/server && uv run $(ENV_FILE) uvicorn redream_api.main:app --reload --port 8001

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
test: ai-test api-test deploy-test app-test ## run every test suite

lint: ai-lint api-lint deploy-lint app-analyze ## lint everything

fmt: ai-fmt api-fmt deploy-fmt app-fmt ## format everything

# ---- db (plain Postgres 16+, apps/server/migrations) --------------------

.PHONY: db-migrate db-test
db-migrate: ## apply pending migrations to DATABASE_URL (env or .env)
	cd apps/server && uv run $(ENV_FILE) python -m redream_api.db.migrate

db-test: ## runner, schema drift + smoke tests on Postgres (TEST_DATABASE_URL: an empty database)
	cd apps/server && REQUIRE_PG=1 uv run pytest tests/pg

# ---- deploy (compose.yaml: Postgres + server, optional Caddy) -----------

.PHONY: deploy-test deploy-lint deploy-fmt init-env up update down logs create-user
deploy-test: ## check compose.yaml, .env.example and init-env.sh (no Docker needed)
	cd apps/server && uv run pytest tests/deploy

deploy-lint: ## lint the deployment checks (tests/deploy)
	cd apps/server && uv run ruff check tests/deploy

deploy-fmt: ## format the deployment checks (tests/deploy)
	cd apps/server && uv run ruff format tests/deploy && uv run ruff check --fix tests/deploy

init-env: ## create .env from .env.example and fill in its secrets
	sh scripts/init-env.sh

# --wait: a server that does not get healthy (a failed migration, a bad
# setting) fails the command; see docker compose logs server.
up: ## build and start the stack (docker compose up -d --build --wait)
	docker compose up -d --build --wait --wait-timeout 180

# Compose's default pull policy never refreshes a cached postgres:17 or
# caddy:2, and --build alone reuses the cached Python base image: pull both.
update: ## after git pull: pull newer postgres/caddy, rebuild on a fresh base image, restart
	docker compose pull --ignore-buildable
	docker compose build --pull
	docker compose up -d --wait --wait-timeout 180

# With the caddy profile too: plain `down` would leave a Caddy started with
# --profile caddy running (harmless when there is none).
down: ## stop the stack (volumes are kept)
	docker compose --profile caddy down

logs: ## tail the stack's logs
	docker compose logs -f --tail=200

create-user: ## create an account in the running stack: make create-user EMAIL=you@example.org
	@test -n "$(EMAIL)" || { echo "usage: make create-user EMAIL=you@example.org" >&2; exit 1; }
	docker compose exec server python -m redream_api.cli create-user "$(EMAIL)"
