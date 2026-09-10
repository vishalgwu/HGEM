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
	@echo   down - stop the local datastore stack, from step S1.3
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

typecheck:
	$(UV) mypy $(CORE_SRC)

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
# These reference files created by later steps. They are declared now because
# S1.2 specifies them and because the target list is the documented interface;
# each one fails with its own tool naming the missing file, which is a clearer
# error than a Makefile guard would produce.

COMPOSE := infra/docker/docker-compose.dev.yml

dev:                        ## S1.3
	docker compose -f $(COMPOSE) up -d

down:                       ## S1.3
	docker compose -f $(COMPOSE) down

migrate:                    ## S3.1
	$(UV) alembic upgrade head

seed:                       ## S3.6
	$(UV) python scripts/seed_demo_tenant.py

eval:                       ## S22.1
	$(UV) python evals/runners/run_suite.py --all
