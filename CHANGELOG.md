# Changelog

All notable changes to this project are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This
project will adopt [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once there is a released package. Per `docs/RULES.md` §3, **a prompt change is a
semver-minor change** and triggers the nightly eval gate.

No version has been released. Nothing here is deployed.

For day-to-day build narrative — what broke, what was learned, tomorrow's first
step — see [`DAILY_LOG.md`](DAILY_LOG.md). This file records what changed in the
repository; the log records what happened while changing it.

## [Unreleased]

### Added

- **S1.3 — local datastore stack.** `infra/docker/docker-compose.dev.yml` with
  Postgres+pgvector, Redis, Neo4j and Arize Phoenix; every image pinned to an
  exact version, every service healthchecked, every published port overridable
  from the shell so a conflict needs no edit to the file.
  `infra/docker/initdb/01-extensions.sql` creates `vector`, `pgcrypto` and
  `pg_trgm` on first boot, replacing the manual `psql` steps. `make dev` waits
  for health; `make dev-reset`, `make dev-ps` and `make dev-logs` added.
  **Langfuse is deliberately absent** - `latest` is v4, which requires
  ClickHouse, MinIO, an authenticated Redis and a worker container, and the
  notebook's four-line config is v2-shaped. It arrives at S13.1 with the stack
  it needs.
- **`scripts/normalise_secrets_baseline.py`** — runs after `detect-secrets` in
  pre-commit and rewrites baseline result paths to POSIX separators. The earlier
  one-off fix was not durable: the hook rewrites those paths with the local
  separator every time it updates the baseline, which happens whenever a line
  number shifts in a baselined file. Idempotent, exits 1 only when it changed
  something, and rewrites the path strings alone so the `exclude` regexes are
  untouched.
- **`tests/unit/test_compose_stack.py`** — images must be pinned to at least
  `MAJOR.MINOR` (a bare `pg16` or `7-alpine` moves just like `latest`), every
  service must declare a healthcheck (`up --wait` treats a missing one as
  satisfied, so it passes the gate unchecked), and no two services may publish
  the same host port.
- **S1.2 — gates.** `.pre-commit-config.yaml` (ruff-check, ruff-format,
  gitleaks, detect-secrets, and mypy + import-linter as `local` hooks running the
  project toolchain); a `Makefile` whose `help` is the default goal and whose
  `lint` target includes the import-linter contracts; and
  `.github/workflows/ci.yml` with a `gates` job running `make lint/typecheck/test`
  and a `hooks` job running `pre-commit run --all-files`, which is where
  `docs/RULES.md` §4's requirement for gitleaks and detect-secrets *in CI* is met.
  Both jobs install with `uv sync --locked --dev`, so a dependency edit that was
  never re-locked cannot merge.
- **`tests/unit/test_dependency_consistency.py`** — four tests making the
  two-lockfile invariant enforceable rather than aspirational: no package may
  disagree between `uv.lock` and `requirements.lock.txt`; `uv.lock` must remain a
  subset of it; every dev-group entry must be an exact `==` pin; and the dev group
  must match `requirements/dev.txt`.
- **S1.1 — uv workspace.** Root `pyproject.toml` configuring the workspace,
  ruff, mypy, pytest, coverage and import-linter; `packages/guardmem-core` with
  its own `pyproject.toml` and `src/guardmem_core/__init__.py`; the six `tests/`
  directories; `.python-version` pinned to 3.12; `uv.lock` (49 packages). The
  first import-linter contract is live and verified to fail on a violation:
  `guardmem_core` may not import fastapi, starlette, uvicorn, mcp or arq.
- Python 3.12 dependency set, collected from the design suite and pinned:
  layered `requirements/` files (69 direct packages, each citing the build step
  or spec clause that requires it) plus `requirements.lock.txt`, the fully
  resolved 311-package transitive lock.
- `.env.example` — environment template covering the S1.4 settings fields, with
  every later-step variable present but commented out and labelled with the step
  that activates it.
- `.secrets.baseline` — records the four known dev placeholders so
  `detect-secrets` passes, as `docs/RULES.md` §4 requires in pre-commit and CI.
- `.gitignore`, restored per `BUILD_NOTEBOOK.md` S0.3.
- `DAILY_LOG.md`, created per S0.4.
- Root `README.md`, `LICENSE` (Apache-2.0), `SECURITY.md`, `CONTRIBUTING.md`,
  and this file.

### Fixed

- **CI actions no longer run on deprecated Node 20.** `actions/checkout@v4`,
  `actions/cache@v4` and `astral-sh/setup-uv@v5` were being forced onto Node 24
  on every run; bumped to `@v5`, `@v6` and `@v7`. Also corrected a comment in
  `ci.yml` that described `UV_PYTHON_DOWNLOADS=never` while the value was
  `automatic` - the value was right and the annotation would have justified
  breaking it.
- **Documentation claims re-checked against the repository.** `docs/README.md`
  no longer says the build has not started or that the master PDF matches
  `BUILD_NOTEBOOK.md`; `docs/PROJECT_TREE.md`'s root listing now matches
  `git ls-files` exactly; and `requirements.txt` states the measured 260-package
  runtime resolution instead of an unverifiable 318.
- **Dead weight removed.** An unused regex constant in
  `tests/unit/test_dependency_consistency.py` (ruff's F401 does not cover unused
  module-level names) and `tests/unit/.gitkeep`, whose directory now holds three
  real test modules.
- **detect-secrets hashes are locale-dependent, and now cannot be.** The tool
  hashes the secret string and opens files with the interpreter's default
  encoding, so a file containing any non-ASCII byte hashes differently on Windows
  (cp1252) and Linux (utf-8). `docs/MCP_INTEGRATION.md:22` holds `gm_live_…` with
  a UTF-8 ellipsis; Windows recorded sha1 `53b0d961…`, Linux computes
  `f0f6a8c9…`, and CI reported a reviewed finding as a new secret. The hook now
  runs `python -X utf8 -m detect_secrets.pre_commit_hook`, so both platforms
  decode identically.
- **`.gitleaks.toml`** allowlists `.secrets.baseline` and nothing else.
  detect-secrets stores 40-character sha1 hashes there, which `generic-api-key`
  matches on entropy - so gitleaks failed exactly when a reviewed baseline update
  landed. `useDefault = true` keeps every rule; verified by probe that the same
  string is still caught in any other file.
- **`.secrets.baseline` is now platform-portable.** It was generated on Windows
  and stored result paths as `docs\BUILD_NOTEBOOK.md`, which Linux CI never
  matches - so a reviewed finding reappeared as an unreviewed one and the
  `hooks` job failed on its first run. Paths are POSIX now, and
  `tests/unit/test_secrets_baseline.py` fails the build if backslashes return.
- **Eight defects in `BUILD_NOTEBOOK.md` S1.2**, corrected in the notebook in the
  same commit. Every pinned hook revision was stale and `id: ruff` is now a
  deprecated alias; `mirrors-mypy` typechecks in an isolated environment that
  cannot see six of `guardmem-core`'s seven dependencies; detect-secrets was
  missing despite RULES §4; `--cov=guardmem_core` makes coverage report
  `module-not-measured` and silently stop measuring; `lint` omitted the
  import-linter contracts that RULES §2.4 makes a gate; `PYTHONIOENCODING=utf-8`
  is required or `lint-imports` exits 1 on Windows for an encoding reason;
  `extend-exclude` must cover every `.md`, not only `docs/`; and Makefile recipes
  must avoid shell metacharacters to survive `cmd.exe`.
- **The two dependency artifacts no longer drift.** The `[dependency-groups] dev`
  block carries exact pins mirroring `requirements/dev.txt`, so `uv lock` cannot
  resolve away from the requirements files. Verified by three consecutive
  `uv run` calls leaving testcontainers at 4.13.3 and redis at 5.3.1.
- **`ruff format` no longer rewrites Python inside root Markdown.** The Day 1
  `extend-exclude = ["docs"]` protected the design suite but left `README.md`,
  `DAILY_LOG.md`, `CHANGELOG.md`, `CONTRIBUTING.md` and `SECURITY.md` exposed,
  because `ruff-format` declares `types_or: [..., markdown]` and the hooks run
  `--force-exclude`. Now `extend-exclude = ["docs", "*.md"]`.
- **Four defects in `BUILD_NOTEBOOK.md` S1.1**, corrected in the notebook itself
  as its closing rule requires. The step told you to run `uv sync`, which is
  exact and would have uninstalled ~300 of the 311 installed packages; its
  acceptance check `uv run python -c "import guardmem_core"` could not pass,
  because `[tool.uv.sources]` says where to resolve a package but does not
  install it without a matching `dependencies` entry; it lacked
  `extend-exclude = ["docs"]`, so current ruff would reformat Python code blocks
  inside the design documents; and it omitted the `import-linter` contracts and
  `branch = true` coverage that `RULES.md` §2.4 and §5 both require.
- **`arq` / `redis` version conflict.** `arq` 0.28.0 constrains `redis<6`;
  resolving the dependency groups independently lands redis on 8.x, which
  silently drags the worker queue back to `arq` 0.25.0 with no error. Pinned
  `redis==5.3.1` so the newer queue wins. Revisit when `arq` supports redis 6+.

### Known issues

- **The master PDF is content-stale.** `docs/GuardMem_AI_Master_Build_Notebook.pdf`
  still matches its recorded SHA-256 byte for byte, so the frozen artifact is
  intact — but its content predates the 2026-09-09 consistency pass. It contains
  `gh repo create guardmem-ai`, the invalid model id
  `claude-haiku-4-5-20251001`, previous-generation `claude-sonnet-4-5` and
  `claude-opus-4-1`, no `tau_mid`, and no Checkpoint B section. `docs/README.md`
  states the PDF and `BUILD_NOTEBOOK.md` "hold the same content"; that sentence
  is inaccurate. **Build from the Markdown.** The PDF is protected and must not
  be edited — corrections go in `BUILD_NOTEBOOK.md`.
- **Dependabot is not configured.** `docs/RULES.md` §4 asks for it weekly, and it
  is the prerequisite for pinning GitHub Actions to full commit SHAs rather than
  majors. It belongs with the security workflow, not with S1.2.
- Branch protection on `main` is not yet enabled (`BUILD_NOTEBOOK.md` S0.3).

## Project history

Recorded for context; these predate the changelog.

- **2026-09-09** — Repository reset to a documentation baseline. Everything
  outside `docs/` was removed, including a 225-file scaffold of empty modules
  whose `.py` files were 0 lines. `docs/PROJECT_TREE.md` now closes with the
  rule that produced the reset: the tree is a blueprint, and files are created
  at the step that needs them.
- **2026-09-09** — Consistency pass across the spec suite. Reconciled a decision
  matrix that referenced a threshold which did not exist (`tau_mid`), three
  different values for median review time, two different coverage numbers, an
  ADR that contradicted the spec of record three ways, and stale model ids.
