# GuardMem AI — top-level developer entrypoints
# All targets are thin wrappers; the real work lives in scripts/, docker-compose
# files under infra/docker, and per-package pyproject.toml / package.json.

SHELL := /bin/bash
COMPOSE_DEV  := infra/docker/docker-compose.dev.yml
COMPOSE_TEST := infra/docker/docker-compose.test.yml
COMPOSE_OBS  := infra/docker/docker-compose.observability.yml

.PHONY: help
help:
	@echo "GuardMem AI — common targets"
	@echo "  make dev          boot postgres+pgvector, neo4j, redis, langfuse, phoenix"
	@echo "  make dev-down     stop the dev stack"
	@echo "  make observability boot otel-collector, prometheus, grafana, tempo"
	@echo "  make migrate      run alembic + neo4j cypher migrations"
	@echo "  make seed         seed a demo tenant with the clinical ontology"
	@echo "  make test         unit + integration"
	@echo "  make unit         unit tests only"
	@echo "  make integration  integration tests (needs make dev)"
	@echo "  make eval         run eval suites against the current build"
	@echo "  make bench        locust load profiles"
	@echo "  make lint         ruff + mypy + eslint"
	@echo "  make format       ruff-format + prettier"
	@echo "  make typecheck    mypy --strict on guardmem-core; tsc on TS packages"

.PHONY: dev dev-down observability
dev:
	docker compose -f $(COMPOSE_DEV) up -d
dev-down:
	docker compose -f $(COMPOSE_DEV) down
observability:
	docker compose -f $(COMPOSE_OBS) up -d

.PHONY: migrate seed
migrate:
	uv run alembic -c infra/migrations/alembic/alembic.ini upgrade head
	uv run python scripts/apply_cypher.py infra/migrations/cypher
seed:
	uv run python scripts/seed_demo_tenant.py

.PHONY: test unit integration
test: unit integration
unit:
	uv run pytest tests/unit -q
integration:
	uv run pytest tests/integration -q

.PHONY: eval bench
eval:
	uv run python evals/runners/run_suite.py --all
bench:
	uv run locust -f bench/locust/write_path.py --headless -u 50 -r 5 -t 2m

.PHONY: lint format typecheck
lint:
	uv run ruff check .
	uv run mypy --strict packages/guardmem-core/src
	pnpm -r lint
format:
	uv run ruff format .
	pnpm -r format
typecheck:
	uv run mypy --strict packages/guardmem-core/src
	pnpm -r typecheck
