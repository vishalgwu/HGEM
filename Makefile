# GuardMem AI - developer entry points.  BUILD_NOTEBOOK.md S1.2
#
# `make <target>`. Run `make` on its own for the list.
#
# PORTABILITY: recipes here must run under both POSIX sh (Linux CI, macOS) and
# cmd.exe, because GNU Make on Windows falls back to cmd.exe when it cannot
# resolve a shell. That rules out `[ -f x ]`, `||`, subshells and single-quoted
# strings. `&&` is fine - both understand it. Anything that genuinely needs
# logic is handed to `python -c` with arguments passed via argv rather than
# interpolated into a quoted string, which behaves identically on both.

.DEFAULT_GOAL := help

# lint-imports renders its progress spinner through `rich`. On Windows, when
# stdout is not a console, rich falls back to a legacy writer that encodes via
# cp1252 and raises UnicodeEncodeError on the spinner emoji - so the gate exits
# 1 for a reason having nothing to do with imports, and only on Windows, so CI
# stays green while the local run fails. Forcing UTF-8 makes it behave
# identically on every platform. Make exports this to every recipe below.
export PYTHONIOENCODING := utf-8

UV        := uv run
CORE_SRC  := packages/guardmem-core/src
# Cleared by `make clean`. `.import_linter_cache` is on this list for a reason:
# it is keyed on file mtime, so restoring a file with an older mtime (a `git
# checkout`, a restored backup) leaves it serving a stale verdict - a false
# PASS as easily as a false FAIL.
CACHES    := .ruff_cache .mypy_cache .pytest_cache .import_linter_cache \
             .hypothesis htmlcov .coverage coverage.xml

.PHONY: help hooks fmt lint imports typecheck test test-all audit clean \
        dev down migrate seed eval

help:
	@echo GuardMem AI - make targets
	@echo   hooks - install the pre-commit hooks, run once after cloning
	@echo   fmt - apply ruff fixes and formatting
	@echo   lint - ruff check, ruff format --check, import-linter contracts
	@echo   imports - import-linter contracts only
	@echo   typecheck - mypy --strict on guardmem-core
	@echo   test - unit and property suites with coverage
	@echo   test-all - every suite with coverage
	@echo   audit - pip-audit over the installed dependency set
	@echo   clean - delete tool caches and coverage output
	@echo   dev - start the local datastore stack, from step S1.3
	@echo   down - stop the local datastore stack, keeps volumes, from step S1.3
	@echo   dev-reset - stop the stack AND delete its volumes, from step S1.3
	@echo   dev-ps - show datastore container health, from step S1.3
	@echo   dev-logs - tail the datastore logs, from step S1.3
	@echo   migrate - alembic upgrade head, from step S3.1
	@echo   seed - load the demo tenant, from step S3.6
	@echo   eval - run the eval suites, from step S22.1

# --- gates -----------------------------------------------------------------
# `make lint && make typecheck && make test` is the S1.2 acceptance check, and
# between them they must run every gate CI runs. That is why import-linter is
# part of `lint` rather than a target you have to remember.

hooks:
	$(UV) pre-commit install

fmt:
	$(UV) ruff check . --fix && $(UV) ruff format .

lint:
	$(UV) ruff check . && $(UV) ruff format --check . && $(UV) lint-imports

imports:
	$(UV) lint-imports

# `tests` is in scope from S1.7, and it has to be. That step's DONE WHEN is
# "fakes exist and satisfy the protocols under mypy --strict" - Protocol is
# structural, so nothing at runtime notices a signature mismatch, and a target
# that only saw CORE_SRC could not verify the claim at all. It paid for itself
# immediately: the first run found fourteen places where the test suite passed a
# raw `str` into a field declared `CandidateId` or `TenantId`, which is the
# exact mistake RULES.md 2.1 introduced those types to prevent.
typecheck:
	$(UV) mypy $(CORE_SRC) tests

# Bare `--cov`, not `--cov=guardmem_core`. The package to measure is already
# declared once as `source_pkgs` in pyproject.toml, and naming it again on the
# command line makes coverage resolve it after import, which reports
# "module-not-measured" and silently drops real code from the report. RULES.md 5
# leans on this number, so it has to be honest.
test:
	$(UV) pytest tests/unit tests/property --cov

test-all:
	$(UV) pytest --cov

audit:
	$(UV) pip-audit

clean:
	@$(UV) python -c "import pathlib,shutil,sys;[shutil.rmtree(p,ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True) for p in map(pathlib.Path,sys.argv[1:])]" $(CACHES)
	@echo removed tool caches

# --- infrastructure --------------------------------------------------------
# migrate/seed/eval reference files created by later steps. They are declared
# now because S1.2 specifies them and because the target list is the documented
# interface; each fails with its own tool naming the missing file, which is a
# clearer error than a Makefile guard would produce.

COMPOSE := infra/docker/docker-compose.dev.yml

# `--wait` blocks until every service reports healthy, rather than until the
# containers have merely been created. S1.3's next instruction is to run psql
# against Postgres, and without this that command races the database's first
# boot - which on a fresh volume includes initdb and the extension scripts.
# It also means a service that comes up unhealthy fails `make dev` instead of
# being discovered later by something confusing.
dev:                        ## S1.3
	docker compose -f $(COMPOSE) up -d --wait

# Keeps the named volumes. Use `make dev-reset` to discard the data too.
down:                       ## S1.3
	docker compose -f $(COMPOSE) down

# Deliberately separate from `down`, and deliberately not the default: `down -v`
# destroys the Postgres volume, and with it every assertion and audit row in the
# local stack. Having it as its own named target means nobody reaches for the
# flag on a whim.
dev-reset:                  ## S1.3
	docker compose -f $(COMPOSE) down --volumes

# The stack is five ports and four services; when something will not connect,
# this is the first thing to look at.
dev-ps:                     ## S1.3
	docker compose -f $(COMPOSE) ps

dev-logs:                   ## S1.3
	docker compose -f $(COMPOSE) logs --tail=100

migrate:                    ## S3.1
	$(UV) alembic upgrade head

seed:                       ## S3.6
	$(UV) python scripts/seed_demo_tenant.py

eval:                       ## S22.1
	$(UV) python evals/runners/run_suite.py --all
