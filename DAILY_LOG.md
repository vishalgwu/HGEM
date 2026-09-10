# DAILY_LOG.md

Created at `BUILD_NOTEBOOK.md` step S0.4. Written every day per §0.6.

**Why this file exists, in the notebook's own words:** *"On day 19 you will not
remember why you set `tau_hi` to 0.78, and the log is the only place that answer
will exist."*

Format per entry — what shipped, what broke, tomorrow's first step. Keep it
honest: a log that only records successes is worthless three weeks later, when
the thing you need is the record of what went wrong and why.

---

## 2026-09-10 — Day 0

**Shipped**

- Read the full design suite end to end: 10 markdown docs, 5 ADRs, 3 runbooks,
  9 diagrams, and all 30 pages of the master PDF.
- **S0.3** — restored `.gitignore` (the 2026-09-09 reset had removed it). Repo
  and remote already existed, so no second repo was created; the notebook's
  `gh repo create guardmem-ai` line is stale and the corrected S0.3 in
  `BUILD_NOTEBOOK.md` says so.
- **S0.1** — toolchain verified: uv 0.9.8, Docker 28.5.1, Node v24.14.1, git
  2.51.0. Installed uv-managed CPython **3.12.12**.
- Created the **HGEM venv** at `.venv` (`--prompt HGEM --seed`). 3.12 because
  every `pyproject.toml` in the spec pins `>=3.12`; the machine's default
  `python` is 3.10 and must not be used here.
- Collected the whole dependency set from the specs into layered
  `requirements/` files — 69 direct packages, each citing the step or clause
  that requires it — plus `requirements.lock.txt` (311 packages, transitive).
- Installed the full runtime + dev set into the venv. All 62 top-level modules
  import; ruff/mypy/pytest/alembic/locust/pip-audit/lint-imports all respond;
  `pip-audit` reports no known vulnerabilities.
- **S0.2** — created `.env` (gitignored) and `.env.example` from the S1.4
  variable set. API keys deliberately left blank.
- Added `.secrets.baseline` so `detect-secrets` passes.
- Root docs: README, LICENSE, SECURITY, CONTRIBUTING, CHANGELOG, this file.

**What broke / what I learned**

- **The master PDF is stale.** Its SHA-256 still matches the recorded hash byte
  for byte, so the frozen artifact is intact — but its *content* is the
  pre-reconciliation version. It contains `gh repo create guardmem-ai`, the
  invalid model id `claude-haiku-4-5-20251001`, previous-generation
  `claude-sonnet-4-5` / `claude-opus-4-1`, no `tau_mid`, and **no Checkpoint B
  section at all**. `docs/README.md` claims the PDF and `BUILD_NOTEBOOK.md`
  "hold the same content" — that sentence is now wrong. **Build from the
  Markdown only.**
- **`arq` 0.28.0 constrains `redis<6`.** Resolve the dependency groups
  independently and redis lands on 8.1.0; combine them and redis 8 silently
  drags the worker queue back to arq 0.25.0. Nothing errors — you would just
  quietly ship a three-release-old queue. Pinned `redis==5.3.1` so the newer
  queue wins. Revisit when arq supports redis 6+.
- **A "complete" `.env` breaks the app.** S1.4's `Settings` uses
  `extra="forbid"`, which rejects any dotenv key that is not a declared field —
  including keys with no `GM_` prefix. Tested: both `GM_BUDGET_DAILY_USD=25`
  and `GOOGLE_CLOUD_PROJECT=x` raise `ValidationError`. So `.env` carries only
  the 18 fields S1.4 declares; every later-step variable is commented out and
  labelled with the step that activates it.
- **`.env.example` failed the project's own security gate.** The dev Postgres
  URL carries an inline `guardmem:guardmem` credential and scans as Basic Auth
  Credentials. `RULES.md` §4 puts detect-secrets in pre-commit and CI, so it
  would have been rejected at S1.2. Fixed with `.secrets.baseline`.
- **Pinned tool versions in the notebook have drifted a major release.**
  `.pre-commit-config.yaml` in S1.2 pins ruff `v0.7.0` and mypy `v1.13.0`;
  current resolution gives ruff 0.16.6 and mypy 2.3.1, and pytest-asyncio went
  0.x → 1.x with config changes. Re-check `asyncio_mode = "auto"` at S1.2.
- **presidio does not detect MRNs out of the box.** Verified with a live
  `AnalyzerEngine()` run — it tags PERSON and PHONE_NUMBER on a sample clinical
  string and misses the MRN entirely. That is exactly why S11.3 calls for custom
  MRN and case-number recognizers; do not assume the defaults cover clinical ids.
- Decided **not** to recreate the empty-module scaffold. Commit `b108e48` did
  that once — 225 files, with `settings.py` and `orchestrator.py` at 0 lines —
  and `0e0bc18` reverted it. `PROJECT_TREE.md` closes by forbidding it. Files
  get created at the step that needs them.

**Still open**

- Branch protection on `main` (S0.3) — needs GitHub Settings → Branches;
  requires a PR + status checks. `gh` CLI is not installed locally.
- API keys not yet pasted into `.env`. Anthropic is needed Day 2; OpenAI is
  needed **Day 3** for embeddings, earlier than the S0.2 table's "Day 9".
- Two doc corrections worth making: the `docs/README.md` claim about the PDF,
  and the stale pre-commit pins.

**Tomorrow's first step**

`S1.1` — the uv workspace and the `guardmem-core` package.
`DONE WHEN: uv run python -c "import guardmem_core"` exits 0.

---

## 2026-09-10 — Day 1 · S1.1 (uv workspace)

**Shipped**

- **S0.4 verified** rather than re-done: `docs/` already holds 10 markdown
  documents + the master PDF, 5 ADRs, 3 runbooks, 9 diagrams, and `DAILY_LOG.md`
  exists — all on `origin/main`.
- **S1.1 complete.** Root `pyproject.toml` (uv workspace, ruff, mypy, pytest,
  coverage, import-linter), `packages/guardmem-core/` with its own pyproject and
  `src/guardmem_core/__init__.py`, the six `tests/` directories, `.python-version`
  pinned to 3.12, and `uv.lock` (49 packages).
- Acceptance: `uv run python -c "import guardmem_core"` exits 0, and
  `ruff check`, `ruff format --check`, `mypy --strict`, `lint-imports` and the
  detect-secrets hook all pass. 6/6.

**What broke / what I learned** — four defects in S1.1 as written, all now fixed
in the notebook in this same commit:

- **`uv sync` would have destroyed the environment.** S1.1 says to run it.
  `uv sync` is *exact* — it uninstalls everything not in the lock. Against the
  venv built from `requirements-dev.txt` that is ~300 of 311 packages removed,
  reported only as a list of `-` lines. Proved it on a scratch workspace before
  going near the real venv. `uv run` is *inexact* and safe. Used `uv lock` +
  `uv pip install -e` instead; venv went 312 → 313 packages, exactly the +1.
- **The DONE WHEN could not pass as specified.** `[tool.uv.sources]` only says
  where to resolve `guardmem-core` from; it does not install it. Without
  `dependencies = ["guardmem-core"]` on the root project,
  `uv run python -c "import guardmem_core"` raises `ModuleNotFoundError`. The
  step's own acceptance check contradicted its own config.
- **ruff now formats Python inside Markdown.** `ruff format .` wanted to rewrite
  `docs/BUILD_NOTEBOOK.md` — including collapsing the aligned `NewType` block in
  S1.5. `make lint` at S1.2 would have failed on the design suite, and anyone
  running `ruff format` would have silently edited the specs. Fixed with
  `extend-exclude = ["docs"]`. This is the version drift from Day 0 biting:
  ruff 0.16.6 vs the 0.7.0 the notebook pins.
- **Two gates RULES.md requires were never in the config.** `import-linter`
  contracts (RULES 2.4 + PROJECT_TREE "Ownership & Dependency Rules") and
  `branch = true` under coverage (RULES 5 requires 100% *branch* coverage on
  decision branches, which line coverage cannot measure). Added both. The import
  contract was then verified by deliberately adding `import fastapi` to
  `guardmem_core` — it breaks with exit 1 and passes with exit 0. A gate that
  cannot fail is not a gate.

Also worth recording:

- **RULES.md's "custom ruff rules" GM001 and GM002 do not need to be written.**
  Ruff has no plugin system, so they could never exist as described — but both
  are already covered by stock rules the notebook's own selector list enables:
  GM001 (`except: pass`) is **S110**, GM002 (call without timeout) is **S113**.
  Verified both present in ruff 0.16.6.
- **`uv lock` resolved against CPython 3.13** until pinned, because
  `requires-python = ">=3.12"` permits it. `.python-version` now holds `3.12` so
  the lock, the venv and `target-version = "py312"` agree.
- **`pytest` exits 5** ("no tests collected") on a repo with no tests. S1.2's
  `make test` target will fail on that unless S1.2 ships its first real test or
  the target tolerates exit 5. Noted in the notebook.

**Still open**

- Branch protection on `main` (S0.3) — needs GitHub Settings; no `gh` CLI here.
- API keys still blank in `.env`.
- Two sources of dependency truth now exist: `pyproject.toml` + `uv.lock`, and
  `requirements/*.txt`. They agree today. Decide at S1.2 whether to generate
  `requirements/` from `uv export` rather than maintaining both by hand.

**Tomorrow's first step**

`S1.2` — pre-commit config, Makefile, CI skeleton. Bring the ruff/mypy pins in
`.pre-commit-config.yaml` up to what is actually installed (0.7.0 → 0.16.6,
1.13.0 → 2.3.1) rather than copying the stale versions from the notebook.

---

<!--
Template for the next entry:

## YYYY-MM-DD - Day N

**Shipped**
-

**What broke / what I learned**
-

**Still open**
-

**Tomorrow's first step**
-
-->
