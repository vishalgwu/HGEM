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
- **Pinned tool versions in the notebook have drifted a major release.** S1.2
  pins ruff `v0.7.0` and mypy `v1.13.0` in `.pre-commit-config.yaml`; current
  resolution gives ruff 0.16.6 and mypy 2.3.1, and `pytest-asyncio` went 0.x to
  1.x with configuration changes. Re-check `asyncio_mode = "auto"` when S1.2 is
  built.
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
