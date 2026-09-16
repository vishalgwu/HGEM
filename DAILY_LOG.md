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

## 2026-09-10 — Day 1 · debug pass

A screen of everything built so far, before moving on to S1.2. Three real
defects, two of them mine.

**Fixed**

- **A fresh clone could not import `guardmem_core`.** Found by actually cloning
  and following README verbatim rather than reading it. `requirements.lock.txt`
  pins third-party packages only; the workspace package needs
  `uv pip install -e packages/guardmem-core`, which no document mentioned —
  README and CONTRIBUTING were written before the workspace existed at S1.1.
  This is the S28.3 cold-start gate failing on day 1. Both documents now carry
  the step, the verification commands, and a description of the failure mode.
  Re-tested cold from a fresh clone afterwards: import and pytest both green,
  and all five contributor gates pass in that clone.
- **Coverage would have measured nothing in CI.** `source` by path only works
  while the package is installed editable; installed properly, the imported code
  comes from site-packages and coverage silently reports 0% — which reads as a
  broken test suite, not a broken config. Now `source_pkgs = ["guardmem_core"]`,
  which follows the import.
- **`.gitignore` was thin and `.gitattributes` was absent.** `.pytest_cache/`
  escaped only because pytest writes its own `.gitignore` inside it; relying on a
  third-party tool's internals for repo hygiene is not a plan. Added the missing
  patterns — `*.egg-info/` and `build/` now matter because S1.1 made this a
  buildable package — plus terraform for S26.1. `.gitattributes` normalises line
  endings, since this builds on Windows locally and Linux in CI. Verified
  `git add --renormalize .` is a no-op, so it locks in the current state rather
  than rewriting anything, and `*.pdf binary` protects the master notebook —
  hash re-checked after the change, still `D4E49FEF…57CD`.

**Added** — `tests/unit/test_package_contract.py`, the first real test. It
asserts at *runtime* what import-linter asserts statically. Those catch
different things: `lint-imports` reads source and fails on a written
`import fastapi`; this test watches `sys.modules` in an isolated subprocess and
also catches a framework pulled in *indirectly*. Proved it by making
`guardmem_core` import fastapi — the test failed reporting
`['fastapi', 'starlette']`, and that transitive `starlette` is exactly what the
static contract cannot see.

Shipping it also resolved the pytest exit-5 problem from S1.1. The right fix for
"coverage reports 0% because nothing imports the package" is a test, not a
looser gate.

**What I got wrong** — my own `# noqa: S603` in that test was dead code, caught
by RUF100: the flake8-bandit rules are already off for `tests/` via
per-file-ignores. Kept the rationale as a plain comment. Also corrected a stale
claim in the `requirements.lock.txt` header, which said the venv matched it
311/311 — still true, but incomplete once `guardmem-core` is installed alongside.

**State** — 7/7 gates green locally, 5/5 in a fresh clone, `pip-audit` clean.
`en-core-web-lg` and `guardmem-core` show as *skipped* by pip-audit because
neither is on PyPI; that is expected, not a failure.

**Tomorrow's first step**

`S1.2` — pre-commit, Makefile, CI skeleton. `make test` now has a real test to
run, so the target can pass honestly rather than tolerating exit 5.

---

## 2026-09-10 — Day 1 · environment rebuild (S1.1 re-verified)

Tore the virtual environment down to nothing and rebuilt it from the documented
cold start, to prove S1.1 reproduces rather than merely persists.

**Shipped**

- **Teardown.** Deleted `.venv` outright — 48,176 files, 1.63 GB. Checked first
  that it was gitignored with zero tracked files, so nothing unreproducible was
  at risk.
- **Rebuild, exactly as README documents it:** `uv venv --python 3.12 --prompt
  HGEM --seed .venv` → `uv pip install -r requirements.lock.txt` (311) →
  `uv pip install -e packages/guardmem-core` → `python -m spacy download
  en_core_web_lg`.
- **The rebuild reproduces the old environment exactly.** 313/313 packages at
  identical versions, Python 3.12.12, `_editable_impl_guardmem_core.pth` present,
  `spacy.load('en_core_web_lg')` returns a 6-pipe model. The 313 reconciles as
  311 (lock, pip included) + `guardmem-core` (editable) + `en_core_web_lg` (not
  on PyPI).
- **S1.1 DONE WHEN re-confirmed** on the fresh env: `uv run python -c "import
  guardmem_core"` — python prints nothing, exit 0.
- **7/7 gates green:** ruff check, ruff format --check, mypy --strict,
  lint-imports, pytest, detect-secrets, pip-audit.
- **Negative-tested both import gates** rather than trusting a green tick. With
  `import fastapi` appended to `__init__.py`, `lint-imports` exits 1 naming
  `guardmem_core -> fastapi (l.7)`, and the runtime contract test fails with
  `['fastapi', 'starlette']` — the transitive `starlette` being exactly what the
  static contract cannot see. Restored to HEAD; tree clean.

**What broke / what I learned**

- **The two dependency artifacts have drifted, and they now actively fight each
  other.** Day 1 recorded them as agreeing "today" with the decision deferred to
  S1.2. They no longer agree:

  | Source | testcontainers |
  |---|---|
  | `requirements.lock.txt` and `requirements/dev.txt` | `4.13.3` (exact pin) |
  | `uv.lock`, resolved from the `>=4.8` floor in `pyproject.toml` | `4.15.0` |

  Caught in the act: right after the rebuild the env held 4.13.3, and the very
  next `uv run` printed `Uninstalled 1 package / Installed 1 package` and left
  4.15.0 behind. So `uv run` — which is what the S1.2 `Makefile` uses for
  `make test` — silently re-syncs to `uv.lock`, while README's install path puts
  the pin back. The environment is bistable and whichever command ran last wins.
  Nothing errors, which is what makes it worth writing down.

  Left at 4.15.0, the post-`uv run` state, because that is what any `make test`
  will converge on. **This is the S1.2 decision, now with evidence:** either
  generate `requirements/` from `uv export`, or pin the dev group in
  `pyproject.toml` so `uv.lock` agrees. Picking one is a real trade — the
  curated `requirements/` files carry a step citation per entry, and a
  machine-generated export throws that away.

- **`import-linter`'s cache produced a false FAIL, and can just as easily
  produce a false PASS.** After the negative test above, `lint-imports` kept
  reporting `guardmem_core -> fastapi (l.7)` against a source tree with no
  `fastapi` anywhere and an empty `git diff`. `.import_linter_cache/` held the
  violating parse verbatim. Grimp keys that cache on file **mtime**, and the
  restore (`cp` to a backup, `mv` it back) gave the file an mtime *older* than
  the cached entry, so the cache was never invalidated. `rm -rf
  .import_linter_cache` and it passes. The direction that actually matters is
  the inverse: the same mechanism will happily serve a stale PASS over a file
  restored by `git checkout`, which is a gate silently not gating. Added
  `.import_linter_cache/` to `.gitignore` with that note — it was previously
  ignored only by the `*` Grimp writes inside the directory itself, which is the
  same "third-party tool's internals doing our repo hygiene" problem already
  fixed for `.pytest_cache/`. Worth a `rm -rf` of tool caches in the S1.2 CI job
  rather than trusting cache invalidation.

- **`lint-imports` exits 1 whenever its stdout goes to `/dev/null` on Windows.**
  Not a contract failure — the contract is green. `import-linter` renders a
  `:brick: Building graph...` spinner through `rich`; with stdout at `/dev/null`
  rich takes its `legacy_windows_render` path, which encodes via **cp1252** and
  raises `UnicodeEncodeError: 'charmap' codec can't encode characters in
  position 0-2`. Redirect to a real file, pipe to `cat`, or run it plainly and
  it exits 0 every time. `PYTHONIOENCODING=utf-8` fixes it; `NO_COLOR=1` does
  not. This is a landmine for S1.2: the moment `lint-imports` goes into the
  `Makefile` or CI behind any `>/dev/null`, it becomes a gate that fails for a
  reason having nothing to do with imports — on Windows only, so CI on Linux
  would stay green and the local run would not. Set `PYTHONIOENCODING=utf-8` in
  the `Makefile` and the workflow env.

- **Reproduced the cold-start break deliberately.** Between the lock install and
  the editable install, `import guardmem_core` raised `ModuleNotFoundError` —
  the exact failure the debug pass fixed in the docs. It is a useful reminder
  that `[tool.uv.sources]` says where to *resolve* a package, never to install
  it, and that the S1.1 text as originally written could not pass its own
  acceptance check.

- **The step as originally written is still the dangerous version.** It says run
  `uv sync`. Against a populated env that removes ~300 of 313 packages. Not run;
  the corrected S1.1 in `BUILD_NOTEBOOK.md` is what was built.

**Still open**

- The `testcontainers` split above — decide at S1.2.
- Branch protection on `main` (S0.3) — needs GitHub Settings; no `gh` CLI here.
- API keys still blank in `.env`. Anthropic needed Day 2; OpenAI needed Day 3
  for embeddings.
- Two doc corrections outstanding: `docs/README.md` still says the repo
  "contains only `docs/`" and still claims the PDF and `BUILD_NOTEBOOK.md` hold
  the same content. The PDF was re-checked this session — SHA-256 still
  `D4E49FEF…57CD`, and it has zero occurrences of "Checkpoint" or `tau_mid`,
  so the claim is measurably false. Root `README.md`'s layout block also omits
  `packages/` and `tests/`.

**Tomorrow's first step**

`S1.2` — pre-commit, Makefile, CI skeleton. Bring the `.pre-commit-config.yaml`
pins up to what is installed (ruff 0.7.0 → 0.16.6, mypy 1.13.0 → 2.3.1), and
settle the `requirements/` vs `uv.lock` split before `make test` bakes `uv run`
into the daily loop.

---

## 2026-09-10 — Day 1 · S1.2 (pre-commit, Makefile, CI)

**Shipped**

- **`.pre-commit-config.yaml`** — ruff-check + ruff-format, gitleaks,
  detect-secrets, and mypy + import-linter as `local` hooks. All six pass on
  `--all-files`. pre-commit bootstrapped a Go toolchain for gitleaks on its own,
  which was the risk I expected to have to work around and did not.
- **`Makefile`** — `help` (default), `hooks`, `fmt`, `lint`, `imports`,
  `typecheck`, `test`, `test-all`, `audit`, `clean`, plus `dev`/`down`/`migrate`/
  `seed`/`eval` for the steps that create their inputs.
- **`.github/workflows/ci.yml`** — two jobs. `gates` runs `make lint`,
  `make typecheck`, `make test`; `hooks` runs `pre-commit run --all-files`, which
  is where RULES §4's "gitleaks and detect-secrets in CI" is actually satisfied.
  Both install with `uv sync --locked --dev`, cached on `uv.lock`,
  `permissions: contents: read`, concurrency cancels superseded runs.
- **`tests/unit/test_dependency_consistency.py`** — four tests that make the
  two-locks invariant enforceable instead of aspirational: no version may
  disagree between `uv.lock` and `requirements.lock.txt`, `uv.lock` must stay a
  subset of it, every dev-group entry must be an exact `==` pin, and the dev
  group must match `requirements/dev.txt`.
- **Installed GNU Make 4.4.1** (`winget install ezwinports.make`). It is not on
  Windows by default and does not ship with Git for Windows, so the S1.2
  acceptance check was unrunnable locally before this.
- **S1.2 DONE WHEN passes:** `make lint && make typecheck && make test`, chained
  exit 0, 6 tests. `docs/BUILD_NOTEBOOK.md` S1.2 rewritten with all eight
  corrections, as its own closing rule requires.

**Decisions taken**

- **The dependency split is settled: keep both artifacts, pin both.** The dev
  group in `pyproject.toml` now carries exact `==` pins mirroring
  `requirements/dev.txt`, so `uv lock` cannot resolve away from it. Verified: three
  consecutive `uv run` calls now leave testcontainers at 4.13.3 and redis at 5.3.1,
  matching the pip lock. The bistability recorded yesterday is gone. The
  alternative — generating `requirements/` from `uv export` — was rejected because
  it would discard the per-entry citation of the step or RULES clause that
  justifies each package, which is the thing that makes that file worth reading.

**What broke / what I learned**

- **Pinning the dev group nearly undid the Day 0 `arq` decision.** Mirroring
  `requirements/dev.txt` exactly meant writing
  `testcontainers[postgres,neo4j,redis]`, and the `redis` extra resolved redis to
  **8.1.0** — while `requirements/stores.txt` pins **5.3.1** on purpose, because
  `arq` 0.28.0 constrains `redis<6`. `uv sync --dry-run` showed it plainly:
  `- redis==5.3.1 / + redis==8.1.0`. Fixing one drift had introduced another, in
  the same commit. Dropped the extras: no test runs a container today, and the
  extras belong in the commit that first does. This is why the new consistency
  test asserts the *subset* property and not just version agreement — version
  agreement alone would not have caught it.

- **`--cov=guardmem_core` makes coverage quietly wrong.** The notebook's `make
  test` names the package on the command line while `source_pkgs` already declares
  it in `pyproject.toml`. That combination emits `CoverageWarning:
  module-not-measured` and drops the module from measurement. Harmless at zero
  statements, which is exactly why it would have gone unnoticed until there was
  real code and a 90% gate depending on the number. Bare `--cov` is clean; isolated
  by running both forms side by side.

- **The `extend-exclude = ["docs"]` fix from Day 1 was half a fix.** `ruff-format`
  declares `types_or: [python, pyi, jupyter, markdown]`, so pre-commit hands it
  every Markdown file, and both ruff hooks run `--force-exclude` so the config
  applies even to explicitly-passed paths. `docs/` was protected; the ROOT markdown
  — README, DAILY_LOG, CHANGELOG, CONTRIBUTING, SECURITY — was not. Proved it with
  a deliberately misformatted `python` block: rewritten at the root, skipped under
  `docs/`. No root file has a python fence today, which is the only reason it had
  not bitten. Now `extend-exclude = ["docs", "*.md"]`, and `ruff format .` went
  from considering 8 files to 3. Illustrative code in prose is often deliberately
  wrong — a snippet showing what not to do has to stay that way.

- **`mirrors-mypy` could not have worked here.** It typechecks in an isolated
  virtualenv seeing only `additional_dependencies`, which S1.2 gives as
  `[pydantic]`; `guardmem-core` already declares six more. The hook would start
  failing on missing stubs the moment real code imports one, while `make typecheck`
  passed — two different answers to the same question. Both mypy and import-linter
  are `local` hooks running the project toolchain, so hook, Makefile and CI are one
  command.

- **`id: ruff` is a deprecated alias.** The installed hook set reports it as
  "ruff (legacy alias)"; `ruff-check` is current. Read the cached
  `.pre-commit-hooks.yaml` rather than guessing — which is also how the markdown
  `types_or` above turned up.

- **My own Makefile had the bug its header warns about.** Unquoted parentheses in
  the `help` recipe are a `sh` syntax error, and `make help` exited 2 on the first
  run. Quoting would fix `sh` and break `cmd.exe`, which prints the quotes. Also
  `sh` collapses unquoted runs of spaces, so column-aligned help is not portable at
  all — the entries read `name - description` now, which degrades cleanly.

**Still open**

- Branch protection on `main` (S0.3) — needs GitHub Settings; no `gh` CLI here.
- API keys still blank in `.env`. Anthropic needed Day 2; OpenAI Day 3.
- The CI badge in `README.md` is unverified until this branch is pushed and
  Actions runs for the first time. Workflow structure and `uv lock --check` were
  validated locally, but no run has executed.
- `docs/README.md` still says the repo "contains only `docs/`" and still claims
  the master PDF and `BUILD_NOTEBOOK.md` hold the same content. Both were false
  yesterday and are more false now.

**Tomorrow's first step**

`S1.3` — the docker-compose dev stack: Postgres+pgvector, Redis, Neo4j, Langfuse,
Phoenix. `make dev` already points at
`infra/docker/docker-compose.dev.yml`; that step creates it.

---

## 2026-09-10 — Day 1 · S1.2 first CI run

Pushed the branch. The `gates` job — the actual S1.2 acceptance check — passed
on ubuntu first time. The `hooks` job failed, on a tree where all six hooks
pass locally.

**What broke**

- **`.secrets.baseline` is not portable, and nobody would have noticed until
  something worse happened.** `detect-secrets` records result paths with the
  separator of the machine that generated the baseline. Ours was generated on
  Windows at S0.2, so it stores `docs\BUILD_NOTEBOOK.md`. Linux CI scans
  `docs/BUILD_NOTEBOOK.md`, misses the key, and reports an already-reviewed
  finding as a brand-new secret. Confirmed by looking the key up both ways
  against the committed baseline: the POSIX form is a MISS.

  Forward slashes work on *both* platforms — verified that the hook resolves a
  POSIX key correctly on Windows and does not rewrite it back to backslashes —
  so the baseline is now POSIX and `tests/unit/test_secrets_baseline.py` pins
  it, along with key/filename agreement and no-stale-paths.

  Worth being precise about why this one matters more than its size suggests.
  The failure is quiet in the dangerous direction: a mismatched path resurfaces
  a *previously approved* finding, and the obvious reaction under a red build is
  to re-run the hook and commit whatever baseline it emits. That is the exact
  motion that allowlists an unreviewed secret. Regenerating on Windows will
  reintroduce it, which is why it needed a test and not a note.

- **My first fix was worse than the bug.** Replacing every `\` in the file
  also rewrote the `exclude` regexes, whose backslashes escape literal dots:
  `^requirements\.lock\.txt$` became `^requirements/.lock/.txt$`, silently
  disabling the lock-file exclusion. Caught by reading the diff rather than the
  test result — the hooks still passed. The fix now replaces the two path
  strings by their exact JSON representation and touches nothing else; the diff
  is four lines.

- **A `sort_keys=True` round-trip is not a small edit.** Re-serialising the
  baseline reordered it into a 66-line diff for a two-line change. Editing the
  raw text keeps the tool's own formatting.

**Still open**

- The `hooks` job has not yet passed. `gates` has. Re-verify both after this push.
- Branch protection on `main` (S0.3); API keys blank in `.env`.

**Tomorrow's first step**

`S1.3` — the docker-compose dev stack.

---

## 2026-09-10 — Day 1 · S1.2 CI, root cause

Two more CI runs, both `gates` green and `hooks` red. Stopped guessing and
reproduced the job in a Linux container instead — `docker run python:3.12-slim`,
fresh clone of the pushed branch, `uv sync --locked --dev`, `pre-commit run
--all-files`. That gave the message the API would not: `Secret Type: Secret
Keyword / Location: docs/MCP_INTEGRATION.md:22`.

**The real root cause: detect-secrets hashes are locale-dependent.**

`detect-secrets` hashes the secret *string*, and opens files with the
interpreter's default encoding — cp1252 on Windows, utf-8 on Linux. Line 22 of
`MCP_INTEGRATION.md` holds the placeholder `gm_live_…` with a UTF-8 ellipsis
(`e2 80 a6`). The two platforms decode that differently, so they hash it
differently:

| decode | secret | sha1 |
|---|---|---|
| utf-8 (Linux) | `gm_live_…` | `f0f6a8c9…` |
| cp1252 (Windows) | `gm_live_â€¦` | `53b0d961…` ← what the baseline held |

Linux computed a hash the baseline did not contain, so an already-reviewed
finding read as a brand-new secret. Confirmed by hashing the raw bytes under
each codec and matching against the committed value — the baseline was written
from a mis-decoded string.

So the baseline was non-portable in *two* independent ways: path separators
(yesterday's fix) and hash encoding (this one). The separator fix was correct
and insufficient, which is why the second run failed identically.

Fixed structurally rather than by patching the value: `detect-secrets` is now a
`local` hook running `python -X utf8 -m detect_secrets.pre_commit_hook`, so both
platforms decode identically by construction. Baseline regenerated the same way
and re-normalised to POSIX paths — regenerating writes the local separator back,
which the guard test catches.

**gitleaks then flagged the detect-secrets baseline.** The new hash is 40 hex
characters with entropy 3.69, so `generic-api-key` matched it — meaning gitleaks
fails precisely when a reviewed baseline update lands. Left alone that trains
the worst possible reflex: the way to a green build becomes "stop looking at
baseline diffs", on the one file where a careless change allowlists a real
secret. Added `.gitleaks.toml` allowlisting that path only, with `useDefault =
true` so no rule is weakened.

Negative-tested, because an allowlist that is too broad is worse than the false
positive it removes: the same high-entropy string is CAUGHT in a normal file and
ALLOWED in `.secrets.baseline`.

**What I would do differently**

Reproduce first. Two pushes were spent on a hypothesis that was true but
partial, and the container gave the exact answer in about four minutes. The
annotations API only ever says "Process completed with exit code 1"; job logs
need admin auth this session does not have.

**Still open**

- ~~`hooks` has not yet gone green in CI.~~ **Resolved.** Run 34538444846 on
  `bdc2945` is green on both jobs, so S1.2's DONE WHEN is now satisfied in full
  — `make lint && make typecheck && make test` locally, and the badge green on a
  pushed branch. The container check predicted it exactly, which is the argument
  for reproducing before pushing rather than after.
- CI warns `actions/checkout@v4`, `actions/cache@v4` and `astral-sh/setup-uv@v5`
  run on deprecated Node 20. Worth bumping; not urgent, and deliberately not
  bundled into a fix commit.
- Branch protection on `main` (S0.3); API keys blank in `.env`.

**Tomorrow's first step**

`S1.3` — the docker-compose dev stack.

---

## 2026-09-10 — Day 1 · S1.2 debug and audit pass

A screen of everything built so far before moving to S1.3. CI was already green
by this point, so there were no failure logs left to read — the two failures had
been diagnosed and fixed earlier the same day. What remained was one real
workflow warning, some dead weight, and four documentation claims that were no
longer true.

**Scope check first.** Nothing in CI fails for a future step's reason. The
workflow runs only lint, typecheck, test and the hooks; the Makefile's
`dev`/`down`/`migrate`/`seed`/`eval` targets point at files that arrive at S1.3,
S3.1, S3.6 and S22.1, and CI never invokes them. So there is nothing deferred
here, and nothing masked.

**Fixed — workflow**

- **Node 20 deprecation.** Every run warned that `actions/checkout@v4`,
  `actions/cache@v4` and `astral-sh/setup-uv@v5` target Node 20 and are being
  forced onto Node 24. Bumped to `checkout@v5`, `cache@v6`, `setup-uv@v7` after
  confirming those majors exist upstream. Full-SHA pinning is the hardened form
  RULES §4 eventually wants, but it needs the weekly Dependabot that section
  also calls for; majors are the honest interim and the workflow says so.
- **A comment in my own workflow said the opposite of what the setting does.**
  `UV_PYTHON_DOWNLOADS: automatic` was annotated "fail instead of silently
  reaching for a different interpreter" — that describes `never`, which would
  disable downloads and make `uv python install` unable to satisfy 3.12 at all.
  The value was right and the comment was wrong, which is the worse of the two
  failure modes: it would have justified a wrong "fix" later.

**Removed — dead weight**

- `_NAME`, an unused compiled regex in `test_dependency_consistency.py`. Ruff
  did not catch it: `F401` covers unused *imports*, not unused module-level
  constants. Found by walking the AST of every tracked `.py` and diffing defined
  names against loaded ones; that scan is now clean repo-wide.
- `tests/unit/.gitkeep`. A `.gitkeep` exists to keep an empty directory tracked,
  and that directory now holds three real test modules. The other five
  `tests/*/.gitkeep` files stay — those directories are still empty.

**Fixed — documentation that had stopped being true**

- `docs/README.md` still said "documentation only … Implementation has not
  restarted … The repository contains only `docs/`". Two build steps have landed.
- `docs/README.md` still claimed the PDF and `BUILD_NOTEBOOK.md` "hold the same
  content". Measured, not asserted: the extracted text has zero occurrences of
  "Checkpoint" and zero of `tau_mid`, carries `claude-haiku-4-5-20251001` and
  the previous-generation Claude ids, and still says `gh repo create
  guardmem-ai`. Step coverage is identical — the same 96 steps and appendices
  A–G — so the Markdown is a strict superset. Recorded as a dated correction
  rather than a silent edit, because the sentence had been wrong since it was
  written.
- `docs/PROJECT_TREE.md` annotated `docs/` as "the only tree that exists today",
  and its root listing predated `.python-version`, `.gitignore`,
  `.gitattributes`, `.secrets.baseline`, `.gitleaks.toml` and the whole
  `requirements/` layer. The listing now matches `git ls-files` at root exactly,
  checked programmatically rather than by eye.
- `requirements.txt` claimed "318 packages including transitives". Re-measured
  with `uv pip compile`: the runtime-only set resolves to **260**, and adding
  `requirements/dev.txt` gives the 311 in `requirements.lock.txt`. The old number
  claimed more packages for the runtime set alone than the runtime-plus-dev lock
  contains, so it was never right.

**Verified, not assumed**

- Master PDF SHA-256 still `D4E49FEF…57CD` — byte-identical, untouched by any of
  today's work.
- All nine diagrams present, non-empty, and linked from `docs/README.md`.
- Every relative link in every Markdown file resolves to a file that exists.
- `ci.yml`, `.pre-commit-config.yaml`, `.secrets.baseline` and `.gitleaks.toml`
  all parse.
- Local: `make lint`, `make typecheck`, `make test` (10 tests), and all six hooks.

**After the push — one more warning, now also gone**

The bump cleared the Node 20 deprecation on the `gates` job (annotations 1 → 0),
but `pre-commit` still carried one: *"Failed to save: Unable to reserve cache …
another job may be creating this cache."* Both jobs run in parallel and derived
the same `setup-uv` cache key, so whichever finished second could not reserve
it. Benign — the loser just skips saving — but a warning that fires on every run
is one people learn to scroll past, which is the actual cost. Fixed with a
per-job `cache-suffix` rather than documented as a wart; the cache is a few MB
and two of them is not a real expense.

**Still open**

- Branch protection on `main` (S0.3) — needs GitHub Settings; no `gh` CLI here.
- API keys blank in `.env`. Anthropic needed Day 2; OpenAI Day 3.
- Dependabot config, which RULES §4 asks for weekly, does not exist yet. It is
  the prerequisite for SHA-pinning the actions, and belongs with the security
  workflow rather than here.
- A `git push` hung once and exited quietly without transferring anything;
  `git ls-remote` showed the branch still on the previous commit. Retrying
  worked immediately. Worth knowing that a silent push here is not proof of a
  push — check the remote ref. **Root cause found at S1.4 — see that entry. It is not the network, and “just retry” is the wrong lesson.**

**Tomorrow's first step**

`S1.3` — the docker-compose dev stack. Docker Desktop is running locally, which
today's debugging needed anyway.

---

## 2026-09-10 — Day 1 · S1.3 (dev datastore stack)

**Shipped**

- `infra/docker/docker-compose.dev.yml` — postgres+pgvector, redis, neo4j,
  phoenix. Every image pinned to an exact version, every service with a
  healthcheck, every published port overridable from the shell.
- `infra/docker/initdb/01-extensions.sql` — runs once on an empty data
  directory and creates `vector`, `pgcrypto` and `pg_trgm`, so the DONE WHEN
  passes straight after `make dev` with no manual `psql`.
- Makefile: `dev` now uses `up -d --wait`, plus `dev-reset`, `dev-ps` and
  `dev-logs`. `dev-reset` is a separate named target because `down --volumes`
  destroys every assertion and audit row in the local stack, and that should
  never be a flag somebody adds on a whim.
- `tests/unit/test_compose_stack.py` — four tests making the compose rules
  enforceable: images pinned to `MAJOR.MINOR` or better, every service declares
  a healthcheck, no host-port collisions, and a guard so the suite cannot pass
  vacuously if the glob stops matching. Negative-tested both main rules.

**S1.3 DONE WHEN — all three pass**

    SELECT '[1,2,3]'::vector   ->  [1,2,3]   (1 row)
    GET localhost:7474         ->  HTTP 200, and bolt auth works
    redis-cli ping             ->  PONG

`make dev` exits 0, which with `--wait` *is* the "all healthy" check. Extensions
present: vector 0.8.6, pg_trgm 1.6, pgcrypto 1.3. apoc 5.26.30 confirmed loaded
with `RETURN apoc.version()` rather than assuming `NEO4J_PLUGINS` took effect.

Also proved persistence, because a datastore stack that loses data on restart is
worse than none: wrote a row to each of the three stores, `make down`,
`make dev`, read all three back intact. Probe data removed afterwards — all
three stores verified empty.

**What broke / what I learned**

- **Langfuse as specified cannot work, and that is a real fork, so I asked.**
  `langfuse/langfuse:latest` is now **v4**, and v4 needs ClickHouse, MinIO, an
  authenticated Redis and a separate worker container — confirmed against
  upstream's own `docker-compose.yml`, not from memory. The four-line block in
  S1.3 is a **v2** configuration; on v4 it starts a container that crashes.
  Decision taken: defer to S13.1, where `docker-compose.observability.yml`
  already exists in the blueprint and where something finally reads it. Nothing
  in S1.3's DONE WHEN touches Langfuse. The alternative, pinning `langfuse:2`,
  works today but is end-of-life and stores dev traces in a data model v3+ does
  not carry forward.

- **The port conflict was not the one the notebook predicts.** S1.3's
  troubleshooting assumes "a local Postgres" and offers `brew services stop
  postgresql`, which is macOS-only. What actually held 5432, 6379, 7474 and 7687
  was **three containers from a different repository** —
  `com.docker.compose.project=01_setup`, created 2026-05-09, mounting
  `C:\college\Github\Research\Human-Gated-External-Memory-HGEM\...`. They
  have `restart: unless-stopped`, so starting Docker Desktop for this work
  brought them back. Not mine to stop: they carry their own data volume. Verified
  this stack on override ports instead, which also exercised the override path.
  Worth noting their Postgres is `postgres:15` — **no pgvector** — so pointing
  `GM_DATABASE_URL` at it would produce exactly the `type "vector" does not
  exist` error at the top of the notebook's troubleshooting table.

- **The Phoenix image is distroless, so `CMD-SHELL` can never work on it.**
  Every probe failed with `exec: "/bin/sh": stat /bin/sh: no such file or
  directory` while the application was serving HTTP 200 quite happily, and
  `make dev` correctly refused to report success. The exec form —
  `["CMD", "python", "-c", ...]` — runs the binary directly and passes. Good
  argument for `--wait`: the failure surfaced immediately instead of at the
  first `psql`.

- **`--appendonly yes` with no volume buys nothing.** S1.3 sets the flag on
  Redis and declares no `/data` volume, so the AOF it writes is discarded the
  moment the container is recreated. Same class of mistake as a healthcheck on
  only one of five services while the acceptance check reads "all healthy".

- **A missing healthcheck is worse than a failing one under `--wait`.** Compose
  treats a service with no healthcheck as satisfied as soon as it starts, so it
  passes the gate without ever being checked — and `--wait` returns while it is
  still booting. That is why the new test asserts every service declares one.

- **The `.secrets.baseline` path fix from earlier today was not durable, and I
  only found out because this step touched a baselined file.** Yesterday's fix
  corrected the committed file; it did nothing about the fact that
  `detect-secrets` **rewrites** those paths with the local separator every time
  it updates the baseline - which it does whenever a line number shifts. Adding
  S1.3 grew `BUILD_NOTEBOOK.md`, the baselined finding moved from line 543 to
  629, and the hook helpfully rewrote all the paths back to Windows form.
  `tests/unit/test_secrets_baseline.py` caught it, in real conditions rather
  than as a synthetic negative test, which is the clearest argument for that
  test existing.

  Repairing it by hand on every commit is the kind of step that gets skipped
  under pressure, on the one file where a careless change allowlists a real
  secret. So `scripts/normalise_secrets_baseline.py` now runs straight after
  `detect-secrets` in pre-commit and POSIX-ifies the paths automatically -
  idempotent, exits 1 only when it changed something, and touches the result
  path strings alone so the `exclude` regexes survive. It fixed all three paths
  unprompted on the next run.

  Two smaller things fell out of writing it: `scripts/*` needed a `T20`
  per-file-ignore, because a pre-commit hook's stdout *is* its interface while
  RULES §6 rightly bans `print` in the application; and a genuine new finding
  had to be reviewed rather than waved through - `POSTGRES_PASSWORD: guardmem`
  in the compose file, the same dev credential already baselined from
  `.env.example`. All five baselined findings were re-read; none is real.

**Still open**

- The `01_setup` containers still hold the documented ports. To use 5432 etc.,
  stop that stack (`docker stop hgem_postgres hgem_redis hgem_neo4j`) — it
  belongs to the `Research\Human-Gated-External-Memory-HGEM` checkout, so that
  is a decision for whoever owns that work, not this repo.
- Branch protection on `main` (S0.3); API keys blank in `.env`.
- Langfuse arrives at S13.1 with clickhouse and minio alongside it.

**Tomorrow's first step**

`S1.4` — the typed settings object. `.env.example` already carries exactly the
21 fields it declares, and the datastore URLs in it now match a stack that is
actually running.

---

## 2026-09-10 — Day 1 · S1.4 (typed settings)

**Cross-check first: `.env.example` needed no changes.** It already carried
exactly the 21 keys S1.4 declares, with every later-step variable present but
commented out and labelled with the step that activates it. The version of the
step I was handed is the pre-reconciliation one — it has no `GM_TAU_MID` and
carries `claude-haiku-4-5-20251001`, `claude-sonnet-4-5` and `claude-opus-4-1`,
all three of which Day 0 already recorded as invalid or previous-generation. The
committed template and the corrected notebook were right; I built from those.

**Shipped**

- `packages/guardmem-core/src/guardmem_core/settings.py` — the first real module
  in the package. 21 fields, DSN-typed store URLs, ordered-threshold and
  Neo4j-scheme validators, `frozen`, `strict`, `extra="forbid"`.
- `tests/unit/test_settings.py` — 16 tests. **100% coverage of settings.py,
  branches included**, which matters more than the number suggests: this is the
  first code the 85% gate has actually had to measure.
- All three DONE WHEN checks pass: prints `0.78`, rejects out-of-order
  thresholds, and fails loudly when a required variable is absent.

**What broke / what I learned**

- **`settings = Settings()` at module scope would have broken CI, and not
  obviously.** Module-scope construction makes *importing* the module a side
  effect: with no `.env` and no environment — exactly CI — the nine required
  fields raise, so the module cannot be imported at all, and a test that only
  wants the `Settings` class cannot run either. The notebook's own DONE WHEN
  nonetheless wants import-time failure on bad config, so both properties are
  real and they pull against each other.

  Resolved with an `@lru_cache` `get_settings()` plus a PEP 562 module
  `__getattr__` resolving the name `settings`. `from ... import settings` still
  constructs and still fails loudly; `from ... import Settings` does not.
  Verified from a directory with no `.env` and a `GM_`-free environment.
  `get_settings()` is also what RULES §2.4 means by "injected", which a
  module-level global is not.

- **`env_file_encoding` defaults to the locale encoding.** That is cp1252 on
  Windows, so a `.env` with any non-ASCII byte parses differently on the two
  platforms. Identical root cause to the detect-secrets baseline bug that cost
  two CI runs at S1.2. Set explicitly rather than discovered twice.

- **The DSN types are free validation, and I checked they are lossless before
  relying on it.** `PostgresDsn`, `RedisDsn` and `AnyUrl` all round-trip our
  URLs to the exact input string, so no downstream caller sees a normalised
  variant. A typo now fails at startup with a precise message instead of at the
  first connection, halfway through a request.

- **`strict=True` does not break environment parsing**, which I assumed it would.
  pydantic-settings coerces env strings before strict validation, so `GM_TAU_HI=0.78`
  still yields a float; strict only rejects passing a `str` where a `float` is
  declared from Python. Tested rather than reasoned about.

- **The first real import exposed an isort misconfiguration.** `guardmem_core`
  is installed editable, so ruff classified it as third-party and wanted it
  interleaved among pytest and pydantic. Cosmetic in one file; corrosive across
  a package whose whole ownership model is about dependency direction. Fixed
  with `known-first-party`, not by accepting the reordering.

- **A validator that only checks ranges would have missed the failure that
  matters.** Every threshold individually inside [0,1] can still be *ordered*
  wrongly, and that produces an empty band in the decision matrix — a class of
  candidate becomes unreachable while nothing errors anywhere. Same reasoning
  drove the Neo4j scheme check: S1.3 publishes the HTTP browser on 7474 and bolt
  on 7687, so a wrong URI answers on the port and fails in the driver.

- **The push stalls are the local credential helper, not GitHub.** Three pushes
  hung; the first two cleared on retry, the third did not, so I stopped guessing
  and traced it. `GIT_TRACE=1` puts the gap in one place:

      21:03:00.668  resolved executable dir ...
      21:03:18.904  start_command: git credential-manager store

  `git-credential-manager get` sat for ~18 seconds before the transfer began.
  That is why reads always looked fine — `git ls-remote` returned in 0.8s and
  `api.github.com` in 0.29s, and neither goes through that path. Where GCM
  exceeded my command timeout the push presented as a hang, and the retry
  succeeding was just GCM being warm.

  The honest conclusion is the opposite of the one I was forming: GitHub was
  never flaky, and "retry until it sticks" would have been the wrong habit.
  `GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=60` makes a genuinely
  stalled transfer abort instead of hang. Verifying the remote ref after every
  push stays the rule regardless.

**Still open**

- API keys are still blank in `.env`. `GM_ANTHROPIC_API_KEY` is needed at S2.2,
  which is the next step that actually calls a model.
- The `01_setup` containers still hold the documented datastore ports.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S1.5` — domain types and the error hierarchy. `NewType` ids so passing a raw
`str` where an `AssertionId` is expected is a type error, and the single
exception hierarchy RULES §2.3 maps to HTTP and MCP codes exactly once.

---

## 2026-09-11 — Day 1 · S1.5 (domain types and error hierarchy)

**Shipped**

- `types.py` — six `NewType` ids: TenantId, TraceId, CandidateId, AssertionId,
  EntityId, Namespace.
- `errors.py` — `GuardMemError` plus the seven subclasses RULES §2.3 names, each
  carrying `code`, `http_status`, **`mcp_code`** and `retryable`.
- `py.typed` — the PEP 561 marker, which did not exist and without which none of
  the above does anything for a consumer. See below.
- 22 new tests. 53 total, **100% coverage across all four modules**, branches
  included.

**What broke / what I learned**

- **The step as written makes this whole module decorative outside the package,
  and I nearly shipped it that way.** `guardmem-core` had no `py.typed`, so
  under PEP 561 every downstream type checker treats it as untyped — meaning
  `AssertionId` is indistinguishable from `str` the moment the gateway, the MCP
  server, the SDK or the eval harness imports it. Which is the only place the
  protection was ever for. `make typecheck` does not catch it, because it checks
  the package's own source directly, where the annotations are visible either
  way.

  Found only because I wrote the type test as a `mypy` subprocess rather than a
  runtime assertion, and it came back:

      Skipping analyzing "guardmem_core.types": module is installed,
      but missing library stubs or py.typed marker  [import-untyped]

  Confirmed with `uv build --wheel` that hatchling ships the marker in the
  distribution, not just the editable install — an editable install resolves
  through a `.pth` to the source tree, so it would have looked fine locally
  regardless.

- **A runtime test of a `NewType` proves nothing.** `NewType` erases entirely:
  at runtime `AssertionId("x")` *is* `"x"`, so equality, `isinstance` and
  behaviour assertions all pass whether or not the annotations do anything. The
  DONE WHEN asks for "a unit test", and the obvious unit test here is one that
  cannot fail. RULES §2.1's real claim — that a raw `str` where an `AssertionId`
  belongs **must be a type error** — needs mypy to be the thing reporting it.

- **RULES §2.3 asks for the MCP code and the step omits it.** "Each maps to an
  HTTP status *and an MCP error code* exactly once, in one table." Without
  `mcp_code` on the classes, that mapping lives only as a Markdown table in
  `MCP_INTEGRATION.md` §6 and `services/mcp_server` re-derives it at S6.2 —
  giving RULES the two tables it explicitly does not want. Worth noting the
  values are deliberately *not* unique: `GM_PROVIDER` and `GM_STORE` both map to
  `-32603` because the agent's correct response is identical, so the test
  asserts uniqueness on `code` and legality on `mcp_code`, and there is a test
  whose whole job is to stop someone "fixing" that duplication.

- **ruff's `N818` and the spec of record disagree, and the spec wins.** All seven
  subclasses are flagged for not ending in `Error`. RULES §2.3 names them, this
  step says copy them verbatim, and RULES §8 is explicit that when code and the
  spec of record disagree the code is wrong. Scoped the rule off for `errors.py`
  with the reasoning written into `pyproject.toml` rather than renaming classes
  away from the document that owns them.

- **I added a `slow` marker I did not need, and `--strict-markers` caught it.**
  Registered it, then measured: the mypy tests run in ~1s each. Deselection
  machinery for a one-second test is the kind of unused config I have spent this
  session removing, so I took it back out.

**Still open**

- API keys blank in `.env`; `GM_ANTHROPIC_API_KEY` is needed at S2.2.
- The `01_setup` containers still hold the documented datastore ports.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S1.6` — the schema layer, and the notebook calls it "the most important 90
minutes of week 1". Copy `MEMORY_ENGINE.md` §0 exactly; every model frozen,
strict and `extra="forbid"`, on the NewType ids landed here.

---

## 2026-09-11 — Day 1 · audit pass through S1.5

A screen of everything built so far, before S1.6. Most of it held up; three
things did not.

**Fixed — reuse**

- **`REPO_ROOT` had been copy-pasted into five test modules**, each as
  `Path(__file__).resolve().parents[2]`. The duplication is the small problem.
  The `parents[2]` is the real one: it encodes "this file sits exactly two
  directories below the root", so a module moved from `tests/unit/` to `tests/`
  keeps working *and starts pointing at the parent of the repository* — where a
  `pyproject.toml` and a `.secrets.baseline` may well exist and be the wrong
  ones. Nothing would fail; the tests would quietly check somebody else's files.

  Now defined once in `tests/conftest.py`, resolved by walking up for the
  `pyproject.toml` marker rather than by counting directories, and raising if it
  is not found. `PROJECT_TREE.md` already listed `tests/conftest.py` as the home
  for shared test code, so this is the documented location rather than a new
  invention. Added `conftest` to ruff's `known-first-party` so the import does
  not read as though it came from PyPI.

**Fixed — dead code I wrote**

- The `repo_root` **fixture** I added alongside that constant, "for tests that
  prefer injection", is requested by nothing. That is precisely the speculative
  code this project keeps telling itself not to write, and I wrote it in the
  same commit as the comment explaining why not to. Removed, and the now-unused
  `pytest` import with it.

**Fixed — a document count that was wrong**

- `BUILD_NOTEBOOK.md` S0.4 said `docs/` holds "nine markdown documents". It
  holds ten. The nine is not arbitrary — it is the count of *specifications* in
  `docs/README.md`'s reference table, excluding `README.md` itself, which is the
  index. The step now says ten and explains which ten, so the number cannot
  drift back into ambiguity.

**Checked and found clean**

- No dead module-level names anywhere else, across every tracked `.py` plus
  `conftest.py` (AST walk, defined-vs-loaded).
- No `TODO`/`FIXME`/`XXX`/`HACK` anywhere in code or config.
- Every relative Markdown link in the repository resolves.
- Master PDF still `D4E49FEF…57CD`.
- `PROJECT_TREE.md`'s listing covers every tracked root path.
- RULES §2.4 size limits: no module over 400 lines, no function over 50, and
  `C901` reports nothing over complexity 10.
- The developer `.env` still loads through the real `Settings` — checked by
  constructing it, not by reading the file.

**A false alarm worth writing down**

`git check-ignore dist` reported NOT IGNORED for `dist`, `build`, `htmlcov` and
`*.egg-info`, which looked like a real gap. It is not: those patterns end in a
slash, so they match directories only, and `check-ignore` cannot classify a path
that does not exist yet. Creating the directories and reading `git status`
showed all four correctly ignored. The lesson is the general one — when a check
disagrees with the config, test the behaviour rather than trusting either.

**Deliberately left for later**

- **Coverage is 100% against a `fail_under = 85`.** RULES §5 sets the release
  gate at 90 and schedules the raise "before the Phase 1 exit gate is signed
  off", which is Day 7. Raising it now would cost nothing today, but the gate
  change belongs where the document says it belongs, and moving gates early is
  its own kind of drift. Due at S7.2.
- Langfuse and its ClickHouse/MinIO stack arrive at S13.1.
- `GM_ANTHROPIC_API_KEY` is still blank; S2.2 is the first step that needs it.
- The `01_setup` containers from the `Research\Human-Gated-External-Memory-HGEM`
  checkout still hold the documented datastore ports.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S1.6` — the schema layer.

---

## 2026-09-11 — Day 1 · S1.6 (the schema layer)

**Shipped**

- `guardmem_core.schemas` — seven modules, 16 models. `base` (`GMModel` +
  `ObjectValue`), `candidate`, `entity`, `verdict`, `policy`, `receipt`,
  `review`, with `__init__` re-exporting the layer as one import site.
- `ReviewTaskId` and `ReviewerId` in `types.py`.
- 35 new tests — 88 total, **100% coverage across all twelve modules**, branches
  included.

**What broke / what I learned**

- **Half this step is not in the spec of record, and I nearly wrote it from
  imagination.** S1.6 says "copy the schemas from `MEMORY_ENGINE.md` §0
  exactly", but §0 has nothing for `entity.py`, `policy.py` or `review.py` —
  because §0 specifies the *pipeline*, and those three are what the pipeline
  writes and what reviews it. Their fields exist, just elsewhere:
  `ARCHITECTURE.md` §5 gives the `assertion` table in SQL and the entity graph
  in Cypher, `MCP_INTEGRATION.md` §2.7 gives `review.decide`'s wire contract,
  and `DESIGN_SYSTEM.md` §3.2 shows what a queue row has to render. Reading the
  downstream steps first — S3.1, S4.3, S5.4, S12.2, S18.1-3, S19.1-2 — is what
  turned six guesses into six derivations.

- **The WATCH OUT is the hardest instruction in the step.** Four classes that
  `PROJECT_TREE.md` names are not here: `Rule` and `PolicyPack`, because §2.3's
  "Rego-compatible" does not say whether a rule is a Python predicate, a
  compiled expression or a handle on a Rego module, and S12.2 decides that
  against a working engine; `Predicate`, which is ontology content (S3.5); and
  `Thresholds`, which S5.4 passes into `decide()`. Every one of them was
  tempting to sketch, and a sketch is exactly the refactor this step exists to
  avoid. What went in instead was `ObligationKind` — the three obligations
  `ARCHITECTURE.md` §2.3 actually names — wired so it does work *today*:
  `RiskVerdict.obligations` keeps the spec's `list[str]` and validates against
  it, so an unknown obligation raises. Without that it is silently inert, since
  S5.4 composes the obligations it recognises and ignores the rest.

- **Two spec clauses disagreed about the same field, twice, and the resolutions
  went opposite ways.** `MEMORY_ENGINE.md` §0 writes `candidate_id: str` while
  `RULES.md` §2.1 demands a `NewType` — resolved toward RULES, because
  `NewType` erases at runtime so the wire format and the column are identical
  either way and the looser reading would make all of S1.5 decorative. But
  `obligations: list[str]` versus `ARCHITECTURE.md` §2.3's structured
  `Obligation` resolved toward MEMORY_ENGINE, because that one is a real type
  change to the spec of record and RULES §8 says it takes an ADR, not a quiet
  edit. The difference is whether the disagreement is about spelling or about
  substance.

- **`strict=True` is not as strict as it reads, and the exception is in the
  most load-bearing field.** pydantic still accepts an `int` where a `float` is
  declared and converts it, so `object=500` on a candidate stores `500.0`. That
  is consistent with §0 typing the value as `float`, and it round-trips stably
  — but a model configured specifically to forbid silent conversions performing
  one deserves a test rather than a surprise. Same for `frozen=True`: it makes a
  model hashable only while every field is hashable, so `Provenance` can go in
  a set and `ExtractionResult` cannot.

- **The round trip only works because strict mode relaxes for JSON.** In Python
  a `datetime` field rejects a string, a `tuple` field rejects a list, and an
  enum field rejects its own value — in JSON all three are accepted, because
  JSON has no type that could carry them otherwise. I probed that before
  writing a line, and it is the difference between this layer being
  serialisable and not.

- **Two test files cannot share a basename here, and the failure is not
  local.** `tests/` has no `__init__.py`, so pytest imports modules under their
  bare names; adding `tests/unit/test_schemas.py` alongside the
  `tests/property/test_schemas.py` the step names aborted collection for the
  *entire* run with "import file mismatch". Renamed the unit one.

- **Parametrising a property test across sixteen models costs 200 seconds.**
  hypothesis spends roughly 0.45 s setting a test up regardless of how many
  examples follow, so three properties × sixteen models paid it 48 times — 22 s
  of fixed cost before an example is drawn. Measured at 100/250/500 examples to
  confirm the shape (57 s / 110 s / 198 s), then restructured to draw from
  `st.one_of` over all sixteen strategies: same three properties, same 500
  examples, 13.5 s. The thing that gets traded away
  is the guarantee that every model was exercised, so the run records the types
  it produced and asserts none was missed. A gate should not rest on "almost
  certainly sampled".

- **An `AuditEvent.payload` can hold a value that does not survive its own
  serialisation.** The field is `dict[str, object]` as the step specifies; a
  `datetime` nested inside validates, dumps to a string and returns a string.
  Harmless in most fields, not in this one — S5.5 digests canonical JSON of the
  payload, so a value that changes across a round trip breaks invariant I5 for
  every later link in the chain. Not forbidden here (a validator would cost a
  serialisation on every construction), but pinned by a test and written into
  the docstring so S5.5 inherits the problem knowingly.

**Also fixed, found while checking claims**

- Both READMEs said the next step was S1.3 and the root one still opened
  "Status: pre-implementation … there is no runtime code yet". Three steps
  stale. Now S1.1–S1.6, with the honest boundary: the typed foundation exists,
  no pipeline stage does.
- `PROJECT_TREE.md`'s `schemas/` listing named classes this step deliberately
  did not build. Relabelled with the step that lands each.

**Still open**

- `make test` is now ~26 s, up from 3.8 s; the property suite is 13.5 s of that
  at 500 examples, and coverage instrumentation most of the rest. Acceptable for
  the gate RULES §5 asks for, but it is the first time the suite has had a
  runtime worth watching.
- Coverage still 100% against `fail_under = 85`; the raise to 90 is S7.2.
- API keys blank in `.env`; `GM_ANTHROPIC_API_KEY` is needed at S2.2.
- Branch protection on `main` (S0.3), and the working branch is now twelve
  commits of four different steps under a name that says S1.2.

**Tomorrow's first step**

`S1.7` — the `LLMClient`, `VectorStore` and `GraphStore` protocols, plus the
in-memory fakes in `tests/fixtures/fakes.py` that every unit test for the next
four days runs on. One decision is already waiting there: `StoredAssertion`
carries no `embedding`, so `VectorStore.upsert` has to say where the vector
comes from.

---

## 2026-09-11 — Day 1 · S1.7 (store and LLM protocols) · END OF DAY 1

**Shipped**

- `llm/base.py` — `Tier`, `LLMResponse`, `LLMClient`.
- `memory/vector/base.py` — `VectorStore`. `memory/graph/base.py` —
  `GraphStore`.
- `tests/fixtures/fakes.py` — `FakeLLM`, `FakeVectorStore`, `FakeGraphStore`,
  plus `tests/fixtures/strategies.py`, the hypothesis strategies moved out of
  the property module.
- 20 new tests — 108 total, **100% coverage across nineteen modules**, branches
  included. Day 1 is done: S1.1 through S1.7.

**What broke / what I learned**

- **The DONE WHEN could not be satisfied, and the reason is the interesting
  part.** "Fakes satisfy the protocols under `mypy --strict`" — except
  `make typecheck` ran mypy over `packages/guardmem-core/src` only, so
  `tests/fixtures/fakes.py` was never checked, and `Protocol` is *structural*,
  so nothing at runtime would have noticed a wrong signature either. The step's
  own acceptance criterion was unverifiable with the toolchain as it stood.

  Extending the target to `tests` turned up 19 errors immediately, and fourteen
  were the same one: a raw `str` passed where a `CandidateId` or a `TenantId`
  was declared. That is precisely what S1.5 built those types to stop — and it
  was happening inside the suite whose entire job is to prove the schema layer
  holds. The protection had been real in the package and absent everywhere else
  for two steps, which is a close cousin of the missing `py.typed` at S1.5.

- **My own drift guard was unsound, and S1.7 is what exposed it.** S1.6 added
  three tests that claim to cover "every schema" by walking
  `GMModel.__subclasses__()`. `LLMResponse` is a `GMModel` living in
  `llm/base.py`; no test imported that module; `__subclasses__()` therefore
  could not see it, and all four guards passed green while covering sixteen of
  seventeen models. A guard that silently answers a smaller question than it
  claims is worse than no guard.

  `conftest` now imports the whole package before walking. Worth noting *why*
  that is safe: S1.4 deliberately made `Settings` construction lazy so importing
  the module has no side effect. Had that been `settings = Settings()` at module
  scope, importing the package tree at collection time would fail on any machine
  without a populated `.env`, CI first among them. A decision taken three steps
  ago for one reason paid for itself here for another.

- **Two protocol signatures disagreed and nothing in the notebook resolved it.**
  `upsert` takes `Sequence[StoredAssertion]`, `search` takes a precomputed
  `embedding`, and after S1.6 `StoredAssertion` has no embedding field — so
  where does the write-side vector come from? I had left this open yesterday.
  The answer is in §0.4's data table: the owner module for embeddings is
  `memory/vector/pgvector_store.py`, and `GM_EMBED_MODEL` is introduced at S3.2,
  which *is* that store. So the store embeds on write, and `search` takes a
  vector because the read path fuses dense with BM25 and a graph expansion and
  holds one query embedding across all three. Recomputing it inside this store
  would embed the same query up to three times per recall. Written onto the
  protocol, because a contract that leaves this unstated cannot be implemented
  correctly by accident.

- **`@runtime_checkable` is a trap and I nearly reached for it.** It would make
  `isinstance(store, VectorStore)` work — by comparing method *names* only. Not
  signatures, not arity, not whether they are async. A gate that passes for any
  object with a `search` attribute is worse than no gate, because it reads like
  a guarantee. The conformance check is three typed bindings at the bottom of
  `fakes.py` and `mypy --strict`, which checks all of it.

- **Protocol bodies are uncovered statements.** `...` never executes, because
  nothing calls a Protocol — so the coverage number would have started drifting
  down for a reason that has nothing to do with tested code. Excluded a bare
  ellipsis, narrowly, rather than lowering a floor or learning to ignore a
  number.

- **I tested the fakes, which the step does not ask for.** Three of their
  behaviours are contracts rather than conveniences: `search` hides tombstoned
  and invisible rows (I6), `supersede` retires rather than deletes, and `upsert`
  is idempotent by id because S3.3's relay replays. Every unit test for the next
  four days runs on these, so a fake that permits what pgvector forbids makes
  the entire week-1 suite a measurement of the wrong system — and it would do so
  with every test green, which is the expensive kind of wrong.

**END OF DAY 1 CHECK**

- `make lint`, `make typecheck`, `make test` — all green. 108 tests, 100%
  coverage, branches included.
- Schemas and protocols importable; 26 names exported from `guardmem_core.schemas`.
- Docker stack up and **healthy on all four services**, but see below.

**Still open**

- **The port conflict finally bit, and it cost the default stack.** `make dev`
  failed on `Bind for 0.0.0.0:7474 failed` — the `hgem_postgres`, `hgem_redis`
  and `hgem_neo4j` containers from the older `Human-Gated-External-Memory-HGEM`
  checkout hold all four documented ports, and they have been up nine hours.
  Worse, compose *recreated* our three containers before failing, so the stack
  was left part-down rather than untouched. Brought up healthy on 5433 / 6380 /
  7475 / 7688 via the overrides the compose file was written for, which proves
  the stack itself is fine. **It is running on those ports now, and `.env` still
  says 5432 / 6379 / 7687** — harmless today because nothing connects until
  S3.1, and a trap on the day something does. Either stop the old containers and
  re-run `make dev`, or update `.env`. Not mine to decide: those containers
  belong to another checkout.
- Coverage still 100% against `fail_under = 85`; the raise to 90 is S7.2.
- `GM_ANTHROPIC_API_KEY` blank; S2.2 is the first step that needs it.
- Branch protection on `main` (S0.3). `main` is now the trunk and the two merged
  branches are deleted, so this is the moment it matters.

**Tomorrow's first step**

`S2.1` — the noise filter, and `MEMORY_ENGINE.md` §1.1 is emphatic that
everything dropped is *counted* and sampled into the funnel: "you must be able
to see what the filter is eating." Rules for the cheap 70%, FAST-tier classifier
for the rest. `ExtractionResult.dropped_noise` already exists to receive it.

---

## 2026-09-12 — Day 2 · S2.1 (the Layer-1 noise filter)

**Shipped**

- `pipeline/l1_extract/noise_filter.py` and `noise_rules.py` — the five drop
  classes of `MEMORY_ENGINE.md` §1.1. Rules settle the cheap majority; the
  ambiguous remainder goes to a FAST classifier in one batched call.
- `schemas/turn.py` — `Turn`, `TurnRole`, `NoiseReason`, `DecidedBy`,
  `DroppedTurn`, `NoiseResult`; `TurnId` in `types.py`.
- `prompts/loader.py` + `prompts/classify_noise/v1.md` — versioned prompts with
  frontmatter, which `RULES.md` §3 requires of the first step that calls a model.
- `tests/fixtures/noise_corpus.py` — the forty hand-labelled turns.
- 98 new tests — 206 total, **100% coverage across 26 modules**, branches
  included. Measured on the corpus: 17 rule drops, precision **1.000**, and 30
  of 40 turns settled without a model call.

**What broke / what I learned**

- **The step's own snippet uses two types that exist nowhere.** `Turn` and
  `NoiseResult` are not in `MEMORY_ENGINE.md` §0, not in `PROJECT_TREE.md`, not
  in any earlier step. What settles their shape is one sentence in §1.1 —
  *"Everything dropped is counted and sampled into the dashboard funnel: you
  must be able to see what the filter is eating"* — which a `dropped` list of
  bare turns cannot satisfy, and neither can this step's own DONE WHEN. So a
  drop carries its class and the tier that decided it. Those are different bugs
  with different fixes: an over-eager rule is a lexicon edit, an over-eager
  classifier is a prompt change and an eval run, and a funnel that cannot tell
  them apart sends the reader to the wrong file.

- **Two of the five classes cannot be decided yet, and the honest move was to
  say so rather than approximate.** §1.1 defines a restatement at cosine ≥ 0.93
  and a third-party claim by the absence of an ontology licence. The embedder is
  S3.2 and the ontology is S3.5. I nearly reached for `rapidfuzz` at ratio 93 as
  a stand-in for the cosine — it is already a dependency, it is one line, and it
  is exactly the lexical-for-semantic substitution §3.1 spends a paragraph
  warning against. What went in instead: the rule fires only on an *exact* echo
  after normalisation, which is sound because equality is cosine 1.0 and because
  the first copy is always kept; everything between exact and unrelated goes to
  the classifier, which is the thing that can actually judge meaning.
  `rapidfuzz` survives as *routing* triage only — it decides whether to spend a
  model call, never whether to drop a turn. Third-party drops nothing at the
  rule tier at all.

- **"Fail closed" does not mean what it usually means in this module.**
  `RULES.md` non-negotiable #3 resolves every failure in the decision path to
  `HITL_REVIEW` or `REJECT`, and Layer 1 has neither. Closed here means
  **keeping**: a kept turn stays inside governance where the matrix can still
  refuse it, and dropping is the only irreversible thing this code does. That
  one observation decided five behaviours — unparseable reply, verdict for a
  turn never sent, turn answered twice, drop with no reason, turn never answered
  for — and all five are now tests.

  The exception is a provider failure, which propagates untouched. Catching it
  would defer an identical failure by two steps (the extractor needs the same
  provider) while hiding which stage first saw it, and `ARCHITECTURE.md` §4
  already says what a dead provider does: the proposal parks.

- **The step calls a model, so `RULES.md` §3 arrived a step early.** The notebook
  introduces `render()` at S2.2. But §3 forbids f-string prompts in business
  logic, and this is the first step with a prompt — so the loader lands here. I
  did not pull in `pyyaml` for five scalar keys; the frontmatter parser is flat
  and **refuses** what it cannot represent exactly rather than guessing, and
  `pyyaml` stays out of the manifest until S3.5 reads real YAML.

  Two things that only showed up by running it. `GMModel` is `strict=True`, so
  validating a frontmatter dict rejects `"1"` for an `int` and `"fast"` for a
  `Tier` — every value in a text file is text. Routing through JSON is the escape
  hatch `schemas/base.py` already documented at S1.6, and it pays for itself
  twice: `extra="forbid"` then reaches the frontmatter, so a misspelled key is a
  load error instead of a silently ignored line. And the `.md` files had to be
  confirmed present in the built wheel — the same class of bug as the missing
  `py.typed` at S1.5, and invisible to an editable install.

- **The prompt's declared tier should be the thing that routes the call.** It was
  a hard-coded `Tier.FAST` in my first pass, which makes the frontmatter
  decoration. `filter_noise` now passes `prompt.spec.tier` straight through, so a
  prompt authored for FAST cannot be put on FRONTIER by an edit at the call site
  — and there is a test that fails if the file changes.

- **My own drift guard failed again, and again it was right to.** S1.6's property
  suite asserts that 500 examples over `ANY_SCHEMA` actually produced every
  model. Adding seven models pushed two *existing* ones below the sampling floor,
  because `st.one_of` weights branches by the entropy each consumes — so
  per-model depth falls every time the layer grows. Raising the example count
  would have bought one step of silence and returned at S2.2. The union still
  supplies depth; a short second pass over `EVERY_SCHEMA`, a tuple of every
  strategy so one example is one draw of each, supplies breadth. The guard is now
  satisfied by construction and keeps the job it can still fail at: catching a
  strategy that produces nothing.

- **The corpus has to be labelled before the rules are run, and it has to contain
  things the rules miss.** Mine holds three — two third-party claims and one
  imperative whose verb is outside the lexicon. A corpus containing only what the
  implementation already handles measures the implementation against itself. The
  flip side is that precision on drops is free for a filter that drops nothing,
  so the gate asserts a recall floor beside it.

  One judgement call worth recording: two words went into the imperative lexicon
  *because* of a corpus miss ("ignore what **I just** said"). That is the line
  between a lexicon improvement and overfitting, and they went in because they
  are anaphora in any imperative's object, not because one turn needed them.
  Nothing else was tuned to the corpus.

- **Bare "no" is not filler, and that took a second pass to see.** The obvious
  ephemeral lexicon contains yes/no/right/correct. In an intake transcript "No."
  is the answer to "any allergies?", and dropping it while keeping the question
  destroys the answer. They are out of the lexicon entirely and reach the
  classifier, which can see the turns around them.

**Also fixed, found while checking claims**

- Three `RULES.md` §2.4 limits were breached by my own changes and paid down in
  the same commit: the lexicons pushed `noise_rules.py` past the 400-line module
  cap, `filter_noise` past the 50-line function cap, and
  `tests/fixtures/strategies.py` past 400 as well.
- `CONTRIBUTING.md` still opened "Status: pre-implementation … `guardmem_core` is
  still an empty package". Four steps stale; both READMEs had been corrected and
  this one was missed.

**Still open**

- **`tests/fixtures/strategies.py` is 399 lines against a 400 cap.** It grows by
  one block per schema, so S2.2 will breach it. It wants splitting per schema
  module; that is a mechanical refactor and does not belong in this commit.
- The rule tier misses three of the corpus's twenty labelled drops by design. Two
  need the ontology (S3.5); the third ("let me know when…") is a genuinely
  ambiguous request that also mentions a real referral.
- Coverage still 100% against `fail_under = 85`; the raise to 90 is S7.2.
- `GM_ANTHROPIC_API_KEY` is still blank, and **S2.2 is the step that needs it** —
  the first one that cannot run offline.
- The dev stack is on 5433 / 6380 / 7475 / 7688 while `.env` says the defaults.
  Harmless until S3.1; a trap the moment anything connects.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S2.2` — K-sample structured extraction. The loader and the canary check it needs
both exist now, so the step reduces to the prompt file, the `ExtractionBatch`
schema, and the K/temperature ladder from `MEMORY_ENGINE.md` §1.2: sample 0 at
temperature 0 is canonical, and the other K−1 at 0.7 exist only to estimate
uncertainty. `ExtractionResult.dropped_noise` is filled from `len(result.dropped)`.

---

## 2026-09-12 — Day 2 · S2.2 (K-sample structured extraction)

**Shipped**

- `pipeline/l1_extract/extractor.py` — `ExtractionContext`, `ExtractionBatch`,
  `extract()`. Two calls when K > 1: canonical at temperature 0, spread at 0.7.
- `pipeline/l1_extract/span_linker.py` — §1.3's rule, exact-match half.
- `prompts/extract_memories/v1.md`.
- `ExtractedFact`, and two new fields on `ExtractionResult`, under **ADR-0006**.
- `tests/unit/test_source_limits.py` — `RULES.md` §2.4's caps, enforced.
- 107 new tests — **366 total, 100% coverage across 30 modules**, branches
  included.

**What broke / what I learned**

- **The step's own snippet cannot produce the canonical sample it describes.**
  `temperature=0.0 if k == 1 else 0.7` draws *every* sample at 0.7, while §1.2
  says sample 0 is drawn at 0 and "the other K-1 exist only to estimate
  uncertainty". `LLMClient.complete` takes one temperature for all `n` samples,
  so the ladder needs two calls. It would have been easy not to notice: every
  result-level assertion I wrote passes under the single-call version. What
  breaks is one step removed — §3.1 drops "a candidate that appears in zero
  clusters containing sample 0's meaning", and with no temperature-0 draw there
  is no sample 0 to be about, only the first of five equally noisy ones. So the
  test asserts the *calls*, not just their results.

- **Two spec gaps in the same object, and the second one is why I wrote an
  ADR rather than a workaround.** `ExtractionResult` as §0 declares it has
  nowhere to put the K samples, so Layer 3 has nothing to cluster; and nowhere
  to put the unsourced drop count, so §1.3's rule — the one the spec calls the
  thing that "kills most confabulated facts" — has an activation count nobody
  can see. That is exactly the failure §1.1 forbids one layer up, in the same
  words. I could have derived the count as `len(samples[0]) - len(candidates)`,
  and it would be true today and quietly false the moment anything else drops a
  fact. A count should be counted.

  Writing ADR-0006 took twenty minutes and forced me to write down the three
  alternatives I had already half-rejected. Two of them were worse than I
  thought: re-extracting at S5.1 costs K more calls *and* breaks replay, because
  the samples would differ every run.

- **A short sample count is the most dangerous thing a provider can do to this
  pipeline, and the tolerant handling is the wrong one.** If a provider returns
  three samples when asked for five, the obvious move is to use what came back.
  Follow it through: K collapses toward 1, §3.1 sets `H_norm := 0` at K=1, and
  zero entropy is *maximum* confidence on that term. A degraded provider would
  make the system more confident, not less — and `ARCHITECTURE.md` §0 says
  "degradation never widens the auto-write path" in as many words. So it raises,
  retryably, and the proposal parks. I nearly wrote the tolerant version first.

- **`span_linker.py` had to land a step early, and the reason is a type.**
  `extract` returns `ExtractionResult`, which carries `MemoryCandidate`s, which
  require a `Provenance`, which has no valid state without a span. There is no
  version of S2.2 that defers span linking to S2.3. The exact-match half lands
  here and S2.3 adds the fuzzy fallback to the same function — which is a
  smaller change than moving code between modules would have been.

- **Eleven parameters is what the naive signature costs, and the length cap is
  what made me look at it.** `extract` needed content, llm, ontology, k, tier,
  tenant, namespace, trace, source tier, captured_at, dropped_noise. I was about
  to accept that. `RULES.md` §2.4 would not let the function fit, and the fix
  that made it fit was also the right design: five of those eleven share a
  property — each is something the model must never be in a position to assert —
  and grouping them into `ExtractionContext` makes that property visible instead
  of implied. A hallucinated `tenant_id` is a tenant-isolation bug; a
  hallucinated `source_tier` lifts the cap `RULES.md` §4 puts on auto-writable
  impact. The size limit was pointing at a design smell, which is what those
  limits are for.

- **Then the limit turned out to be ambiguous, and I had been enforcing the
  wrong reading for two steps.** Measured from `def`, `extract` could not carry
  the docstring §8 requires — seven parameters and five raise conditions do not
  fit in 50 lines. I was about to add a wrapper function whose only purpose was
  to hold a docstring, which is the point at which a rule is being gamed rather
  than followed.

  The evidence that settled it was already in the tree: `llm/base.py::complete`
  sits at exactly 50 lines measured from `def` and its body is the single token
  `...`. Under the strict reading the repository's own reference Protocol is at
  the cap while containing no logic at all — so the strict reading makes §2.4 a
  docstring-length limit, not the complexity signal it sits beside `C901` to be.
  RULES §2.4 now says which it is.

  And I stopped checking it by hand. §0 of that document is unambiguous — "if a
  rule below isn't checkable by a linter, a test, or a CODEOWNERS review gate,
  it's a suggestion" — and I had been pasting an AST script into a terminal once
  per step, which is how the `strategies.py` breach went unnoticed until it was
  predicted. `tests/unit/test_source_limits.py` runs it now, and it caught both
  real breaches immediately.

- **Both drift guards fired again, and both were right.** The property suite's
  registry check caught `ExtractedFact`, `ExtractionBatch` and then
  `ExtractionContext` the moment each appeared without a strategy. That guard
  has now paid for itself at every step since S1.7.

- **A test I wrote turned out to be unreachable, and the type system was why.**
  I parametrised the short-sample refusal over "zero samples" and "one sample";
  zero is not expressible, because `LLMResponse.samples` is `min_length=1`. The
  only reachable short count is a partial one. Deleted rather than worked
  around — a test for a state the types forbid is a test of the test.

**Also fixed, found while checking claims**

- `tests/fixtures/strategies.py` breached the 400-line cap exactly where
  yesterday's log said it would, at the next step that added a schema. Split
  into `strategy_primitives.py` (the vocabulary) and `strategies.py` (the
  builders and the registry).
- `tests/unit/test_extractor.py` reached 473 lines; split into the path where
  nothing goes wrong and `test_extractor_refusals.py`, with the shared
  scaffolding in `tests/fixtures/extraction.py`.

**Still open**

- **Temporal extraction is not implemented and it is a stated gap, not an
  oversight.** `ExtractedFact` has no `valid_from` / `valid_to`, because the v1
  prompt does not ask for them and a field the prompt never fills is a field
  that is always `None`. §2.2(c)'s temporal-overlap check is the first real
  consumer, so a v2 prompt belongs at **S4.3** — and a prompt change is a
  semver-minor change that triggers the nightly eval gate (`RULES.md` §3).
- `ExtractionResult.dropped_noise` arrives as an argument to `extract`. That is
  the honest option of the two available, but it is a seam the orchestrator has
  to remember; **S5.6** is where it gets wired, and where a forgotten count
  would silently read as zero.
- `GM_ANTHROPIC_API_KEY` is still blank. S2.2 runs entirely on fakes, so the
  first step that genuinely needs it is now the first live extraction run — but
  nothing in the suite will tell me it is missing until then.
- Coverage still 100% against `fail_under = 85`; the raise to 90 is S7.2.
- The dev stack is on 5433 / 6380 / 7475 / 7688 while `.env` says the defaults.
  Harmless until S3.1; a trap the moment anything connects.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S2.3` — the span linker's fuzzy fallback. `link_span` already exists and its
signature does not change; what is left is `rapidfuzz.fuzz.partial_ratio_alignment`
with score ≥ 92, and the property test invariant I1 rests on: for any candidate
with no matching substring, the pipeline emits `REJECT(UNSOURCED)` and never a
stored assertion. `ExtractionResult.dropped_unsourced` already counts what the
exact matcher rejects, so the fallback's effect will be visible as that number
falling.

---

## 2026-09-12 — Day 2 · S2.3 (span linker, fuzzy fallback) · END OF DAY 2

**Shipped**

- `span_linker.py` — `SpanMatch`, exact match then `partial_ratio_alignment` at
  ≥ 92, with the span snapped to whole words.
- `Provenance.alignment`, and `verbatim` redefined as the source text, under
  **ADR-0007**.
- `tests/property/test_i1_sourced_writes.py` — invariant I1, 500 examples.
- `tests/unit/test_layer1_end_to_end.py` — the END OF DAY 2 CHECK as a test.
- 33 new tests — **399 total, 100% coverage across 31 modules**, branches
  included. **Layer 1 is complete**: raw turns in, sourced candidates out.

**What broke / what I learned**

- **The property test found a real bug on its first run, and the hand-written
  test for the same thing had been passing for the wrong reason.** A `verbatim`
  of `" "` is a substring of almost any source, so the *exact* pass found it and
  returned a one-character span quoting whitespace — non-empty, so `Provenance`
  accepted it, and a citation of nothing, which `RULES.md` §1.1 treats exactly
  as no span at all. The fuzzy path had already refused it inside `_snap`, so
  the two halves of one function disagreed about the same input.

  My unit test for this used *three* spaces. Three spaces are not a substring of
  the test source, so it took the fuzzy path and passed — testing the branch
  that was already correct. That is the sharpest argument for property testing I
  have hit on this project: I wrote the case, I believed it covered the rule,
  and it covered the other half of the function.

- **The aligner returns a span, not a quote.** `allergic to penicilin` scores
  95.24 and the raw span is `allergic to penicilli` — truncated mid-word.
  `allergic to penicillin.` returns a span with a trailing space. The reviewer's
  highlight is rendered from `source_span` and half a word is not something a
  human can act on, so spans are snapped to whole words. Widening is safe in the
  one direction that matters: the result still contains the matched region, so it
  can quote more of the sentence but never turn a false citation into a true one.

- **Fuzzy matching forced a decision the exact version never had to make: when
  the claim and the source differ, which one is `verbatim`?** Its own docstring
  had already answered — "what the reviewer reads and what NLI compares against,
  so it is the text itself and never a paraphrase" — so the source text wins.
  But that destroys the information §3.2 needs: the fuzzy-match penalty is a
  function of "span alignment", and once `verbatim` *is* the source text,
  comparing the two returns 1.0 by construction. So the alignment has to be
  measured here and stored, or the penalty is unimplementable at S5.2. ADR-0007.

  Second ADR in two steps, which gave me pause. Both were forced the same way:
  a downstream clause the spec itself states, needing a value nothing carries.
  The alternative each time was to silently not implement the clause.

- **I nearly added a minimum-length guard on fuzzy matches, and the data said
  not to.** A three-character claim matching at 92 sounds alarming. Measured:
  one wrong character in a three-character needle scores 67 (`PCQ` against a
  source containing `PCP`), and `hivez` against `hives` scores 80. The threshold
  is self-limiting on short strings. An unspecified extra rule would have been a
  guess dressed as caution, and I would have written a paragraph defending it.

- **An unreachable guard came back, and I removed it rather than testing it.**
  `_snap` returned `None` when it had matched only whitespace. Coverage flagged
  the branch; a brute-force search over 60,000 mutated inputs never produced one.
  The argument is sound rather than statistical: the aligner's score *is* the
  similarity between needle and window, a non-blank needle scores 0 against a
  blank window, and a blank needle is refused before the call. So the two
  conditions cannot hold together. `_snap` is total now, and the invariant it
  was guarding is asserted over 500 generated examples instead. Same lesson as
  the `_near_duplicate` guard at S2.1.

- **The other two uncovered branches were reachable, and brute force is how I
  found the inputs.** Trailing whitespace in the raw span and a span starting
  mid-word both happen, but not on any example I thought to write — 60,000
  random mutations of the source produced `'e the CVS on Elm Strfet '` for the
  first. Both are tests now, with the inputs recorded. Guessing at adversarial
  inputs for a similarity metric is not something intuition is good at.

- **Two rejections cost recall and I left them alone.** A case-different quote
  scores 86.36 and a doubled-whitespace quote 91.67; both are harmless quoting
  differences and both fall under §1.3's 92. Nudging the threshold to catch them
  would be tuning a spec number to make two cases pass, and Checkpoint B's own
  diagnosis says to *tighten* it if `S_src` underperforms, not loosen it. Both
  are recorded as measurements in the test file so the Checkpoint B conversation
  has them.

**END OF DAY 2 CHECK**

Raw text in, candidates out, each with a span, K samples retained — and it is a
test rather than a look, because composing the two stages is what makes the seam
between them visible. `tests/unit/test_layer1_end_to_end.py` asserts the thing
that is easiest to get wrong later: **the document the spans index into is the
denoised one**, not the transcript. A caller that joins the kept turns one way
here and another way in S5.6 puts every stored span a few characters off — onto
real text, which is precisely why it would not look like a bug. `dropped_noise`
crosses the same seam and reads as zero if a caller forgets it.

- `make lint`, `make typecheck`, `make test` — all green. 399 tests, 100%
  coverage, branches included.
- Day 2 complete: S2.1, S2.2, S2.3.

**Still open**

- **Temporal extraction is still not implemented**, by decision rather than
  oversight: `ExtractedFact` has no `valid_from` / `valid_to` because the v1
  prompt does not ask for them. §2.2(c)'s temporal-overlap check is the first
  real consumer, so a v2 prompt belongs at **S4.3**.
- `ExtractionResult.dropped_noise` and the denoised-document join both cross the
  filter/extractor seam by convention. **S5.6** is where the orchestrator makes
  them structural; until then `test_layer1_end_to_end.py` is the only thing
  holding the shape.
- Coverage still 100% against `fail_under = 85`; the raise to 90 is S7.2.
- `GM_ANTHROPIC_API_KEY` is still blank. All of Layer 1 runs on fakes, so
  nothing in the suite reports it missing — the first live run will.
- The dev stack is on 5433 / 6380 / 7475 / 7688 while `.env` says the defaults.
  **S3.1 is the step that connects**, so this stops being harmless tomorrow.
- Branch protection on `main` (S0.3).

**Tomorrow's first step**

`S3.1` — the initial Alembic migration: `tenant`, `entity`, `assertion`,
`audit_event`, `outbox`, `review_task`, `policy_version`, with the bitemporal
columns, the RLS policy and `REVOKE DELETE` that `RULES.md` non-negotiable #2
depends on. First thing to settle before writing any SQL is the port question
above — and `Provenance` is now a list on `StoredAssertion` with an `alignment`
per entry, which the table shape has to account for.

---

## 2026-09-13 — Debug pass: red CI, a quadratic filter, and two guards that were not guarding

Not a build step. A review of everything through S2.3, prompted by CI having
been red since S2.1 while every local run was green.

**Shipped**

- CI diagnosed and fixed: six dev dependencies mirrored into `pyproject.toml`'s
  dev group, `uv.lock` regenerated.
- `test_dependency_consistency.py` rewritten — symmetric mirror check, PEP 508
  marker evaluation, TOML parsing instead of regex.
- `TurnHistory` — the noise filter is no longer quadratic. **159 ms → 4.4 ms at
  400 turns.**
- `render()` path-traversal guard; `_tokens` and `TurnHistory.__len__` deleted.
- 409 tests, 100% branch coverage, all four gates verified **in a
  CI-equivalent environment** rather than only in mine.

**What broke / what I learned**

- **CI was red for six commits and I did not look.** Every one of those commits
  ended with me reporting "all gates green" — and they were, locally. The badge
  was in the README the whole time. That is the lesson worth keeping: *"the gates
  pass on my machine" and "the gates pass" are different claims*, and I had been
  making the second while checking the first.

- **The cause was the environment, not the code, and the two installs were never
  the same.** CI runs `uv sync --locked --dev`, which resolves **`uv.lock`
  only** — 96 packages. A developer runs `uv pip install -r
  requirements.lock.txt` — 313. `types-pyyaml` was pinned in
  `requirements/dev.txt` and not in the dev group, so it existed in the second
  set and not the first. `mypy --strict` needs it for
  `test_compose_stack.py`'s `import yaml`, and S1.7 is the step that widened
  `make typecheck` to cover `tests/`. That is the exact commit CI went red on.

  Five more had drifted identically. `pyproject.toml` already said the dev group
  "carries EXACT pins mirroring requirements/dev.txt"; the word was *mirroring*
  and the reality was a subset.

- **The guard for this existed and checked the wrong direction.** It asserted
  that dev-group entries appear in `requirements/dev.txt` — the direction where
  the consequence is a missing citation. The direction that breaks CI is a
  package the developer has and CI does not. One `assert` was missing, and the
  test's name said "matches" while it meant "is contained in".

- **Then fixing it produced a false positive, which is how guards die.** With
  the six added, the `uv.lock ⊆ requirements.lock.txt` check failed on
  `pyyaml-ft`. That is not drift: `uv.lock` is a **universal** lock and carries
  entries for every interpreter its markers cover, so `libcst` requires
  `pyyaml-ft` under `python_full_version == '3.13.*'` — which a 3.12 project can
  never install. `requirements.lock.txt` is resolved for one interpreter and
  rightly omits it. The temptation was to allowlist the package name. What the
  guard needed was to evaluate the markers, which it now does, against the
  interpreter `.python-version` names rather than the one running the test.

  While in there I replaced the regex that matched `name = "..."` followed by
  `version = "..."` with `tomllib`. `uv.lock` is TOML. The regex was true of the
  file rather than of the format.

- **I verified the fix by reproducing CI instead of reasoning about it.**
  `UV_PROJECT_ENVIRONMENT=<scratch> uv sync --locked --dev` builds CI's exact
  96-package environment without touching the 313-package one I work in. Then,
  to be sure the diagnosis was the cause and not a coincidence, I uninstalled
  the stubs from that scratch venv and confirmed the *exact* CI error came back:
  `Library stubs not installed for "yaml"`, `test_compose_stack.py:33`. I had
  nearly skipped that step, and the first attempt at it was wrong — `uv pip
  uninstall` did not target the scratch venv and mypy's incremental cache hid
  the result, so it "passed" and briefly convinced me the diagnosis was wrong.

- **The noise filter was quadratic and nobody would have noticed until it
  mattered.** Profiling Layer 1 at realistic sizes: 50 turns 2.8 ms, 400 turns
  159 ms. Eight times the input, fifty-seven times the time. The cause was not
  the fuzzy comparison, which is inherently per-pair — it was that both
  backward-looking rules took `Sequence[Turn]` and re-derived `normalise()` for
  every earlier turn on every turn. **5,350 calls for 100 turns where linear is
  200.**

  `TurnHistory` normalises once on `add`, and keeps a `set` beside the list so
  the exact-match half is O(1) rather than a scan. 400 turns: 4.4 ms. 1,000
  turns: 10.8 ms. This product's entire premise is conversations that run for
  months; a curve like that is not a micro-optimisation, it is the difference
  between the filter being cheap and the filter being the reason the eval path
  misses its SLA.

  The regression test counts `normalise` calls rather than timing anything. A
  wall-clock assertion on a shared CI runner measures the runner, which is the
  same reason `RULES.md` §5 bans `sleep` in tests.

- **Coverage found the dead code the refactor created**, which is the argument
  for a 100% floor stated better than I could state it: `_tokens` had no callers
  left because both had been given the normalised string directly, and
  `TurnHistory.__len__` was API I wrote because objects usually have one.

- **`render()` would follow `../../..` out of the package.** Nothing exploits it
  today — `name` is a module constant at both call sites — but it is a public
  function of a library, and the only thing preventing a traversal was that no
  `v1.md` sat at the far end. That is a property of the filesystem, not of the
  code.

**Deliberately not fixed**

- **The untrusted-content delimiter can be escaped.** `content` goes between
  `<untrusted_content>` tags and nothing stops the content from closing them.
  The canary catches an echo, not an escape. The obvious fix — sanitise
  `content` — is *wrong*: every `source_span` indexes into exactly that string
  and `source_hash` is its digest, so rewriting it silently invalidates the
  provenance of every candidate produced from it. The real answer is the
  pre-flight detector at **S11.1**, which `ARCHITECTURE.md` §2 puts before any
  model sees the text and which quarantines rather than rewrites. Recorded in
  the extractor's docstring so S11.1 inherits it knowingly.
- **CI runs only on `ubuntu-latest`** while all development happens on Windows.
  Every Windows-specific hazard the repo has hit — `PYTHONIOENCODING`, cp1252
  in `.env`, cmd.exe in the Makefile, separators in `.secrets.baseline` — is
  currently caught by a human, not by a runner. A matrix would close that, at
  double the CI minutes. Worth a decision, not worth making unasked.

**Still open**

- **The dev stack is on 5433 / 6380 / 7475 / 7688 while `.env` says the
  defaults, and S3.1 is the step that connects.** Docker Desktop was not running
  during this pass, so the S1.3 stack could not be re-verified. This is the one
  thing that must be settled before the next step, and it is not mine to settle:
  the containers holding the default ports belong to another checkout.
- Temporal extraction still absent by decision (S4.3); the two Layer-1 seams
  still held by convention until S5.6's orchestrator.
- Coverage 100% against `fail_under = 85`; raise to 90 at S7.2.
- `GM_ANTHROPIC_API_KEY` blank — nothing needs it until a live run.
- Branch protection on `main` (S0.3).

**Next step**

`S3.1` — the initial Alembic migration. Settle the port question first, then
`tenant`, `entity`, `assertion`, `audit_event`, `outbox`, `review_task`,
`policy_version`, with the bitemporal columns, the RLS policy and the `REVOKE
DELETE` that `RULES.md` non-negotiable #2 rests on. Note that `Provenance` now
carries `alignment` (ADR-0007) and is a *list* on `StoredAssertion`, so the
table shape has to account for both.

---

## 2026-09-13 — Day 3 · S3.1 (initial migration, bitemporal assertions, RLS)

**Shipped**

- `0001_initial` — eight tables, bitemporal columns, two partial retrieval
  indexes, RLS on every tenant-scoped table, and the grants that hold
  `RULES.md`'s non-negotiables.
- `infra/docker/initdb/02-app-role.sql`, `alembic.ini`, `env.py`.
- `tests/integration/test_migration_invariants.py` — 15 tests.
- `.env` moved to the shifted ports; cold start verified from an empty volume.
- 428 tests, 2 skipped, 100% branch coverage.

**What broke / what I learned**

- **Four bugs in my own SQL, and three of them would have passed review.** They
  were found by running the constraints, not by reading them, which is the
  argument for making a DONE WHEN a test rather than a paragraph.

  `CHECK (lower(span) >= 0 AND upper(span) > lower(span))` reads as airtight.
  `int4range(5, 5)` is an **empty** range, `lower()` and `upper()` return NULL
  on one, and **a CHECK that evaluates to NULL passes**. So the one thing the
  constraint existed to reject - a span that cites nothing, which `Provenance`
  refuses in Python and §1.1 calls no span at all - went straight in.

  `ENABLE ROW LEVEL SECURITY` does not apply to the table's owner. The migration
  runs as the owner. So the natural way to convince yourself isolation works -
  connect, SELECT, see only your tenant - would have shown every tenant's rows
  and looked like a pass. `FORCE ROW LEVEL SECURITY` is the other half.

  `current_setting('app.tenant_id', true)` returns NULL when the variable was
  never set, which is the case everyone tests. A session that sets it and then
  `RESET`s it reads back the **empty string**, and `''::uuid` raises. Tenant
  isolation surfaced as a 500 instead of as zero rows.

- **The third of those forced a design decision I had not noticed I was
  making.** Fixing the empty string with `NULLIF` raised the question of what a
  *malformed* tenant id should do. Swallowing everything uncastable into "no
  rows" is tempting and wrong: in a product whose subject is remembered facts, a
  bug in tenant propagation would then present as "this patient has no
  memories". Unset is silence; malformed is an error. Both are fail-closed, only
  one is diagnosable.

- **The step's REVOKE needs a role the stack does not create.** `guardmem` owns
  the tables, and an owner is not subject to its own grants - so revoking DELETE
  from it protects nothing at all. The app role belongs to the deployment
  (initdb here, Terraform in production) and the grants belong to the table, so
  the migration raises if the role is missing rather than skipping the revoke. A
  migration that silently does not apply a P0 property is worse than one that
  fails, because failing is the only signal anyone gets.

- **Provenance had to become its own table, and the docs had already said so.**
  `schemas/entity.py` recorded at S1.6 that the domain model holds a list and
  that S3.1 would decide the storage. Following `ARCHITECTURE.md` §5 literally
  would have meant S3.2 splitting the table one step later - precisely the
  retrofit this step's WHY warns about. The cost is that §1.1's `NOT NULL` has
  no column to live on any more, which is why the deferred constraint trigger
  exists. Deferred, because assertion and citations are written in one
  transaction; a plain trigger would fire before the citations were there.

- **I reversed one of my own decisions on evidence, mid-step.** I extended the
  size gate to `infra/` and exempted alembic revisions from the *module* cap
  only, writing that the function cap "still applies: that one is about
  complexity". Then `_belief_tables` failed it at 59 lines - because two
  `CREATE TABLE` statements are 59 lines. Its cyclomatic complexity is 1. Line
  count is not complexity, which is exactly why `C901` exists as a separate
  rule, and `C901` still covers migrations. The exemption now covers both caps
  and says why it changed.

- **`asyncpg` has no `py.typed`, which is the `types-pyyaml` failure again.**
  This time the gate caught it in the same minute rather than six commits later.
  The real fix is `asyncpg-stubs`, and I did not take it: adding one stub means
  regenerating `requirements.lock.txt`, and `uv pip compile` today moves
  seventeen unrelated packages forward - botocore, huggingface-hub,
  arize-phoenix, tqdm. That is a dependency bump that deserves its own commit
  and its own test run, not a passenger on a migration. A justified per-module
  override holds the line until then.

**DONE WHEN**

- `make migrate` runs clean, from a destroyed volume: `make dev-reset && make
  dev && make migrate` takes an empty disk to a migrated schema with no manual
  step, which also proves `initdb` still does its half.
- `\d assertion` shows `valid_from` / `valid_to` / `recorded_at` /
  `retracted_at` / `superseded_by` / `embedding`.
- A DELETE as `guardmem_app` raises `permission denied for table assertion`,
  while the retirement path - `UPDATE ... SET valid_to = now()` - succeeds.

All three are assertions in `tests/integration/`, along with the append-only
audit chain, the unsourced-write trigger, the empty-span constraint, and four
tenant-isolation cases.

**Still open**

- **The dev stack needs its port overrides on every `make dev`.** The old
  checkout's containers hold 5432 / 6379 / 7687 and restart with Docker, so the
  incantation is `POSTGRES_PORT=5433 REDIS_PORT=6380 NEO4J_HTTP_PORT=7475
  NEO4J_BOLT_PORT=7688 make dev`. `.env` now matches those. A `--env-file` for
  compose would make it durable; that is a decision about a machine-local quirk,
  not a project default.
- `requirements.lock.txt` is behind what `uv pip compile` resolves today by
  seventeen packages. Refreshing it is a deliberate change with its own commit.
- Temporal extraction (S4.3), the two Layer-1 seams (S5.6), coverage to 90%
  (S7.2), branch protection on `main` (S0.3).

**Tomorrow's first step**

`S3.2` — the pgvector store. It is the step that puts `sqlalchemy`, `asyncpg`
and `pgvector` into `packages/guardmem-core/pyproject.toml` (they are on the
workspace root today, for the migration), and the step where `Any` from an
untyped asyncpg stops being acceptable. Its own DONE WHEN is a testcontainers
integration test: write, search, supersede, with the superseded row absent from
search and present in a point-in-time query. `StoredAssertion.provenance` is a
list, so `upsert` writes the provenance rows too - inside the same transaction,
or the deferred trigger rejects the assertion at COMMIT.

---

## 2026-09-13 — Day 3 · S3.2 (pgvector store, testcontainers, CI integration job)

**Shipped**

- `memory/vector/pgvector_store.py` — `VectorStore` over the S3.1 schema, with
  `pool.py` and `rowmap.py` beside it. One store per tenant; every statement
  inside a transaction carrying `SET LOCAL app.tenant_id`.
- `as_of` added to `VectorStore.search`, and an `Embedder` protocol added to
  `memory/vector/base.py`. Both are protocol changes the step needed and did not
  mention.
- Testcontainers, at last: `tests/fixtures/postgres.py` starts the pinned
  `pgvector` image, mounts the repo's own `initdb/` into it, and runs
  `alembic upgrade head`. The fifteen S3.1 tests stop skipping.
- `ci.yml` gains an `integration` job. **473 tests, 100% coverage** with the
  integration suite included.
- `asyncpg-stubs` added; the `ignore_missing_imports` override deleted.

**What broke / what I learned**

- **The deferral in S3.1 was based on a measurement of the wrong thing.** I
  wrote, in `pyproject.toml` and in the log, that adding `asyncpg-stubs` would
  drag seventeen unrelated packages forward and therefore deserved its own
  commit. Today I ran it: `uv pip compile requirements-dev.txt -o
  requirements.lock.txt` added exactly one package and moved nothing. `uv pip
  compile` honours the pins already present in its output file unless told to
  upgrade; the seventeen came from a resolution *from scratch*. The caution was
  right, the fact was wrong, and the only way to tell which was to run it.

  It paid for itself immediately. With the stubs in place, `mypy --strict`
  caught the first annotation error on the new module: `Pool.acquire()` yields a
  `PoolConnectionProxy[Record]`, not a `Connection[Record]`. Under the override,
  every connection in that file had been `Any`.

- **I shaved a module to fit a line cap, and the cap's own error message told me
  not to.** `pgvector_store.py` came in at 424 lines against a 400 cap, and I
  trimmed prose to 403, then to 401 — at which point what I was doing was
  obvious. The test says "split it along a real seam rather than shaving it".
  There were two seams: `rowmap.py` (the table's shape, in both directions, with
  the SQL that fills it — so adding a column is one file to edit) and `pool.py`
  (process-scoped lifetime, request-scoped store; the constructor for the
  long-lived thing sitting inside the module for the short-lived one invites a
  pool per request). The store is 367 lines and the split made it better, which
  is the argument for having a cap at all.

- **Two `conftest.py` files are one module name too many.** I put the container
  fixtures in `tests/integration/conftest.py`, and `mypy` refused the whole test
  tree: "Duplicate module named conftest". In a package with no `__init__.py`
  there is nothing to disambiguate them. The fix is `pytest_plugins` in the one
  root conftest, which also puts the fixtures next to the other fixtures.

- **I let blind string replacement loose on a 500-line file and it produced
  `_write_andreveal` and `test_..._absent_fromsearch`.** Renaming
  `_reveal` → `reveal` matched inside `_write_and_reveal`; renaming `_search`
  matched inside `from_search`. Then ruff's `--fix` helpfully removed the
  now-undefined import, which hid half of it. Nothing shipped broken — lint and
  mypy caught all of it — but the twenty minutes were self-inflicted, and the
  lesson is that a rename across a file is an AST job or an anchored one, never
  a substring one.

- **`assert True not in row` does not test what it looks like.** I wrote it to
  prove `visible` is not among the INSERT parameters, and it failed — because
  `corroboration_count` is `1` and `1 == True` in Python. The check that
  actually holds is `not any(isinstance(value, bool) for value in row)`. A
  boolean identity trap in a test asserting the absence of a boolean is a
  pleasing shape of bug.

- **Three mutants, three kills.** Green tests prove nothing on their own, so I
  broke the store three ways and checked which tests noticed: ignoring `as_of`
  killed the DONE WHEN test, dropping `visible` from the filter killed the
  invisibility test, and `ON CONFLICT DO UPDATE` killed the replay test. That
  last one is the one I most wanted evidence for — it is the difference between
  a retry and a resurrection.

- **I pushed with every local gate green and the new CI job failed anyway —
  again.** The debug pass three commits ago was entirely about this class of
  mistake, and I made a fresh instance of it: the integration suite applies the
  schema through `alembic`, `env.py` builds `Settings`, and `Settings` has nine
  required fields. The fixture supplies one. The other eight come from `.env`,
  which I have and a fresh runner does not — `8 validation errors for Settings`.

  What is worth keeping is not the fix (`cp .env.example .env`, which is the
  README's own setup line) but that the *shape* of the error repeated. A new
  test job is a new environment, and a new environment is the thing that has to
  be checked rather than assumed. I confirmed both halves locally afterwards by
  moving `.env` aside to reproduce the failure and swapping `.env.example` in to
  prove the fix — which took two minutes and should have come before the push.

- **`ryuk` would not pull on this machine** (registry EOF, twice), so the local
  runs used `TESTCONTAINERS_RYUK_DISABLED=true`. Safe here because the fixture
  stops the container in a `finally`; CI pulls it normally. Worth knowing the
  flag exists before a demo.

**Still open**

- The outbox relay (S3.3) is the only thing that may flip `visible`, and until
  it exists the integration tests stand in for it with an owner `UPDATE`.
- `embed_text` renders `predicate: object`. `verbatim` would very likely
  retrieve better but it is a *list*, so the vector would depend on how many
  sources a fact has — the axis `S_cor` owns. Revisit at S22 with the eval
  harness, which is the first point there is a number to compare.
- `search` with `as_of` cannot use `assertion_hnsw` (partial on
  `valid_to IS NULL AND visible`) and falls back to a sequential scan. Correct
  for now; revisit if point-in-time queries ever become hot.

**Tomorrow's first step**

`S3.3` — outbox and dual-write coordination. It is the component that flips
`visible` true, so it closes the loop this step deliberately left open, and
`upsert`'s `ON CONFLICT DO NOTHING` exists for its replay semantics.

## 2026-09-13 — Day 3 · S3.3 (outbox relay, store router, dual-write coordination)

**Shipped**

- `memory/relay.py` — `OutboxRelay.run_once()`: claim a bounded batch, apply the
  graph side, then `visible = true` and `dispatched_at` in one transaction.
  Nothing else in the system may set that column, which closes the loop S3.2
  left open.
- `memory/outbox.py` — the `outbox` table in both directions, the same seam
  `rowmap.py` draws for `assertion`. `PgVectorStore.upsert` now enqueues the
  event inside the transaction that writes the assertion.
- `memory/router.py` — the write entry point, and the app-layer half of
  `RULES.md` §4's defence in depth.
- `tenant_transaction` moved out of the store into `pool.py`, where the relay
  can share it.
- Four new test modules, 520 tests, 100% coverage with the integration suite.

**What broke / what I learned**

- **The step's WHERE names a service that does not exist, and building it would
  have been the wrong sixty minutes.** `services/worker/tasks/outbox_relay.py`
  needs a workspace package, an `arq` dependency, a Dockerfile and a CI job —
  none of which the DONE WHEN touches. It tests claim, dispatch and complete,
  which is logic, so the logic went into `guardmem-core` behind a `run_once()`
  with no loop in it. The arq binding is four lines and belongs to the step that
  builds the worker. It also kept `RULES.md` §5's "no `sleep`" honest for free:
  there is no loop here to sleep in.

- **"Single Postgres transaction" decided where the enqueue lives, and I had it
  in the router first.** A router that enqueues after the store's write opens a
  *second* transaction, and the gap between them is an assertion that is
  durable, sourced, invisible, and unreleasable by anything — strictly worse
  than a partial write, because nothing retries it. The store already has to own
  one transaction (the deferred provenance trigger), so it owns this too. The
  layering objection is real and the answer is that atomicity across two tables
  is a property of Postgres, not of a protocol.

- **`uuid5` again, for the third time, and I nearly missed it.** I wrote the
  outbox id as `uuid4` and only caught it writing the replay test. `upsert`
  answers `ON CONFLICT DO NOTHING`, so a random event id deduplicates *nothing*:
  the assertion collides on its primary key and does nothing while its event is
  enqueued a second time, and the relay dispatches a write that may already be
  dispatched. Exactly the shape that bit provenance at S3.2. The lesson has
  generalised now — **any row this system writes on a replay path needs a key
  derived from its content**, and "what does a retry do to this table?" belongs
  in the review of every insert, not just the ones that look like queues.

- **I moved the `attempts` increment three times before it was right.** After
  the work: lost on the crash it exists to count. Before the claim: racy. In the
  claim's own statement, committed before the dispatch starts: correct, and it
  is what separates a poison event from one nobody has reached yet.

- **`FORCE ROW LEVEL SECURITY` applies to `DELETE`, which broke cleanup in a way
  that looked like a foreign key bug.** The relay is not tenant-bound, so its
  tests write rows under *both* of the fixture's tenants. I widened
  `_drop_tenant` to `tenant_id = ANY(...)` and the teardown started failing on a
  reference from `entity` — because the owner is subject to its own policies and
  the `DELETE` only reached rows the currently-set tenant made visible. It
  deleted the first tenant's rows, silently skipped the second's, and fell over
  three statements later. The fix is a loop with `set_config` per tenant. The
  error pointed at the constraint; the cause was the policy.

- **Three mutants, three kills.** Completing before the graph write killed both
  DONE WHEN tests; an `upsert` that stops enqueuing killed every relay test; a
  claim that stops counting `attempts` killed the retry and cap tests. The first
  is the one worth having evidence for — it is the difference between a dual
  write and two writes.

- **Where you kill the relay is the entire test.** Dying before the graph write
  proves nothing: nothing happened, and a retry repeats nothing. The only
  interesting window is between the edge landing and the flip, because it is the
  only point where a retry re-applies an effect that already took. Writing the
  double that sits exactly there (`GraphThatDiesAfterWriting`) was the moment
  "exactly once" stopped being a phrase and became at-least-once delivery with
  idempotent effects, which is the only thing a queue with a crashing consumer
  can actually promise.

**Still open**

- `route()` returns "both" and cannot return anything else. §2.4's three-way
  split turns on the predicate's declared type, which is ontology content —
  **S3.5**. Guessing now would guess in the direction that loses data: an
  assertion wrongly routed vector-only writes no edge, so `degree()`
  under-reports and §3.3's blast-radius feature prices a change as safer than it
  is.
- `ARCHITECTURE.md` §4's graph-outage row wants a **`graph_pending` flag** and a
  vector-only write. There is no such column, and adding one without the health
  probe that sets it would be a field nothing fills. Today a graph outage leaves
  the event pending and the assertion invisible, which satisfies the "never drop
  the assertion" half and is stricter than the degraded mode.
- The relay dispatches **sequentially**. `RULES.md` §2.2 wants fan-out through a
  `TaskGroup` bounded by a semaphore; with an in-process graph a batch of fifty
  costs less than the round trip that claimed it. **S7.1** makes the graph a
  network hop and is the step that should add it.
- A capped event stays pending in `outbox` rather than moving to a dead-letter
  table. Find them with
  `SELECT * FROM outbox WHERE dispatched_at IS NULL AND attempts >= 5`.
- Still no audit event on the write path. The §2.4 diagram puts one in the same
  transaction as the assertion and the outbox row; `observability/audit.py` and
  the hash chain arrive at their own step, and a half-built chain verifies
  nothing.

**Tomorrow's first step**

`S3.4` — the NetworkX graph store. The relay already writes through the
`GraphStore` protocol and every test of it runs against a fake, so this is the
step that gives it a real backend and `degree()` its first real caller.

---

## 2026-09-13 — Day 3 · S3.4 (NetworkX graph store)

**Shipped**

- `memory/graph/networkx_store.py` — a `MultiDiGraph` keyed by `assertion_id`,
  the first real `GraphStore`. `degree()` and `neighbors()` over live edges, and
  a single-tenant guard that is enforced rather than described.
- An `import-linter` contract for S3.4's second DONE WHEN clause: the pipeline
  may not import a concrete store or a driver.
- `networkx` added to `guardmem-core`, `types-networkx` to the dev group,
  mirrored into `requirements/` and both locks.
- `tests/unit/test_networkx_graph_store.py` — 37 tests, most of them run twice.
  561 tests, 100% coverage.

**What broke / what I learned**

- **The fake and the real store agreed on the first run, and that is the result
  worth recording.** `fakes.py` has said since S1.7 that a fake permitting what
  a real store forbids makes the whole week-1 unit suite a measurement of the
  wrong system. It was an argument, not a fact, because there was no real
  `GraphStore` to check it against. Parametrising the `store` fixture over both
  turned it into a fact: every unit test written against `FakeGraphStore` since
  S1.7 was measuring something the real backend also does. That took about four
  extra lines and I would not have written them if the docstring had not been
  quite so insistent.

- **Building the first backend found a gap in the protocol, not in the
  backend.** `degree(entity)` and `neighbors(entity)` take no tenant. For
  `PgVectorStore` that was fine — the tenant binds at construction and RLS
  enforces it underneath. Here there is no RLS, one process-wide graph, and no
  argument to filter on, so two tenants in one store would price one's blast
  radius with the other's edges. `PROJECT_TREE.md` already said "dev /
  single-tenant fallback"; what it did not say is what happens if you ignore
  that. Now `upsert_assertion` refuses a subject held for another tenant, and
  the failure mode if someone points the multi-tenant relay at this is the good
  one: the write raises, the event stays pending, the assertion stays invisible,
  and `attempts` climbs to the cap where an operator finds it.

- **The guard has to be on the subject and not the object, which I got wrong
  first.** Guarding both refused a correct write immediately: two tenants
  recording an allergy to penicillin legitimately share the node
  `"penicillin"`, because a literal is a value and not an entity. A subject is
  always a resolved `EntityId` and belongs to exactly one tenant. One line, and
  the test that caught it is the one I nearly did not write.

- **`MultiDiGraph` keyed by `assertion_id` gave me idempotence for free, and
  something better than idempotence.** `add_edge` with an existing key replaces
  the attributes rather than adding a parallel edge — so a replay does not just
  deduplicate, it *refreshes*. A replay after supersession writes the new
  `valid_to` through, and right now that is the only way the graph ever learns a
  fact was retired: `GraphStore` has no `supersede` and §2.3's `SUPERSEDES` edge
  belongs to the step that coordinates it.

- **An ordered set for the BFS frontier, not a `set`.** A plain set makes
  traversal order depend on string hashing, so two runs of the same test can
  return the same edges in a different order — the kind of flake that gets
  diagnosed three steps later and blamed on the wrong thing.
  `dict.fromkeys`-style ordering costs nothing.

- **The lock file rewrites its own header, every time.**
  `uv pip compile` dropped thirty lines of hand-written notes from
  `requirements.lock.txt` — the package count, the spaCy model caveat, the
  reason `ml-local.txt` is excluded. Nothing failed; the notes were simply gone
  from the diff. Pasted back with an S3.4 line added, and the header now says
  that regenerating destroys it.

**Still open**

- **The graph has no supersession path.** `PgVectorStore.supersede` updates
  Postgres and nothing tells the graph, so a retired edge stays live there until
  the outbox happens to replay it. `neighbors()` filters correctly on what it
  holds; keeping what it holds current is the missing half, and §2.3 names the
  `SUPERSEDES` edge it should write. Belongs with the step that coordinates
  supersession (L2, S5.x).
- `GM_GRAPH_BACKEND` does not exist yet. `BUILD_NOTEBOOK.md` Appendix B
  introduces it at **S7.1** with the Neo4j backend, and a setting with one legal
  value is a setting nobody can get wrong.
- Multi-hop follows every string object, because the ontology that decides which
  strings are entity references arrives at **S3.5**. Both stores make the same
  assumption, which is at least consistent.

**Tomorrow's first step**

`S3.5` — the ontology loader and the clinical starter pack. It is what
`route()` needs to stop returning "both" for everything, what the span linker's
two under-detected noise classes need, and what tells a traversal whether a
string is an entity reference.

---

## 2026-09-13 — Day 3 · S3.5 (ontology loader, clinical starter pack)

**Shipped**

- `schemas/ontology.py` — `Ontology`, `PredicateSpec`, and an `ObjectSpec` union
  of `ScalarObject` / `CodedObject` / `EntityRefObject`, plus `parse_ontology`
  and `load_ontology`.
- `ontology/clinical.yaml` — fifteen predicates over six entity types, extending
  `MEMORY_ENGINE.md` §2.1's three worked examples.
- `pyyaml` added to `guardmem-core`; `tests/fixtures/strategy_ontology.py` for
  the property suite. 606 tests, 100% coverage.

**What broke / what I learned**

- **I wrote nine refusal tests that tested nothing, and they all passed.** The
  helper that produced a broken pack took keyword arguments and mangled the
  names back into YAML — `pack(impact__critical="impact: catastrophic")` was
  supposed to substitute `impact: critical`, and substituted nothing. So nine
  tests parsed a *valid* pack, `parse_ontology` raised nothing, and
  `pytest.raises` failed. That is the lucky version: the failure mode of a
  vacuous `pytest.raises` test is usually silence, and I would have shipped nine
  green tests covering nothing.

  The fix is not a better substitution, it is an `assert old in text` inside the
  helper, so a substitution that does not apply fails loudly. The general
  lesson, which I keep relearning in new costumes: **a test helper that
  transforms its input must assert the transformation happened.** The S3.2 log
  has the same shape in blind string replacement producing `_write_andreveal`.

- **Two defaults, two different right answers, and the difference is whether the
  absence *means* something.** `requires_corroboration` may default to `false` —
  §2.1's example omits it on two predicates and everybody reads that as "no". It
  is the ordinary case. `min_source_tier` may not, and the same example omits it
  too: there is no tier that is safe to assume. Permissive silently widens a
  safety surface on every predicate somebody forgot; strict makes an omission
  look like a broken predicate. So the pack states it fifteen times, and the
  file says why rather than looking repetitive.

- **The discriminated union paid for itself before I finished writing it.** My
  first pass was one `ObjectSpec` with optional `system` and `entity`. Then I
  wrote the test for `{type: coded}` with no system and realised it *validated*
  — producing a coded value whose terminology nobody declared, which cannot be
  validated, deduplicated, or shown to a reviewer. Three models discriminated on
  `type` turn that into a load error and get `{type: text, system: RxNorm}` for
  free from `extra="forbid"`.

- **A `@cache` plus `frozen=True` still does not give you an immutable object.**
  `load_ontology("clinical").predicates.pop("allergy")` would succeed and every
  later caller in the process would get the mutated pack. `schemas/base.py`
  already documents that freezing blocks attribute assignment and not container
  mutation; caching is what turns that footnote into a shared-state hazard, so
  the docstring says so at the call site.

- **The property suite forced a better strategy than I would have written.**
  `Ontology`'s validator requires every reference to resolve, so a strategy
  drawing entities and predicates independently fails on nearly every example
  and hypothesis reports it as a flaky filter rather than as the constraint it
  is. Drawing the entity types first and building predicates from them is the
  order a person writes a pack in, which is usually the sign a generator is
  right.

- **`strategies.py` was thirteen lines under the cap**, so the new models went
  into `strategy_ontology.py`. That was going to be a shove; it turned out to be
  a seam — these are the only models in the layer that describe a *declaration*
  rather than a fact.

**Still open**

- **Nothing consumes the ontology yet.** `l2_validate/schema_gate.py` is the
  first real consumer: it coerces a candidate's object against the declared
  value type and sends an unknown predicate to the `quarantine` namespace. The
  coercion table — `{type: coded}` to a Python type — belongs there and is
  deliberately not here.
- `StoreRouter.route()` still returns "both" for everything. The ontology now
  exists to split it, but the field that decides is the object's `type`, and the
  routing rule (§2.4's "typed relational predicates → graph") should be written
  against a working L2 rather than guessed now.
- Multi-hop `neighbors()` still follows every string object. `entity_ref` is now
  declarable, so the graph *could* ask — but `GraphStore` has no ontology and
  giving it one is a protocol change that belongs with the step that needs it.
- **Only `clinical.yaml` ships.** `PROJECT_TREE.md` lists legal and fintech
  packs; each arrives at the step that needs it, and three half-considered
  ontologies would be worse than one.
- §2.1 says an ontology change "triggers a revalidation sweep of affected
  assertions". `version` makes that possible; nothing sweeps.

**Tomorrow's first step**

`S3.6` — the seed script. It is the first thing that puts a tenant, an ontology
and a set of assertions together, which makes it the first end-to-end exercise
of everything Day 3 built.

---

## 2026-09-13 — Day 3 · S3.6 (demo tenant seed) · END OF DAY 3

**Shipped**

- `scripts/seed_demo_tenant.py` + `scripts/demo_tenant_data.py` — `make seed`.
  One tenant, seven entities, twenty-eight assertions with real provenance
  spans, two of them superseded, written through the real path and released by
  the real relay.
- `make typecheck` and the pre-commit mypy hook widened to `scripts/`.
- `tests/integration/test_seed_demo_tenant.py` — the DONE WHEN plus the END OF
  DAY 3 CHECK. 619 tests, 100% coverage.

**What broke / what I learned**

- **The idempotence requirement turned out to already be satisfied, and that is
  the best thing about this step.** I sat down expecting to write "does this row
  exist?" before every insert. None of it was needed: ids derived with `uuid5`
  meant the second run collided at every insert and the
  `ON CONFLICT DO NOTHING` from S3.2 and S3.3 absorbed it. The derived-id
  decision has now paid off three times — provenance at S3.2, the outbox row at
  S3.3, and here — and each time the *caller* got simpler rather than the
  storage getting cleverer. That is the shape of a good invariant.

- **The ontology refused four of my facts, and it was right.** I wrote
  `primary_dx`, `blood_type`, `insurance_plan` and `advance_directive` sourced
  from the patient, because that is who was talking. The pack declares
  `min_source_tier: trusted_system` for all four, and the seed's own check threw
  before a single row was written: *"blood-type: source tier verified_user is
  weaker than blood_type's declared minimum trusted_system"*. The fix is not to
  relax the pack — it is that the transcript should contain the record the
  clinician is reading from, which is both more realistic and the reason those
  tiers exist. **S3.5 earned its keep about ninety minutes after it landed**,
  which is the fastest any of these declarations has been vindicated.

- **A seed with no retired fact cannot demonstrate the product.** The step asks
  for "~30 existing assertions" and says nothing about supersession, but
  `ARCHITECTURE.md` §0's central claim is that nothing is deleted and
  contradiction resolves by supersession. A demo database with no `valid_to` set
  anywhere has no point-in-time query worth running. Three extra turns — a
  follow-up call five months later — buy two retired facts and the whole
  bitemporal story.

- **`mypy --strict` over `scripts/` caught a real bug in its first run.** The
  seed built its turn lookup as an inferred `dict[TurnId, Turn]` and handed it
  to a function declared `dict[str, Turn]`. `dict` is invariant in its key, so
  that is an error; `NewType` erases at runtime, so nothing would ever have
  failed. It is the same class of find S1.7 got when `tests/` joined the target,
  and the same argument applies to a script that writes to the database: the
  widening cost one word in the Makefile.

- **The stdout was idempotent and still misleading.** It said "28 written" on a
  re-run where nothing was written. Now it says "submitted", which is what
  actually happens: all twenty-eight are handed to the store and none of them
  land. The DONE WHEN only constrains row counts, and a script whose report
  contradicts its own behaviour would satisfy it.

- **All twenty-eight quotes match exactly, which I checked rather than assumed.**
  `link_span` tries an exact `find` first and falls back to a fuzzy pass above
  §1.3's threshold of 92, so a quote retyped from memory rather than copied out
  of the turn would still resolve — at an alignment below 1.0, quietly. Measured:
  zero unmatched, zero non-exact. Worth knowing in both directions, because it
  also means **the seed does not exercise the fuzzy path at all** and cannot
  stand in for a test of it.

**Still open**

- **Seeded vectors are reproducible noise and seeded confidence is a
  placeholder.** There is no `Embedder` in the package until **S9.1**, so the
  seed hashes text into a unit vector — identical text embeds identically and
  nothing else is modelled. Layer 3 does not exist, so `confidence` is a
  constant. Only `risk` is real, and only because it is §3.3's impact floor.
  **Nothing about retrieval quality or calibration may be measured on seed
  data**; that is the nightly eval suite's job, against a labelled corpus.
- The transcript lives under `scripts/`, which the ownership table forbids
  `evals/*` from importing. When **S22**'s eval harness wants the same forty
  turns, it moves to a shared location rather than being copied.
- The graph the seed builds is in-process and dies with the script. That is what
  `NetworkXGraphStore` is; **S7.1**'s Neo4j backend is the first durable one.
- No audit events. The §2.4 diagram puts one in the write transaction; the hash
  chain arrives at its own step, and a half-built chain verifies nothing.

**Tomorrow's first step**

`S4.1` — the schema gate. It is the first real consumer of the ontology: coerce
a candidate's object against the declared value type, and send an unknown
predicate to the `quarantine` namespace rather than rejecting it. Day 4 is
Layer 2.

---

## 2026-09-13 — Audit pass: four facts written twice, and a prompt shown the wrong ontology

Not a step. A read of everything Day 3 produced, looking for what would break
later rather than what is failing now. Nothing was failing.

**Shipped**

- `memory/vector/hash_embedder.py` — the one deterministic `Embedder`.
- `ImpactLevel.risk_floor`, `SourceTier.at_least`, `libpq_dsn` /
  `sqlalchemy_dsn` — three facts moved to the type that owns them.
- `Ontology.as_prompt_yaml()`, and the extraction fixture now passes the real
  clinical pack.
- `tests/unit/test_hash_embedder.py` and `test_shared_primitives.py`.
  656 tests, 100% coverage.

**What broke / what I learned**

- **Every finding had the same shape, which is the finding.** A fact stated in
  the document that owns it, and restated as a literal in a caller, with nothing
  comparing the two. The impact floors were prose in `ImpactLevel`'s docstring
  and a `dict` in the seed. The tier ordering was prose in `SourceTier`'s
  docstring and a tuple in the seed's data module. The DSN scheme was a magic
  prefix at three call sites. The embedder was fifteen lines written twice. I
  was not looking for a pattern; I found the same one four times, and the reason
  is structural — **a docstring that states a fact is not a place the fact
  lives**, so the next person who needs it writes it down again.

- **The tier ordering was the dangerous one, and it was dangerous quietly.**
  `SourceTier` is a `StrEnum`, so `<=` compares alphabetically: Python will tell
  you, without raising, that `tool_output` outranks `trusted_system`. Nothing
  in the repo did that comparison yet — the seed used an explicit tuple — but
  the next caller to reach for the obvious operator would have got a wrong
  answer in the direction that lets a weak source write a critical predicate.
  `SourceTier.at_least` exists, and there is a test that asserts the *wrong*
  comparison is wrong, so the method cannot later be mistaken for ceremony.

- **A real drift, not a latent one: the extraction prompt was being shown an
  ontology the loader rejects.** `extract` takes `ontology_yaml: str`, and the
  only caller passed a hand-written two-line fragment written months before the
  ontology existed. Measured against S3.5's loader: six validation errors. The
  model was being told one vocabulary while its output would be validated
  against another, and nothing compared them because nothing could - there was
  no way to get from a validated `Ontology` back to prompt text.
  `as_prompt_yaml()` closes that, and round-trips through `parse_ontology`.

- **Three inline `str.replace(dsn, ..., 1)` calls were not anchored at the
  front.** `replace` rewrites the first occurrence *anywhere*, so a password
  containing the scheme text would be corrupted. Nobody has that password. I
  only found it because writing the shared helper made me write a test for what
  it should not touch - which is the argument for extracting a duplicated
  one-liner even when the one-liner looks obviously correct.

- **I renamed rather than aliased, and the name was the point.** The first
  version of this kept `FakeEmbedder = HashEmbedder` so call sites read
  unchanged. That is worse: the thing had stopped being a fake, and shipped
  code called a fake in the one directory people look for doubles is how it
  ends up in production by accident. Eight call sites, one rename.

- **Two mistakes of my own, both caught by the tests I was writing.** I pinned
  three vector values from memory instead of measuring them, and left an
  `await` on a synchronous method. The first is the one worth naming: a pinned
  constant that was never measured is a test that asserts the author's
  recollection.

**Still open**

- `schemas/ontology.py` is at 393 lines against `RULES.md` §2.4's cap of 400.
  The next addition splits it, and the seam is already visible: the models are
  one thing and the YAML loader is another.
- `scripts/` has no `__init__.py`, so `demo_tenant_data` is a bare top-level
  module name under `mypy`'s roots alongside `tests/`. Harmless today; the same
  trap as the duplicate `conftest` if either tree ever grows a matching
  basename.
- Nothing else in the sweep: no bare excepts, no unbounded `gather`, no
  outbound call without a timeout, no mutable defaults, no TODO markers, and
  `.env.example` builds `Settings` with no missing or extra keys.

**Tomorrow's first step**

`S4.1` — the schema gate, unchanged by this pass except that it now inherits
`SourceTier.at_least` and `ImpactLevel.risk_floor` rather than needing to invent
them.

---

## 2026-09-13 — Cleanup to S4.1: the last repeated builder, and four honest dependencies

A second pass over everything up to S4.1 — which is the whole codebase, since
S4.1 is the next step and does not exist. The first audit took the duplicated
*facts*; this one took the duplicated *code* and the dependencies nobody could
account for.

**Shipped**

- `tests/fixtures/assertions.py` — one `StoredAssertion` builder, replacing five.
- The four unimported `guardmem-core` dependencies annotated with the step that
  will import them, plus two tests that keep the annotation honest.
- 660 tests, 100% coverage, no public API removed.

**What broke / what I learned**

- **The dead-code scan came back clean, and proving that was most of the work.**
  I wrote a reachability pass over every module-level name in the package, the
  tests and the scripts. It printed about 170 names — and nearly all of them
  were pytest classes and test functions, which are referenced by collection
  rather than by code. The four that were not were the `_llm: LLMClient =
  FakeLLM()` conformance assignments, which exist precisely so `mypy` checks
  structural Protocol conformance and would take that checking with them if
  deleted. **A scan whose output is 98% false positives is a scan you have to
  read all of**, and the temptation to delete something to justify the exercise
  is exactly the risk.

- **Five copies of the same builder, not three.** The first audit found three
  and I fixed those. A scan for module-level function names defined in more than
  one file found two more - `_assertion` in `test_fakes.py` and in
  `test_vector_rowmap.py`. I had missed them because they are spelled with a
  leading underscore and I had grepped for the public name. The lesson is about
  method, not about those two files: **grep finds what you already suspect; a
  structural scan finds what you do not.**

- **I consolidated five and deliberately left two.** `test_schema_models.py`'s
  builder takes `**overrides` over a dict because its job is to construct models
  that are *invalid* - a backwards interval, a span pointing at nothing - and a
  builder with typed keyword arguments cannot express that. And the three
  `_turn` helpers are one-line wrappers over a four-field model with three
  different parameter orders tuned to their call sites; merging them would make
  eighty call sites worse to remove a drift risk that is approximately zero.
  Consolidation is not a goal in itself.

- **The builder's `provenance` parameter took a single citation and had to take
  a list.** Caught immediately by `test_vector_rowmap`, which builds a
  corroborated fact with two. I had reached for the common case and made the
  model's own field shape wrong; the field is a `list` because §2.4 resolves a
  duplicate by appending, and the fixture should not be the one place that
  forgets it.

- **Four declared dependencies that nothing imports.** `httpx`, `structlog`,
  `anyio`, `numpy` - and every one of them is genuinely coming: RULES §2.2 and
  §6 name three, S5.1 needs the fourth. The right move is not to remove them and
  not to leave them silent. An audit that finds an unimported dependency and
  cannot tell "not needed yet" from "no longer needed" removes the wrong one,
  and the failure lands on a step nobody has written yet. They are annotated in
  the manifest with their step - and, because a comment nobody checks is a
  suggestion, **two tests enforce the annotation in both directions**: an
  unimported dependency must carry a step, and an annotated one must stop
  carrying it once it is actually imported. I mutated both to confirm they bite.

**Still open**

- Nothing new. The sweep found no dead code, no unused imports, no debug
  leftovers, no float equality, no `is` on literals, no bare excepts, no
  unbounded fan-out, no untimed outbound call, and `uv lock` is current.
- Carried from the audit: `schemas/ontology.py` sits at 393 lines against the
  400 cap, and `scripts/` has no `__init__.py`.

**Tomorrow's first step**

`S4.1` — the schema gate, on a codebase with one assertion builder, one
deterministic embedder, and a dependency list that can explain itself.

---

## 2026-09-13 — Day 4 · S4.1 (ontology schema gate)

**Shipped**

- `pipeline/l2_validate/schema_gate.py` — four outcomes against the tenant
  ontology, carrying `S_sch` forward. The first pipeline consumer of S3.5.
- `tests/unit/test_schema_gate.py` (32 cases) and `tests/fixtures/strategy_l2.py`.
  700 tests, 100% coverage.

**What broke / what I learned**

- **The step says three outcomes and the spec of record says four.** "pass,
  coerce, or quarantine/reject" groups the last two; §3.2's `S_sch` scale gives
  them different numbers — 0.4 unknown-but-plausible, 0 reject — and different
  fates. A quarantined candidate stays retrievable and flagged; a rejected one
  stops. Collapsing them would have thrown away the 0.4 the confidence
  composite is specified to receive, and nothing downstream would have noticed
  because nothing downstream exists yet. **Two documents disagreeing is the
  cheapest kind of bug to find and the most expensive kind to find late.**

- **"Never reaches the store router" needed two answers.** The obvious one is
  the grouping: `admitted` excludes quarantined candidates, so a caller
  forwarding `admitted` cannot write one. That guarantee only holds while the
  caller obeys it. The second is the namespace rewrite to
  `quarantine:<tenant>`, which holds when the caller does not — a flag has to be
  checked by every read path, a namespace simply is not the one a primary read
  asks for. Same shape as binding the store's tenant at construction rather than
  filtering, which is becoming this codebase's house move.

- **Coercion is mostly a list of refusals, and Python supplied two of them.**
  `bool("no")` is `True` — so a boolean predicate reading its value by
  truthiness would record a patient declining consent as having given it. And
  `True == 1`, because `bool` subclasses `int`, so a numeric predicate that did
  not check `bool` first would store `1.0` for `True`. Both are accidents of the
  language that would read as decisions once they were in a database. The
  boolean vocabulary is a closed set of six words, and the test that asserts
  `consent_flag: "NO"` becomes `False` is the one I most wanted a mutant for.

- **The eager-coercion trap is subtler than the wrong-coercion one.** My first
  `_as_boolean` ran `False` through the word vocabulary and came back with
  `(False, True)` — correct value, `COERCED` outcome, 0.3 of confidence lost for
  doing nothing at all. Caught by writing the "already the right type" test for
  each declared type rather than only for the string ones.

- **Gating against the shipped `clinical.yaml` rather than a fixture** was free
  this time and would not have been a week ago. The S3.6 audit had just finished
  paying for the alternative in the extraction fixture, where a hand-written
  ontology had drifted so far it failed its own loader.

**Still open**

- The gate cannot check a predicate's declared `subject` entity type:
  `MemoryCandidate.subject` is a surface form until entity resolution runs at
  **S4.2**. §2.1 lists it as the gate's job, and it will be once there is a
  resolved entity to check.
- `min_source_tier` is not checked here either. `RULES.md` §4 makes the tier a
  cap on what may *auto-write*, so it belongs with the decision matrix at
  **S5.4** — putting it in the gate would move a safety rule away from the table
  that composes it. The S3.6 seed checks it for its own data; that is data
  validation, not the gate.
- `schema_fit` is carried on `GatedCandidate` and nothing reads it yet.
  `ConfidenceReport.schema_fit` is filled at **S5.2**.
- Nothing joins the stages. Layer 1's output is not piped into the gate by any
  code; `orchestrator.py` is **S5.6**.

**Tomorrow's first step**

`S4.2` — incumbent retrieval. Top-k within `(namespace, subject, predicate)`
plus a one-hop graph expansion, which is the first caller of both stores at
once and of `assertion_live_idx`.

---

## 2026-09-13 — Day 4 · S4.2 (incumbent retrieval)

**Shipped**

- `pipeline/l2_validate/conflict.py`, first half — §2.2's top-10 within
  `(namespace, subject, predicate)` plus the 1-hop graph widening.
- `embed_text` moved to `memory/vector/base.py` and grew a `Claim` protocol, so
  candidates and assertions render through one function.
- `tests/fixtures/seed.py` — the seeded demo tenant as a plugin.
  729 tests, 100% coverage.

**What broke / what I learned**

- **Entity resolution is specified in no document, and this is the step that
  needed it.** §2.2 retrieves "within `(namespace, subject, predicate)`";
  `VectorStore` filters on `subject_id`, a UUID; `MemoryCandidate.subject` is a
  surface form because Layer 1 extracts what the speaker said. Something has to
  turn "Joan Ellery" into an entity id, and I searched the notebook,
  `MEMORY_ENGINE.md`, `ARCHITECTURE.md` and `PROJECT_TREE.md` for it - nothing.
  Not deferred to a step, not mentioned. **I had also written, at S4.1, that
  S4.2 was where resolution happens** - I assumed it because the gap had to
  close somewhere, and today it did not. Corrected in the changelog and in
  `schema_gate.py`, and `retrieve_incumbents` takes the resolved id as an
  argument so the absence stays visible instead of becoming a guess inside a
  retrieval function.

- **The import contract caught a layering mistake I would not have looked for.**
  `retrieve_incumbents` must embed the candidate with the same renderer that
  produced the stored vectors, so it imported `rowmap.embed_text` - and
  `lint-imports` refused, because `rowmap` imports `asyncpg` for one annotation
  and the S3.4 contract says the pipeline never reaches a driver. My first
  instinct was that the contract was being pedantic about a `TYPE_CHECKING`
  import. It was not: the right reading is that *what text a fact embeds as* is
  a decision about meaning and had no business living in the module that knows
  column order. It is in `base.py` now, beside the protocols, and its tests
  moved with it.

- **The property that matters here fails silently, which is why it has two
  tests.** If the candidate and the incumbents were rendered differently, cosine
  would still return numbers, still order them, and still look like retrieval.
  The unit test asserts the embedder was handed exactly `embed_text`'s output;
  the integration test asserts the *nearest* incumbent is the one the candidate
  restates. The mutant that embeds `provenance.verbatim` instead kills both -
  and the second is the one I would trust, because it fails on behaviour rather
  than on a call.

- **My DONE WHEN assertion was wrong and the database told me so.** I asserted
  the seeded allergy came back as the only incumbent; four came back, because
  `allergy` is `cardinality: many` in the clinical pack and the seed wrote four.
  The code was right and the test was wrong. Fixed to assert what the step
  actually means - the seeded fact is present *and* ranks first - which is a
  stronger claim than the one I first wrote.

- **Two modules named `test_incumbent_retrieval.py`.** `tests/` has no
  `__init__.py`, so a unit and an integration module sharing a basename are two
  modules with the same name: pytest aborts collection and `mypy` refuses the
  pair. The unit one is `test_conflict.py` now, named for the module it covers.
  This is the third time that trap has appeared and the first time I walked into
  it knowingly enough to recognise the error message.

- **`@dataclass(slots=True)` breaks zero-argument `super()` in a subclass.**
  `slots=True` builds a new class object and rebinds the name, so the `__class__`
  cell closed over by `super()` is the pre-slots class while `self` is an
  instance of the post-slots one. `TypeError: super(type, obj): obj must be an
  instance or subtype of type`, at the call rather than the definition.

**Still open**

- **Entity resolution.** The largest gap in the build so far: it has no step, no
  module, and no mention in any spec. Everything downstream of Layer 1 that
  addresses a subject needs it. It should get an ADR before it gets code.
- The graph half returns `Edge`s rather than assertions, because turning one
  back into a `StoredAssertion` needs a fetch-by-id `VectorStore` does not have.
  **S4.3** is the step that will know whether the checks need the provenance.
- Retrieval is sequential. `RULES.md` §2.2 wants a `TaskGroup`, and there is
  nothing to overlap while the graph is in-process; **S7.1** makes it a network
  hop.

**Tomorrow's first step**

`S4.3` — the three conflict checks, into the same module: NLI contradiction,
cardinality against the ontology, and temporal overlap.

---

## 2026-09-13 — Day 4 · S4.3 (the three conflict checks)

**Shipped**

- `pipeline/l2_validate/conflict.py` — `detect()`. §2.2's three checks in §2.2's
  order: (b) cardinality, (c) temporal overlap, then (a) NLI. The first two are
  arithmetic and short-circuit.
- `pipeline/l2_validate/nli.py` — `NLIJudge` protocol and `LLMJudge`, one
  BALANCED call for the whole incumbent set.
- `prompts/adjudicate_conflict/v1.md` — bidirectional entailment plus
  contradiction over verbatim pairs.
- `VectorStore.search` returns `ScoredAssertion` now, carrying the cosine the
  store measured.
- The 60-pair probe, by hand: **60/60 against a floor of 90%.** 784 tests, 782
  passing locally (62 against real Postgres), 2 skipped.

**What broke / what I learned**

- **The step's own pseudocode omits check (c), and the spec puts it before the
  judge.** The notebook sketch short-circuits on `ONE` and then calls
  `nli.compare`; §2.2(c) has `ONE_PER_TIME` fire on intersecting intervals, and
  it is arithmetic like (b). I built the spec's three, in the spec's order. Had
  I built the sketch, every `weight_kg` restatement would have cost a BALANCED
  call to reach an answer the ontology already had.

- **Two clauses of the spec disagree, and one of them says so.** §2.2(b) makes a
  second live value with a different object a CARDINALITY conflict *"regardless
  of NLI"*; §2.3's table would escalate the same pair on a high contradiction
  score. I followed (b) because its "regardless" is explicit and because nothing
  is lost — both routes end in supersession. Worth writing down that I checked
  rather than picked.

- **I could not honestly emit `supersede` for a contradiction.** §2.3 chooses
  between supersede and escalate using `C`, the Layer 3 confidence composite,
  which does not exist until S5. So CONTRADICTION resolves to `escalate` today.
  That is a deliberate deviation from a table in the spec of record, which is
  exactly the kind of thing that looks like a bug in three weeks if it is not
  recorded here. S4.4 closes it.

- **A 100% probe score is a reason for suspicion, not satisfaction.** Sixty out
  of sixty on a bar of ninety could mean the corpus is easy, or that the
  assertions are tautological. So I mutated the code five times: contradiction
  threshold to 0.99 → 78%, and it named all thirteen misses; cardinality testing
  `MANY` instead of `ONE` → 72%; ambiguous floor to 0.0, and deleting either of
  `nli.py`'s two guards → their own tests fail. Five for five. The mutants are
  the evidence; the score on its own is not.

- **What the probe measures is narrower than the DONE WHEN sounds, and I wrote
  that into the corpus rather than letting the number speak for itself.** Eleven
  pairs are settled before any judge is asked — those are `detect` end to end.
  The other forty-nine carry NLI numbers I set by hand, because there is no
  model to run (`RULES.md` §5 bans live calls from the unit suite), so for those
  the probe measures *my code's reading of the thresholds*, not a judge's
  accuracy. Real measurement of the half this repo owns; fake measurement of the
  half it does not. The nightly eval gate at S27.1 asks the other question, and
  this same corpus is its input.

- **The count check in `LLMJudge` is the one thing here that could lose a fact
  silently.** Judgements are matched back to incumbents by position. A reply one
  entry short does not raise on its own — it shifts, so incumbent 2's
  contradiction is read as incumbent 3's, and a fact gets retired for something
  a different fact said. Nothing downstream could detect that. It raises.

- **Two 400-line caps hit in one step, and both splits improved the code.**
  `conflict.py` reached 409 and split along the S4.2/S4.3 seam
  (`incumbents.py` / `conflict.py`) — which also let `nli.py` stand apart, right,
  since S4.3 already schedules a cross-encoder to replace it. The corpus reached
  667 and split three ways by *which mistake each row guards against*, a line
  its own explanatory paragraph had already drawn. That split killed a group
  called `_MORE` — ten rows appended "when the set was counted and came to
  fifty" — whose members all belonged to real groups. "More" was never a
  category.

- **Answering S4.2's open question: the checks do not need the graph
  neighbours.** Yesterday I left open whether S4.3 would need provenance off the
  `Edge`s. It does not — all three checks read `nearest`, which carries full
  `StoredAssertion`s. The `Edge`-vs-assertion asymmetry is still there and still
  unresolved; it just is not S4.3's problem, so I have moved it forward rather
  than closed it.

**Still open**

- **Entity resolution.** Unchanged from yesterday, and still the largest gap in
  the build. No step, no module, no mention in any spec.
- **CONTRADICTION → `escalate` is a placeholder.** It becomes a real choice at
  S5 when `C` exists; S4.4 is where the table gets implemented.
- The graph half still returns `Edge`s rather than assertions. Not needed by the
  three checks; **S4.4**'s merge is the next step that could care.
- `detect` judges one candidate at a time. A batch arriving from Layer 1 makes
  one BALANCED call per candidate with an incumbent, and nothing yet batches
  across candidates.

**Tomorrow's first step**

`S4.4` — the resolution matrix and dedupe/merge: §2.3's table, and the merge
that an incumbent already saying what the candidate says should have been all
along.

## 2026-09-13 — Day 4 · S4.4 (resolution matrix and semantic dedupe)

**Shipped**

- **`pipeline/l2_validate/dedupe.py`** — §2.3's six-row table as a pure
  function, §2.4's merge, and the precedence that reduces ten incumbents to one
  answer. `conflict.py` decides whether to ask a model; this decides what the
  answer means.
- **DUPLICATE and REFINEMENT, which S4.3 carried the evidence for and compared
  nowhere.** REFINEMENT is the row that needs both entailment directions, and
  the reason `Judgement` has two.
- **`merge`** — appends the citation, raises `corroboration_count` only for a
  genuinely new `source_hash`, keeps the same `assertion_id`. Idempotent,
  because S3.3's relay replays.
- **Invariant I2 over 500 generated write sequences**, plus three properties
  beside it so the invariant is not satisfiable by writing nothing.
- 759 unit and property tests passing; Layer 2 at 100% statement and branch
  coverage.

**What broke / what I learned**

- **I wrote the equality check as a short circuit and it was wrong.** The
  reasoning was clean: if the incumbent already holds this exact object, cosine
  and entailment have nothing left to establish, so return DUPLICATE before
  paying for a judge. It ran ahead of the judge, and an S4.3 test —
  `test_a_contradiction_never_comes_back_as_supersede` — turned red with
  `merge` where it wanted `escalate`.
  The test was right and I had missed something real: **`object` carries no
  polarity.** "Allergic to penicillin" and "not allergic to penicillin" both
  extract `penicillin`. The negation lives in the verbatim, which is the only
  thing the judge ever sees. So an equal object is not agreement, and the
  short-circuit version would have merged a fact with its own negation —
  raising `corroboration_count` for it, which raises `S_cor`, which raises `C`.
  That is precisely the failure I had written a paragraph against in the same
  file an hour earlier, under "contradiction is read before the similarity
  rows", and then reintroduced by putting the check somewhere else. Equality is
  now *evidence* handed to `classify`, read below the contradiction rows.
- **Two of the S4.3 tests were using identical objects as scaffolding.** They
  wanted to reach the judge and picked the shortest route, which happened to be
  an incumbent holding the same value. Once equality meant something, those
  tests were asserting on a case they were not about. Fixed the fixture, not the
  rule — `latex` against `penicillin` is what "the judge is being asked about
  these two" actually looks like.
- **Nineteen corpus pairs had the wrong label and the probe told me.** Every
  miss was a row the corpus itself had named `-same`, `-restated` or
  `ok-duplicate-wording`, sitting at `expected=NONE`. They were always
  duplicates; S4.3 had no DUPLICATE row to label them with. Relabelled, nothing
  added or removed — a probe that grew nineteen easy pairs would have raised the
  score by dilution.
- **The table has a hole and I nearly papered over it.** Cosine 0.88, both
  entailments 0.9, no contradiction: that matches no row in §2.3. The first
  instinct was to widen row 1's cosine until the gap closed. The honest version
  is a documented fall-through to `coexist` and a note that the table is not
  total, because widening a threshold to make a function total changes what the
  spec says while looking like an implementation detail.
- **`most_severe` is a rule §2.3 does not contain.** The table is written for
  one pair; retrieval returns ten. I could not find a reading of the spec that
  settles it, so the ordering is stated in one comment block with its argument
  attached, rather than emerging from the order of a few `if`s.

**Still open**

- **Entity resolution.** Unchanged, and still the largest gap in the build.
- **CONTRADICTION → `escalate` is no longer a placeholder**, though it looks
  identical from outside. The rule is implemented in full and `escalate` is what
  it returns when `C` is unknown. S5.4 is where the number arrives and the
  answer can change.
- **The applier does not exist.** `detect` returns a hint and nothing carries it
  out; the four lines that do live in the I2 property test. S5.6 joins the
  stages, and that is the step that has to prove it honours the hint.
- **`merge` does not recompute confidence**, and §2.4's sentence asks for it.
  `S_cor` is one of five inputs to §3.2's composite — S5.2's, not this step's.
- The graph half still returns `Edge`s rather than assertions. The merge did not
  need it either, so it moves forward again.

**Tomorrow's first step**

END OF DAY 4 CHECK: "a second contradictory fact is detected, not silently
stored alongside." Then `S5.1` — semantic entropy, and the worked example in
§3.1 that has to reproduce `H_norm = 0.590` to three decimals.

---

## 2026-09-14 — Day 5 · S5.1 (semantic entropy)

**Shipped**

- **`pipeline/l3_score/entropy.py`** — §3.1's bidirectional-entailment
  clustering, `H_norm = H / log K`, and the minority-hallucination drop. The
  first Layer 3 module.
- **The DONE WHEN**: §3.1's worked example reproduces `H_norm = 0.590` to three
  decimals, with the clusters and the unnormalised `H = 0.950` pinned beside it.
- **`EntailFn` injected**, so LID's detector can back it later without touching
  this module. 793 unit and property tests passing; `entropy.py` at 100%
  statement and branch coverage.

**What broke / what I learned**

- **The formula overshoots its own bound, and the case it breaks on is the one
  that matters most.** `MeaningClusters.entropy` is declared `le=1.0`. When
  every sample is its own cluster, `H` equals `log K` exactly in arithmetic —
  and in float64 it is `log K` give or take an ulp. I measured it across
  K = 2..199: the error never exceeds 8e-16, but it lands strictly *above* 1.0
  for 51 of those values, and **K = 5 is one of them**. Five is §1.2's largest
  sample count, so "five samples that all disagree" raised a `ValidationError`.
  That is the commonest maximum-uncertainty case in the system and precisely
  what the entropy term exists to detect — the module would have crashed exactly
  when it had something to say. A test asserting `== 1.0` is what surfaced it,
  and the honest fix was to go and measure the range rather than reach for
  `approx` and move on.
- **A mutant caught a test of mine that was passing for the wrong reason.**
  Deleting the reverse-entailment check killed only one test, and it was not the
  one named `test_one_way_entailment_does_not_merge`. I had ordered its two
  samples so the *forward* comparison failed, which meant the reverse check the
  test was written to exercise was never reached. It passed, it was green all
  along, and it was measuring nothing. Reordered; it now kills that mutant.
  Worth noting the mutation run is the only thing that could have told me.
- **I nearly dropped `numpy` and talked myself back.** For K <= 5 the vectorised
  `-(p * log p).sum()` buys nothing over a loop, and I had a clean precedent for
  retagging an unused dependency (`anyio`, at S4.3). But CHECKPOINT B computes
  AUROC over 200 labelled candidates, so numpy stays in the manifest either way
  — which makes spec fidelity the cheaper tie-break and the retag pure churn.
  The deferred-dependency guard then made the bookkeeping automatic: the moment
  the import landed, it told me to move the entry out of the block.
- **The 400-line cap moved a file for the third time, and again it improved
  things.** Registering Layer 3 took `strategies.py` to 404. Rather than shave,
  `schemas/verdict.py`'s four generators went to `strategy_verdict.py` — the
  seam this directory already draws, one strategy module per source module.
- **My first `MeaningClusters` strategy was a repair pass and it was wrong.** I
  drew arbitrary label lists and tried to fix them into valid union-find roots,
  which is a second implementation of `_find` living in the fixtures. Replaced
  with a construction that cannot produce an invalid partition: each sample
  either starts a cluster or joins one that already has a root.

**Still open**

- **The grouping from `ExtractionResult.samples` is not built, and its docstring
  said S5.1 owned it.** §3.1 clusters "the K samples for a given
  `(subject, predicate)`", and turning `list[list[ExtractedFact]]` into that
  needs an answer to a question no document asks: if three of five samples
  proposed a fact, is K three or five? Treating it as three discards real
  evidence of uncertainty; treating it as five needs a rule for what absence
  clusters *as*. Both are defensible and both silently recalibrate `C`, so I did
  not invent one — the docstring now points at S5.6, which composes the pipeline
  and has the whole picture.
- **The minority drop cannot fire.** Candidates come from the canonical sample,
  so every candidate is in sample 0's cluster by construction.
- **Entity resolution.** Unchanged.
- **No applier.** Unchanged from S4.4 — S5.6.

**Tomorrow's first step**

`S5.2` — the confidence composite: `C = w_H(1-H) + w_g S_src + w_s S_sch +
w_c S_cor + w_k S_con` with the v1 weights, the weights version string on every
report, and `S_cor = 1 - exp(-0.8(n-1))` checked against 0.0 / 0.55 / 0.80 /
0.91 for one to four sources.

---

## 2026-09-14 — Day 5 · S5.2 (confidence composite)

**Shipped**

- **`pipeline/l3_score/confidence.py`** — §3.2's five terms, `V1_WEIGHTS`, and
  the weight set that refuses to sum to anything but 1. Pure: the entailment
  `S_src` needs is an argument, not a model call.
- **`SourceTier.grounding_multiplier`**, beside `at_least`, because it is a fact
  about the tier.
- **The DONE WHEN both ways**: each term pinned with the whole weight on it and
  the rest zeroed, plus the sum-to-1 assertion. `S_cor` reproduces the step's
  0.0 / 0.55 / 0.80 / 0.91. 836 tests; `confidence.py` at 100% statement and
  branch coverage.

**What broke / what I learned**

- **The spec's own formula scores a contradiction as perfectly consistent, and
  I nearly implemented it.** §3.2 says `S_con = 1 - contra`. But S4.3 writes
  `contradiction = 0.0` on any conflict the deterministic checks settled, on
  purpose, because a made-up number would read as a measurement. Put those two
  together and a candidate that directly clashes with a live `ONE` predicate
  scores `1 - 0.0 = 1.0` — the maximum consistency score, awarded to the one
  case that is definitionally inconsistent with memory. The zero means *nobody
  measured*, and nothing downstream can tell that apart from *measured zero*. I
  had written the literal version and the test I wrote next — "a cardinality
  clash scores below a novel fact" — is what made me look.
- **Two of my own tests were wrong before the code was.** A weight of 1.1 never
  reaches the sum validator, because the field's own `le=1.0` bound rejects it
  first; the test had to spread 1.1 across two fields to test what it claimed
  to. And `corroboration(1000) < 1.0` failed because the term saturates — which
  led to the next one.
- **I estimated the saturation point and was wrong by a factor of twenty.** I
  reasoned that `1 - exp(-0.8(n-1))` hits 1.0 where `exp` underflows, around
  n = 930. It actually hits 1.0 at **n = 48**, where the remainder drops below
  the epsilon of 1.0 — a different and much earlier limit. I only found out
  because I measured instead of writing the estimate into the test. Harmless in
  itself, but the reasoning was the kind that reads as authoritative and is not,
  so the measured number is pinned with a note saying it was measured.
- **A mutant showed a test suite that could not see a transposition.** Swapping
  `w_g` and `w_s` failed exactly one test — the one that reads the weights
  literally. Every composite test had grounding and schema_fit both at 1.0, so
  the arithmetic genuinely could not notice which weight went where. Added a
  case with five distinct term values checked against the hand-computed result.
- **§3.2's tier multipliers contradict `RULES.md` §4's ordering.** §4 ranks an
  unverified human above a tool output; §3.2 scores the tool output higher (0.85
  against 0.8). I implemented both as written, because they answer different
  questions — authority against textual support — and pinned the inversion with
  a test so nobody "fixes" it on the assumption that one ordering governs both.
  **This one wants a human decision**: if it is a typo, it belongs fixed in the
  spec, not worked around here.

**Still open**

- **The tier-multiplier inversion** above. Flagged, not resolved.
- **`S_src`'s entailment has no producer.** The module takes it as a number and
  nothing computes it yet: it needs an NLI call over (verbatim span, claim), and
  the claim has no natural-language rendering — `embed_text` renders
  `predicate: object` for vectors, which is not a sentence to entail. S5.6.
- **`sources` likewise has no producer.** A fresh candidate has one citation, so
  `S_cor` is 0.0 for everything until a merge raises `corroboration_count`.
- Entity resolution, the missing applier, `ExtractionResult.samples` grouping:
  all unchanged.

**Tomorrow's first step**

`S5.3` — impact risk: the linear score, the sigmoid, then the floor by declared
impact, with the whole feature dict persisted. The DONE WHEN is the one that
makes the point of separating `C` from `R`: a CRITICAL-impact candidate with
perfect confidence still scores `R >= 0.80`.

---

## 2026-09-14 — Day 5 · S5.3 (blast-radius impact risk)

**Shipped**

- **`pipeline/l3_score/impact.py`** — §3.3's linear score, the squash, and the
  floor by declared impact. `impact_features.py` beside it for the eight
  features and the vocabularies they come from.
- **`ImpactLevel.risk_feature`**, beside `risk_floor`, and a test asserting the
  two differ at every level.
- **The DONE WHEN**: a CRITICAL candidate benign on every other axis scores
  0.255 on the linear model and 0.80 after the floor. Both asserted. 905 tests;
  both new modules at 100% statement and branch coverage.

**What broke / what I learned**

- **A test of mine passed for the wrong reason, and the parametrisation is what
  showed it.** `test_every_feature_raises_risk_on_its_own` bumped one feature
  from zero and checked `R` went up. Seven of the eight cases failed — not
  because the betas were wrong, but because at all-features-zero `z` is -3.4 and
  a single feature rarely lifts the sigmoid past even the LOW floor of 0.15. The
  *floor* was deciding, so the test would have passed against almost any beta.
  My own docstring had claimed LOW impact avoided this. Rerun from a mid-range
  baseline where `z` is 1.6, with an explicit assertion that the floor is not
  deciding. Running it per-feature is the only reason I saw it at all; in
  aggregate it would have passed.
- **`novelty = 1 - cosine` can exceed 1, and I wrote the clamp only because I
  went back to check why `ConflictReport.cosine` is bounded at -1.** It is
  because cosine over unnormalised embeddings is genuinely negative. So a
  perfectly ordinary retrieval result would have produced a feature of 1.4 and
  `RiskFeatures` would have raised on it — a validation error from a legitimate
  input, which is the worst kind because it looks like a bug in the caller.
- **`mutation_type` has no clean source and I nearly used the wrong one.**
  `resolution_hint` is the obvious field and it does not fit: `merge` and
  `escalate` are hints with no mutation type, `refine` and `retract` are
  mutation types with no hint. Worse, using it would have scored an escalated
  CONTRADICTION as `coexist` — pricing the risk of the *decision* instead of the
  write, when what is proposed is retiring a live fact. `ConflictKind` maps
  cleanly and asks the right question.
- **Three of the eight features are specified and produced by nothing.**
  `scope`, `pii_class` and `irreversibility` appear in §3.3's table and in no
  other document, no ontology field, no extractor. I made them enums with the
  spec's values rather than float arguments, so the vocabulary is reviewable and
  a caller has to state a choice instead of passing 0.0 by default. That does
  not close the gap, it just stops it being invisible.
- **§3.2's tier inversion propagates into risk.** `source_tier_risk` is
  `1 - grounding_multiplier`, so a tool output scores *less* risky (0.15) than
  an unverified human (0.2). Pinned with a test in both modules, so whoever
  resolves the §3.2-vs-§4 question finds every site that depends on the answer.

**Still open**

- **`RiskVerdict` has nowhere to record which betas produced its `R`.** §3.3
  refits them weekly and `RiskBetas` carries a `version`; §0 gives the verdict
  four fields and none of them is it, and `DecisionRecord` versions thresholds
  and policy only. After the first refit `replay_trace.py` recomputes a
  different `R` and prints a diff it cannot explain. Needs an ADR; **S5.5**'s
  audit chain and **S5.6**'s replay are where it bites.
- **The §3.2 tier-multiplier inversion.** Still awaiting a human call.
- **`scope`, `pii_class`, `irreversibility` have no producer.** S5.6 passes
  them; nothing classifies a predicate.
- Entity resolution, the missing applier, `ExtractionResult.samples` grouping:
  unchanged.

**Tomorrow's first step**

`S5.4` — the decision matrix. Pure function, no I/O, no clock, no randomness:
the matrix, then the seven hard overrides in order, with `Thresholds` passed in
as a value and half-open bands. The DONE WHEN includes invariant I4 — `decide()`
total and deterministic over `C, R` in `[0,1]²`.

---

## 2026-09-14 — Day 5 · S5.4 (decision matrix)

**Shipped**

- **`pipeline/l3_score/decision.py`** — §3.4's twelve cells, the starred cell's
  corroboration test, and the clamp that stops an escalation recursing.
- **`pipeline/l3_score/overrides.py`** — the seven hard overrides as a rule
  table, with `tighten` turning "obligations compose, they never relax" into an
  ordering rather than a convention.
- **`Thresholds`** in `schemas/verdict.py`, with `Settings.thresholds()` as the
  only construction path, plus `GM_THRESHOLDS_VERSION`.
- **Invariant I4** property-tested over generated thresholds as well as
  generated scores. 1044 tests; all of Layer 3 at 100% statement and branch
  coverage.

**What broke / what I learned**

- **Hypothesis disproved a property I was confident about.** I wrote "raising
  `C` never makes the decision worse" as an obvious monotonicity check, and it
  failed on the third example. Read down §3.4's middle risk column: the
  `tau_lo` band ESCALATEs and the `tau_mid` band above it HITL_REVIEWs. More
  confidence, *stricter* outcome. My first instinct was that the table had a
  transposed row — it does not. Escalation re-runs Layer 3 on FRONTIER, which is
  worth paying for precisely where the model is unsure enough that a bigger one
  might settle it; above `tau_mid` it would not, so the remaining doubt is a
  person's. §3.5 budgets FRONTIER at <=6% of candidates, which is the same
  decision seen from the cost side. I replaced the false property with two true
  ones and pinned the non-monotone step as deliberate.
- **A mutant survived, and the reason was that all my boundary tests lived in
  the wrong column.** Making `tau_hi` exclusive killed nothing: at low risk the
  top two rows are both AUTO_WRITE, so the boundary is invisible there, and
  every case in `TestTheBandsAreHalfOpen` used low risk. It only bites in the
  middle column. Three new boundary tests, each at the risk band where that
  threshold actually decides something. `tau_mid` and `tau_lo` were fine by
  luck, not by design.
- **§3.4's table contradicts its own prose about `R`.** "Lower bound inclusive,
  upper exclusive" against column headers drawn `<= rho_lo` and `> rho_hi`. I
  went with the prose, because it is the sentence stated as a rule and because
  every disagreement then lands on the stricter cell — but this is a real
  ambiguity in the spec of record and the two tests that pin it say so.
- **Override 1 asks for an outcome that does not exist.** "-> QUARANTINE", and
  `Decision` has four members, none of them that. The quarantine is a namespace
  from §2.1, so the decision is REJECT. Worth noticing that the parenthetical
  "(never AUTO_WRITE)" is the operative part and the enum was never going to
  carry the rest.
- **Four of the seven overrides are unimplementable from §3.4's own
  signature.** Nothing in `(conf, risk, conflict, thresholds,
  already_escalated)` knows about a canary, a source tier, an ontology flag or a
  circuit breaker. I could have dropped them, or widened a spec-of-record model;
  both are worse than one extra parameter carrying data. `OverrideSignals` has
  no defaults on purpose — a benign default is an override that quietly does not
  fire, and the caller who forgets one gets the *permissive* answer.
- **`Thresholds` ended up somewhere the docs said it would not.**
  `verdict.py` had a paragraph saying S5.4 would own it. Settling its shape
  turned up the reason not to: `Settings` already enforced the band ordering, so
  a copy of that rule beside `decide()` would have been two homes for one
  invariant. Updated the paragraph rather than leaving it wrong.
- **The 50-line function cap moved `apply_overrides` into a rule table**, which
  is better code than what it replaced — the loop is six lines and knows nothing
  about any individual rule.

**Still open**

- **§3.4's `R` band ambiguity.** Implemented one way, pinned by tests, but the
  spec says both things. Worth a decision from whoever owns it.
- **QUARANTINE is named by §3.4 and absent from `Decision`.** Same.
- **`RiskVerdict` still cannot record which betas produced its `R`** (#57), and
  **§3.2's tier multipliers still invert `RULES.md` §4** (#52). Both unchanged.
- Entity resolution, the missing applier, `ExtractionResult.samples` grouping:
  unchanged. S5.6 owns the applier and is now two steps away.

**Tomorrow's first step**

`S5.5` — the audit chain: `digest_n == sha256(payload_n || digest_{n-1})`,
invariant I5, and the first thing in the build that makes a decision provable
rather than merely recorded.

---

## 2026-09-14 — Day 5 · S5.5 (hash-chained audit log)

**Shipped**

- **`observability/audit.py`** — I5's digest, canonical JSON, `next_link` and
  `verify_chain`. Pure, so the whole invariant is unit-testable without Docker.
- **`observability/audit_store.py`** — the `audit_event` table, with `append`
  taking a *connection* so the row commits with the state change it records,
  and a per-tenant advisory lock so concurrent appends cannot fork the chain.
- **The DONE WHEN against a real database**: a real `UPDATE` on a real row, and
  `verify_chain` naming that exact `seq`. 1088 unit and property tests plus 74
  integration tests; `audit.py` at 100% statement and branch coverage.

**What broke / what I learned**

- **Running the integration suite found two bugs, and one of them had a comment
  warning me about it.** `event_from_row` passed `row["tenant_id"]` straight to
  the model. asyncpg returns `uuid.UUID` for a UUID column, and `TenantId` is a
  `NewType` over `str` — `rowmap.py` has a paragraph explaining that exact trap,
  which I had read three steps earlier. Pydantic's `strict=True` refused it
  outright, which is the good outcome: the alternative is a `UUID` object that
  compares unequal to every tenant id in the pipeline and fails somewhere else
  entirely. The second was mine too: `_drop_tenant` did not clear `audit_event`,
  whose foreign key to `tenant` made every teardown fail and leaked one test's
  tenant into the next.
- **I nearly shipped a one-check `verify_chain`.** Comparing each link's digest
  against a recomputation catches an edited payload, and I wrote the test for
  that and it passed. The case it misses is the one an actual attacker would
  use: recompute the digest too, and that link is now internally consistent.
  What they cannot fix without rewriting the whole tail is the *next* link's
  `prev_digest`. Writing the tamper tests as "each specific way of editing
  history" rather than "tampering is caught" is what surfaced it.
- **The digest has to survive `JSONB`, which is not the same as surviving
  `json.dumps`.** Postgres does not store an object as text; it normalises key
  order and whitespace. So canonical JSON is sorted, unpadded and ASCII-escaped,
  and the integration suite asserts a rich payload comes back *byte-identical*
  rather than merely verifying — because if a number's formatting had moved, the
  failure would surface as an unexplainable digest mismatch.
- **A `datetime` in a payload is refused rather than stringified**, and the
  reason is subtle enough to be worth the paragraph in the docstring: reaching
  for `default=str` produces a digest over the *string*, which `JSONB` hands
  back unchanged. It would verify forever while the audit record quietly
  disagreed with the object it was taken from.
- **Concurrency would fork the chain and nothing would say so until a
  verification ran.** Two transactions read the same head, both claim it. The
  fix is an advisory lock rather than `SELECT ... FOR UPDATE`, because that
  statement needs UPDATE privilege and `0001_initial` revokes it on this table —
  the append-only grant and the obvious locking primitive are in direct
  conflict, which took a minute to see.

**Still open**

- **A truncated chain verifies.** Dropping links from the head leaves a shorter,
  internally consistent chain. Detecting it needs an external witness — a
  published head, a countersignature — and neither is specified anywhere.
  `ChainVerification.checked` is what a caller compares against its own
  expectation, and that is the whole of the mitigation today.
- **`read_chain` reads the whole chain.** Honest now, not for a busy tenant. The
  incremental form needs a verified-prefix checkpoint, which needs somewhere to
  record it.
- **`memory/vector/pool.py` is the Postgres layer for the whole package** and
  sits under `memory/vector/` because that is where S3.2 first needed it. The
  audit store is the second caller saying so. A rename across three steps'
  code; its own commit.
- **Nothing calls `append` yet.** S5.6's orchestrator is what puts an audit
  event in the same transaction as a write — open item 18 finally has its step.
- The four spec questions (52, 57, 61, 62) are unchanged.

**Tomorrow's first step**

`S5.6` — the orchestrator and deterministic replay. It is the step every
"nothing joins the stages yet" note has been pointing at: Layer 1 to Layer 3 in
one call, the applier that carries out a resolution, and
`scripts/replay_trace.py` printing "identical" for a fresh trace.

---

## 2026-09-14 — Day 5 · S5.6 (orchestrator and deterministic replay)

**Shipped**

- **`pipeline/orchestrator.py`** — one proposal, every layer, for the first
  time. Candidates scored concurrently under an explicit bound, each failure
  attributed to its candidate rather than cancelling the batch.
- **`pipeline/deps.py`** — `Deps`, and the three protocols nothing implements.
- **`pipeline/inputs.py`** — the joins: the document spans index into, §3.1's
  draw grouping, §3.2's claim rendering, §3.3's feature assembly.
- **`scripts/replay_trace.py`** — the DONE WHEN. Prints "identical". 1146 unit
  and property tests plus 79 integration; the three new modules at 100%
  statement and branch coverage.

**What broke / what I learned**

- **I settled §3.1's `K` question and got the fix backwards on the first
  attempt.** S5.1 deferred "when three of five draws propose a fact, is K three
  or five?" to this step. Five is right, and the two missing draws are
  abstentions — that part I reasoned to correctly. Then I clustered the
  abstentions *together*, on the perfectly sensible grounds that silence is one
  meaning rather than one per silent sample. The numbers said otherwise: one
  claim against four shared abstentions is a 0.2/0.8 split, and entropy measures
  *spread*, so a lopsided split is low-entropy. A fact one sample proposed came
  out more confident than one three samples agreed on. I only saw it because I
  printed the table rather than trusting the argument.
  The fix is not the arithmetic, though — it is that `same_meaning` is
  bidirectional entailment, an abstention asserts no proposition, and a
  non-assertion entails nothing, including another one. Leaving them unclustered
  falls out of the ordinary rule and makes support monotone: five agreeing 0.0,
  three of five 0.59, one of five 1.0.
- **Composing the pipeline is what finally forced three gaps into the open.**
  Entity resolution, §3.3's `pii_class` and `irreversibility`, and the
  entailment function. Each had been "specified nowhere" in an open item for
  weeks; what changed is that `run()` cannot be written without them. Making
  them injected protocols with *nothing shipped* is the honest form — a default
  entity resolver would have looked like progress and silently split one patient
  across three spellings.
- **The orchestrator cannot write, and that is a real architectural conflict
  rather than laziness.** `RULES.md` non-negotiable #4 wants the audit event in
  the same transaction as the state change. `VectorStore.upsert` owns the only
  transaction and the protocol hands out no connection — deliberately, because
  §2.4 makes the backend an operator decision. Closing it means widening the
  protocol or teaching the store about audit, and both deserve an ADR. What I
  could do honestly was notice that a DECISION event is not a state change: three
  of the four outcomes write nothing, so `append_decision` ships alone and the
  WRITE event waits.
- **`strict=True` refuses its own JSON round trip through a dict.**
  `DecisionRecord.model_dump(mode="json")` writes enums as strings; reading them
  back with `model_validate` on the `JSONB` dict is rejected, because in dict
  mode a string is not an enum. `model_validate_json` accepts the identical data,
  because in JSON a string is *how* an enum is spelled. Only visible against a
  real column.
- **Open item #35 came true exactly as written.** It said `scripts/` having no
  `__init__.py` was "harmless today; the same trap as the duplicate `conftest` if
  either tree grows a matching basename". What grew was a test importing
  `scripts.replay_trace`. Adding the package cost one Makefile line, and broke
  the seed integration test until I switched that to `-m` too — which the fixture
  now explains rather than just doing.
- **Two caps fired and both improved the code.** `_decide_one` at 78 lines split
  at the Layer 2 / Layer 3 seam; the module at 401 lines gave up
  `override_signals` to `inputs.py`, where it belonged anyway.

**Still open**

- **The applier, still.** `run()` returns decisions and nothing carries them
  out. This is now a *narrower* gap than before — it needs one Postgres-specific
  composition owning one transaction — but it needs an ADR first.
- **Entity resolution, `pii_class`, `irreversibility`**: named as protocols,
  implemented by nothing. `run()` refuses to be called without them, which is
  the gap being loud rather than closed.
- **No escalation loop.** Every candidate is a first pass; an ESCALATE is
  returned for the caller to act on. §3.4's second pass needs FRONTIER providers
  (S9.1) to be worth measuring.
- **`scope_of_namespace` under-prices a shared namespace with an unrecognised
  prefix** — `team:eng` scores as one subject. Pinned by a test that says so.
- The four spec questions (52, 57, 61, 62) are unchanged.

**Tomorrow's first step**

**CHECKPOINT B** — "the most important gate in the project". 200 candidates from
the seed transcript, hand-labelled by a human, and the AUROC of `C` against those
labels. `>= 0.80` proceeds; `< 0.75` stops the build. Everything after it assumes
the scoring can tell good candidates from bad ones, and Day 5 is cheap to redo
where Day 25 is not.

---

## 2026-09-14 — CHECKPOINT B (the harness; the gate itself is blocked)

**Shipped**

- **`guardmem_core/eval/discrimination.py`** — AUROC by ranks with tie
  correction, the per-term diagnostics, the self-agreement check, and the
  PASS/MARGINAL/FAIL bands.
- **`scripts/checkpoint_b.py`** — `verify`, `template`, `score`, `agreement`.
- **B1-B8 all pass**, run through `verify` rather than read. 1204 unit and
  property tests plus 79 integration; `discrimination.py` at 100% statement and
  branch coverage.

**What broke / what I learned**

- **The gate cannot be run, and it is not a matter of effort.** Step 3 is "run
  the pipeline, collect `C` for each". There is no `LLMClient` implementation -
  only the Protocol and `FakeLLM`. I checked before building anything, which
  was the right order: the alternative was building a generator that could never
  be honestly executed.
  What I briefly considered and rejected was hand-authoring the 200 candidates.
  It would have produced a number, and the number would have measured whether
  `C` separates *my* idea of a plausible bad extraction from my idea of a good
  one. That is the checkpoint's own "no model grading" rule broken by a
  different route, and it would have been worse than no measurement because it
  would have looked like one.
- **So CHECKPOINT B is gated on S9.1, not on Day 5**, and the notebook's
  placement does not say that. Recorded in the notebook and in the sign-off,
  because the risk here is specific: Day 6 onward assumes the scoring
  discriminates, and proceeding without noticing the gate never ran is exactly
  the three-wasted-weeks failure the checkpoint exists to prevent.
- **Writing the sign-off block honestly found a gap I had not looked for.** The
  automated-check line reads "I1, I2, I3, I4 must all be green" and
  `tests/property/` holds three files. **I3 - supersession is acyclic - has no
  property test.** I would not have noticed by running the suite, because a
  missing test passes.
- **Ties are the substance of the AUROC implementation, not an edge case.**
  `S_cor` is 0.0 for every single-sourced candidate, and every candidate is
  single-sourced today - so the per-term diagnostic over corroboration is
  *entirely* ties. A threshold-sweep or tie-blind implementation would have
  reported something between 0 and 1 depending on extraction order. The rank
  form with average ranks returns exactly 0.5, and a test asserts it.
- **`auroc` raises on a one-sided corpus rather than returning 0.5**, which took
  a moment to see as the right call. 0.5 reads as "no discriminative power"; the
  truth is "there was no question here". One of those sends you back to the
  labels and the other sends you back to the scorer.

**Still open**

- **THE GATE ITSELF.** Not run. Everything after Day 5 assumes the scoring
  discriminates and that is unverified. Run it the day S9.1 lands.
- **I3 has no property test.** Supersession acyclicity.
- **No `LLMClient` implementation** - S9.1. This also means nothing in the repo
  has ever made a real model call.
- The applier (#44), and the four spec questions (52, 57, 61, 62), unchanged.

**Tomorrow's first step**

Day 6 - but with the gate recorded as open. The honest alternative is to jump to
`S9.1` (provider adapters), run CHECKPOINT B for real, and only then build the
gateway. That is a sequencing decision rather than a technical one, and it
belongs to whoever owns the roadmap.

---

## 2026-09-14 — Day 6 · S6.1 (MCP server skeleton) + a red-CI debug pass

**Shipped**

- **CI is green again.** Two jobs were red on `main` — `integration` since S5.6
  and `gates` since CHECKPOINT B — and it was one root cause and one line:
  `pythonpath = ["."]` in `[tool.pytest.ini_options]`.
- **S6.1** — `services/mcp_server/`, the first deployable. Speaks MCP over
  stdio, opens the pool, loads the ontology, advertises tools/resources/prompts,
  lists **zero** tools. `guardmem-mcp` is the console script.
- **`tests/integration/test_mcp_stdio.py`** runs S6.1's DONE WHEN in CI: a real
  `ClientSession` over the SDK's in-memory transport, asking for the tool list.
- **Invariant I3 has a property test**, closing yesterday's gap.
- Four production defects fixed — see below. 1236 unit and property tests plus
  87 integration; 99.76% with the integration suite included.

**What broke / what I learned**

- **The CI failure was a lesson about how tests find their own subject.** Both
  failing tests import the module they test from `scripts/`, and *nothing* put
  the repo root on `sys.path`. pytest's prepend mode inserts the directory that
  holds `conftest.py` — `tests/` — not the workspace root. So `import scripts`
  had been working by accident all along: `python -m pytest` prepends the CWD,
  and the bare `pytest` console script that the Makefile and CI both run does
  not. The Windows form is nastier than the Linux one: the filesystem is
  case-insensitive, so `import scripts` *succeeds* against `.venv\Scripts` as a
  namespace package and only the submodule import fails — which reads like a
  missing file rather than a path problem, and I spent the first few minutes
  looking for a file that was right there.
- **`audit_store.py` had four statements with no timeout.** Every other call
  site in the package passes one; these four shipped without. The one that
  matters is `pg_advisory_xact_lock`, which waits without any bound of its own —
  one stalled transaction parks every later append on that tenant forever, each
  holding a pooled connection, until the pool is gone and the process stops
  serving every *other* tenant too. Found by grepping for `await connection.`
  with no `timeout`, which is a five-second check I should run every step.
- **`HashEmbedder.texts` grows forever**, and the docstring said "recording is
  free". It is free for a script that exits, which was every caller it had. It
  is not free for a server — and S6.1 is the step that gave it one. Two things
  I had written days apart turned into a leak the moment they met, which is the
  argument for doing this sweep at the step that adds the first long-lived
  process rather than later.
- **`GM_MAX_CONCURRENT_SCORES` did nothing at all.** Declared at S1.4, and S5.6
  wrote its own module constant next to the semaphore. Dead configuration is a
  particular kind of bad: it does not fail, it *answers* — you turn the knob,
  nothing changes, and you conclude something about the system that is false.
- **S6.2 is blocked on S9.1, which building S6.1 is what revealed.** I expected
  the blocker for Day 6 to be tool schemas. It is not: a `memory.propose` tool
  is a call to `run()`, and `run()` needs an `LLMClient`, an `EntityResolver`
  and a `CandidateClassifier` — none of which exist. The same wall CHECKPOINT B
  hit yesterday, met from the other side. **Day 6 is one step long, not four.**
- **I did not advertise `resources.subscribe`, against the step's instruction.**
  The other three capabilities say what can be *listed*; `subscribe` promises a
  notification, and nothing here can send one. A client that subscribed would
  wait forever and could not tell that from "nothing has changed" — a failure
  with no symptom. Recorded as correction 2 on S6.1 and pinned by a test.
- **The notebook has no appendices**, and four documents point at them —
  `settings.py` at "Appendix B" for the env inventory, `.env.example` at the
  same, `docs/README.md` at "Appendix D", and the roadmap at A and G. Three now
  point at the document that actually owns the fact. **Appendix G is the one I
  left**: it is the cut-order list, i.e. what to sacrifice when behind, and
  inventing that would be me making a scope decision. The roadmap now says it is
  unwritten instead of pointing at a section that would answer it.
- **The dependency guard earned its keep again.** Adding `mcp[cli]` made
  `uv lock` resolve four transitive packages ahead of `requirements.lock.txt`,
  and `test_uv_lock_agrees_with_requirements_lock` caught it on the first run.
  Pinned with `[tool.uv] constraint-dependencies` rather than by recompiling the
  pip lock — these are nobody's direct dependency, so a constraint is the true
  statement, and `uv pip compile` would have eaten the lock's hand-written
  header (open item #24) to move four patch versions nothing asked to move.
- **An async-generator pytest fixture cannot hold an anyio task group.**
  `ClientSession.__aenter__` opens one, and pytest-asyncio may run teardown in a
  different task, so the exit raises `Attempted to exit cancel scope in a
  different task` — *after* the assertions pass, which makes it read like a
  framework bug rather than a fixture bug. The connection helper is a plain
  `@asynccontextmanager` entered inside each test body instead.

**S6.1, second pass — running it for real**

- **Drove `guardmem-mcp` as an actual subprocess over an actual pipe**, against
  the live dev stack, which is the half of the DONE WHEN the in-memory test
  deliberately does not cover. Works: initialize at protocol `2025-11-25`, zero
  tools/resources/prompts, `subscribe=False`, pool closed on disconnect.
- **Then ran it from a different directory, and that is where the real bug was.**
  78 lines of `BaseExceptionGroup` and asyncio frames, with `9 validation errors
  for Settings` in the middle, exit 1, and the pipe already accepted. The cause
  is structural rather than cosmetic: the `Settings` read was in the lifespan,
  the lifespan runs inside `Server.run`, and `Server.run` runs inside
  `stdio_server()` — so the error had to come back out through anyio.
- **Two facts a pydantic traceback cannot convey, and both are the actual cause.**
  `Settings` resolves `.env` against the **working directory**, and an MCP client
  picks that directory — Claude Desktop does not use the repo. And the SDK spawns
  a server with `get_default_environment()`, which returns only
  `DEFAULT_INHERITED_ENV_VARS`, so `GM_DATABASE_URL` exported in my shell would
  never have reached it either. I would have spent an hour on that at S6.3.
- **`preflight()` now runs before the transport opens.** One line, exit code 2
  rather than 1 — a supervisor restarting a crashed server and a supervisor
  restarting a *misconfigured* one are different behaviours, and the second is a
  loop that never converges.
- **mypy caught a tautological assertion** — `EXIT_CONFIG != EXIT_OK` over two
  `Final` literals is non-overlapping, so it rejected the comparison. It was
  right, and the static check is the better guarantee: it holds for every caller
  rather than in one test.
- **A 107-second unit run scared me and was nothing.** `--durations` put every
  slow test in the pre-existing property suite; the new ones are ~10 ms each. The
  machine was busy with the containers and the subprocess runs. Worth the two
  minutes to check rather than assume.

**Still open**

- **THE GATE ITSELF.** Unchanged, and now with one more step built on top of it.
  S9.1 unblocks CHECKPOINT B *and* S6.2 *and* the rest of Day 6 — three reasons
  to do it next rather than one.
- **S6.3 has to reconcile two environment schemes.** `MCP_INTEGRATION.md` §1's
  config block passes `GUARDMEM_API_KEY` / `GUARDMEM_BASE_URL` /
  `GUARDMEM_DEFAULT_NAMESPACE`; `Settings` wants `GM_`-prefixed names and nine
  required fields. Those are two different contracts and the doc is the spec of
  record, so the reconciliation belongs there first.
- **`resources.subscribe` belongs at S6.4**, with the resource and the change
  feed together.
- **Appendix G — the cut order — is unwritten.** A scope decision, not mine.
- **Nothing writes an assertion.** The applier (#44) still needs an ADR.
- The four spec questions (52, 57, 61, 62), unchanged.

**Tomorrow's first step**

**S9.1 — provider adapters.** Not S6.2. Day 6 cannot continue past the skeleton
without a model client, and the same client is what lets CHECKPOINT B be run for
real. Building the gateway on an unverified scorer was already the risk the
checkpoint exists to prevent; building two more days of Day 6 on it as well
would compound it for no gain.

---

## 2026-09-14 — Day 6 · S6.2 (the four core tools)

**Shipped**

- **All four tools**, with §2.1-§2.4's schemas and descriptions copied exactly
  into `tools/schemas.py`. `memory.search` and `memory.get_entity` work end to
  end; `memory.propose` and `memory.commit` validate everything and decline.
- **`VectorStore.retired`** — without it §2.1's `excluded` could only ever have
  been an empty array. Implemented on the Postgres store and the fake.
- **`GM_MCP_TENANT_ID` / `GM_MCP_DEFAULT_NAMESPACE`**, with the tools refusing
  rather than defaulting.
- **1426 tests, 2 skipped, 99.05% coverage.** `pipeline.py`, `search.py` and
  `schemas.py` at 100%.

**What broke / what I learned**

- **I was asked to build S6.2 having twice said it was blocked, so I built
  everything that is not.** The split is not a compromise, it is the finding:
  `search` and `get_entity` need a store and an embedder, both of which exist;
  `propose` and `commit` are calls into `run()`, which needs three things that
  do not. Writing the refusal so it names all four missing dependencies at once
  matters — an operator who fixed `LLMClient` alone would hit the next one and
  reasonably conclude the work was open-ended.
- **The thing I am most sure about: no invented decision.** It would have been
  easy, and plausible-looking, to return `auto_write` with a confidence. That is
  the one output this product must never fabricate, because the entire claim is
  that a fact was governed before it was believed.
- **§2.1's `excluded` had no producer and I nearly missed it.** `search` must
  never return a retired assertion (I6), `as_of` asked about now is the live
  set, and `valid_to IS NOT NULL` is not an expressible filter. The field would
  have shipped permanently empty and looked implemented. It is the one place
  where "copy the schema exactly" forced a change to a *core protocol*.
- **Then I got `excluded` wrong in a way only real data showed.** Driving the
  seeded tenant: a search for `allergy` returned three allergies and "2 retired
  and excluded" — a `home_address` and a `preferred_pharmacy`. Scoped by
  predicate now. A footnote that is usually wrong is worse than no footnote, and
  the unit tests I had written would never have caught it, because I had written
  them against one predicate.
- **A zero vector is not an inert vector.** `get_entity` passed `[0.0] * dim`
  because "the protocol wants one and the order is not read". Cosine distance
  against zero magnitude is `NaN`, `ScoredAssertion`'s bound rejected it, and the
  *client* saw JSON-RPC "Invalid request parameters" — a server bug reported as
  the caller's mistake. Two fixes: embed the entity id, and stop letting any
  unexpected exception reach a client as a bare protocol error.
- **The fake accepted filter keys the real store refuses.** `PgVectorStore`
  raises `KeyError` on an unknown filter key; the fake matched it with `getattr`
  and returned nothing. So a typo passed the fast suite and failed the slow one.
  Exactly S1.7's rule about fakes, and it had been wrong since S1.7.
- **I hit the 400-line cap three times in one step** — `pgvector_store.py`,
  `search.py`, `test_mcp_tools.py` — and every split improved the code, which is
  the cap working. `queries.py` in particular: SQL *composition* is where
  `RULES.md` §4 could be broken, and it is now forty lines that can be read for
  that rather than four hundred.
- **And I reintroduced open item #35's exact trap**, naming a unit and an
  integration module `test_mcp_tools.py`. mypy refused the pair outright and
  pytest's collection broke. Renamed; the item was right that it would recur.

**Still open**

- **S6.3 and S6.4 are blocked behind S9.1**, same as S6.2's write half. S6.3 is
  "have a conversation that writes a fact", and nothing writes a fact.
- **THE CHECKPOINT B GATE.** Unchanged, and now with a tool surface on top.
- **An agent cannot search by a patient's name.** `subject` is a `uuid` filter;
  entity resolution is specified nowhere. This is the most user-visible
  consequence of that gap and there is now a test that says so.
- **`memory.get_entity` returns no neighbours in a fresh process** (S7.1).
- The applier (#44), and the four spec questions (52, 57, 61, 62), unchanged.

**Tomorrow's first step**

**S9.1.** For the third time, and now with three steps stacked behind it: the
CHECKPOINT B gate, S6.2's write half, and S6.3. It also needs
`GM_ANTHROPIC_API_KEY`, which is still blank.

---

## 2026-09-15 — S9.1 (provider adapters) — the first real model calls

**Shipped**

- **Three adapters behind one `LLMClient`**: Anthropic and OpenAI through their
  official SDKs, Ollama over `httpx`, as `requirements/llm.txt` specified.
- **S9.1's DONE WHEN, in CI.** One prompt, one schema, three APIs that agree
  about almost nothing, one `LLMResponse`. Mocked at the transport so the
  vendors' SDKs still do the parsing.
- **`tests/live/`**, deselected by default via a `live` marker rather than
  skipped — and driven for real against a local Ollama 0.34.0.
- **CHECKPOINT B is no longer structurally blocked.** 1380 unit and property
  tests plus 108 integration.

**What broke / what I learned**

- **The single most valuable thing I did today was run the code against a real
  model, and it took four minutes.** Every mock I had written passed. The live
  draw returned three character-identical samples at temperature 0.7, because I
  had sent one fixed seed for the whole draw and Ollama honours a seed exactly.
  `H_norm` would have been **0 on every candidate this system ever scored**, and
  §3.2 would have read that as maximum confidence. CHECKPOINT B's own failure
  list describes this exactly — "plausible numbers with no discriminative
  power ... invisible to unit tests" — and I had walked straight into it while
  building the thing that was supposed to unblock that gate.
  The unit test I added afterwards asserts the *cause* (the seeds differ),
  because a mock cannot reproduce the symptom.
- **`anthropic` 1.4.0 has no `temperature`, `top_p` or `top_k` at all.** I
  checked the SDK signature before writing rather than after, which is the only
  reason this was a design decision instead of a 400 at runtime. Sampling
  controls are gone on the current Claude models.
  The consequence is not cosmetic: §1.2's "canonical at 0, spread at 0.7" is
  **not what gets drawn on Anthropic**. K independent calls to a
  non-deterministic model still vary, so entropy is still measurable — but it is
  the model's own variance, not one this system set. An AUROC measured on
  Anthropic and one measured on Ollama are two different numbers, and the
  CHECKPOINT B sign-off now has to name the provider.
- **`LLMResponse.temperature` became `float | None`, and `seed` showed me how.**
  Its docstring already said `None` means "the provider does not support one -
  a statement about reproducibility". Temperature is the same statement. Writing
  the requested 0.7 into the audit record would have been a number no provider
  ever saw.
- **I imported two packages the workspace did not declare.** `anthropic` and
  `openai` were in `requirements/llm.txt` — the human inventory — and not in
  `guardmem-core`'s own dependencies, so they were absent from `uv.lock` and CI
  would have failed on import while every local run passed. The `types-pyyaml`
  failure from S1.7, in a new place. The deferred-dependency guard caught the
  `httpx` half; nothing could have caught the other half but looking.
- **I guessed an SDK error shape and was wrong.** `exc.body["error"]["code"]`
  always returned `None`, because `body` is the *inner* error object. The effect
  was that OpenAI quota exhaustion — which arrives on the same **429** as plain
  rate limiting — was classified as retryable. Caught only because I wrote the
  test with a realistic body rather than a hand-built exception.
- **The DONE WHEN could not be taken literally in CI**, and the honest repair
  was a marker that *deselects* rather than a `skipif`. `RULES.md` §5 wants no
  skips on main and live calls only in the nightly job; deselection gives both.

**Still open**

- **THE GATE ITSELF, STILL.** Unblocked, not run. It needs
  `GM_ANTHROPIC_API_KEY` — blank since S0.2 — and 200 hand-labelled candidates.
  **This is now the highest-value thing left in the project**: everything from
  Day 6 onward assumes the scoring discriminates.
- **S6.2's write half, S6.3 and S6.4** are unblocked on the *model* and still
  blocked on entity resolution and the applier, both of which need an ADR.
- **No OpenAI chat prices in the table.** No `GM_MODEL_*` is an OpenAI id, so
  every price would have been written from memory against a model nothing
  selects. Add them in the commit that first points a tier at OpenAI.
- **An Ollama tag is not a digest.** The adapter refuses `:latest`; `llama3.1:8b`
  can still be re-pulled to a different build, so an audit record naming it is
  weaker evidence than one naming a Claude id.
- S9.2 (routing), S9.3 (breaker + fallback) and S10.1 (the cost ledger) are what
  these adapters were built to sit under. None of them exists yet.

**Tomorrow's first step**

**Put a key in `GM_ANTHROPIC_API_KEY` and run CHECKPOINT B.** Not S9.2. The gate
has been the answer to "what is the highest-value next thing" for four steps
running, and as of today nothing but a credential is in its way.

---

## 2026-09-15 — S5.1 correction 4 (the entailment producer) + a doc-correction pass

**Shipped**

- **`llm/entailment.py` — `EntailFn`'s first and only producer.** S5.1 declared
  the callable, asked that it stay injected, and never assigned anybody to
  supply one. Nobody did, through S5.2, S5.6 and S6.2. It is the largest of
  `Deps`'s three unimplemented members: §3.2 routes `w_H = 0.35` through the
  clustering and `w_src = 0.25` through the grounding, so **0.60 of `C` was
  arriving through a callable that did not exist**.
- **Batched, not per-pair, because `entropy.py` said so.** Its docstring already
  carried the design - "precompute the pairs it needs and pass a lookup" - and
  `LLMEntailer.lookup` is that: one BALANCED call over every pair, returning a
  sync closure. `cluster_meanings` makes up to `K(K-1)` comparisons and a round
  trip each would have been the slowest thing in the pipeline.
- **Driven against a real Ollama before being believed**, and
  `tests/live/test_live_entailment.py` pins what it showed. 100% branch coverage
  on the module; 1514 tests green, 98.92%.
- **Four false documentary claims corrected** in a separate commit ahead of it.

**What broke / what I learned**

- **`PHASES_AND_ROADMAP.md` said the notebook has no appendices and that
  Appendix G "does not exist - so the one document that is supposed to say what
  to sacrifice says nothing".** PART 6 carries A-G and has since the scaffold
  commit; `git log -S` puts the cut order in `b108e48`. A 2026-09-14 correction
  had *introduced* the error while fixing a different pointer, and
  `docs/README.md` said "the same appendices A-G" the whole time. Two owned
  documents disagreed and the wrong one won for a day. **The lesson is the
  cheap one: I read the pointer instead of the notebook.**
- **"Nothing is in its way now but an API key and a labelling session" was in
  three places and was never true.** S9.1 closed one of Checkpoint B's four
  blockers, and its own log entry generalised that to all four. `deps.py` and
  `MISSING_DEPENDENCIES` both named the other three, unread. A step's closing
  claim about what remains is the least reliable sentence in the log, because it
  is written by the person who has just stopped looking.
- **The gate does not need an API key at all**, and that went unnoticed for four
  steps while the key was named as the blocker. Ollama runs it for nothing. The
  sign-off already has a provider line, which is the whole accommodation needed.
- **A repo guard caught the new model before I did.**
  `test_every_model_has_a_strategy` failed the moment `EntailmentBatch` existed:
  every `GMModel` needs a hypothesis strategy, and the walk finds models
  wherever they are defined. This is S1.6's drift guard doing exactly its job.
- **Two tests asserted the *contents* of `MISSING_DEPENDENCIES` by spelling
  them out**, so closing a gap failed them for the wrong reason - they were
  testing the shape of a sentence, not the property that every gap is named.
  Both now walk the tuple, the way `test_errors.py` walks the exception
  hierarchy. They would have broken again at the next closure.
- **The live run is the part I would not skip again.** Every unit test passes
  against a model that returns 0.7 for everything, which is CHECKPOINT B's
  headline failure. The real draw showed the property that matters: 0.95 for a
  wider claim entailing a narrower one and **0.00 for the same pair reversed**.
  Without directional discrimination, §3.1's bidirectional clustering is an
  expensive one-way test.
- **`llama3.1:8b` answers in coarse steps** - 0.00, 0.80, 0.95, 1.00 - rather
  than a smooth distribution, and 0.80 is exactly §3.1's `_SAME_MEANING` cut.
  It lands on the right side (`>= 0.8`), but a scorer whose mass sits *on* the
  threshold is worth knowing about before reading an AUROC off it. Another
  reason the sign-off has to name the provider.

**Still open**

- **THE GATE, and it is now two ADRs and one wiring change away** rather than
  four unknowns. `EntityResolver` and `CandidateClassifier` each need a decision
  written down before an implementation; the entailer needs `_score_and_decide`
  to assemble a candidate's pairs and await one lookup.
- **The harness still has no `generate`**, and the seed transcript is forty
  turns for one patient supporting 28 facts. 200 candidates needs more
  transcript - that is corpus work, not code.
- **`LLMEntailer` does not cache across calls.** Two proposals sharing a pair
  pay twice. Deliberate: a prompt-keyed cache is S9.4 and belongs in front of
  the client, not inside one caller of it.
- No OpenAI chat prices; an Ollama tag is not a digest; S9.2, S9.3 and S10.1
  unbuilt. The applier (#44) and the four spec questions, unchanged.

**Tomorrow's first step**

**ADR-0008, entity resolution**, then ADR-0009 for the classifier. Not the
orchestrator wiring - that is the one piece of the three that needs no decision,
so it is the one that can wait. `deps.py` has argued since S5.6 that a default
resolver is worse than none; the ADR is where that argument stops being a
docstring.

---

## 2026-09-15 — ADR-0008 (entity resolution binds, it does not match)

**Shipped**

- **ADR-0008.** The gap `deps.py` has called "the largest in the build" since
  S5.6 has a decision. Resolution reads an **explicit binding** and does no name
  matching: `hints.subject` when the caller names the entity, the namespace when
  it is subject-bound, and a refusal otherwise.
- **Amendments in the same commit**, per `RULES.md` §8: `MCP_INTEGRATION.md`
  §2.2 (`hints.subject` gains the description it never had — it is an entity id,
  not a name), `ARCHITECTURE.md` §5 (`:Entity.id` is derived, not opaque), both
  indexes, and `deps.py`'s docstrings, which asserted the opposite of the ADR
  the moment it was accepted.
- **No code.** The ADR is the deliverable; the implementation is the next step
  and the ADR says exactly what it is.

**What broke / what I learned**

- **The protocol signature cannot do the job, and the missing argument was in
  scope at the call site the whole time.** `resolve` has no way to supply
  `entity.type`, which is `NOT NULL`. `PredicateSpec.subject` is the entity type
  — `allergy` declares `subject: Patient` — and `_decide_one` computes `spec`
  on the line *directly above* the `resolve` call and does not pass it. The seam
  was drawn one argument too narrow and nothing could have revealed that except
  trying to write the implementation behind it.
- **The namespace already answers the question, and that reframes the problem
  entirely.** `MEMORY_ENGINE.md` documents `patient:8812 | org:acme |
  session:xyz`; `scope_of_namespace` reads the prefix; the demo tenant is seeded
  under `patient:7781` with one patient. *Which* subject a proposal concerns is
  stated by the caller before any text is read. The hard literature problem —
  is "Joan E." the same person as "Joan Ellery"? — is **not the problem in front
  of us**, and I spent the first part of this looking at it because the gap had
  been described as a matching problem for four steps running.
- **The asymmetry is what settles it.** A false *split* writes a duplicate; a
  false *merge* puts one person's allergy on another person's record — and no
  invariant here catches it. I1 holds (the span is real), I2 holds (one live
  value per predicate, on the wrong entity). For a clinical pack that is the
  worst available failure, so v1 makes it *unreachable* rather than unlikely.
- **`entity` has no `namespace` column**, so the scope `resolve` is handed is
  one storage cannot record. Resolved by encoding it in a derived id rather than
  by a migration — which matches the derived-id rule every replay path already
  follows, needs no `RETURNING`, and is race-free. The column-plus-unique-index
  alternative was close and is written down as such.
- **The seed and core would have had two id schemes.** The seed derives the
  patient from `"patient-7781"`; the resolver derives from the namespace
  `"patient:7781"` and the tenant *uuid* rather than its slug. The seed adopts
  core's derivation — one scheme, per the convention that a fact lives on the
  type that owns it — and the cost is `make dev-reset && make seed` once,
  because the derived id is the primary key.

**Still open**

- **Implementation.** Two pieces the ADR names: `resolve` gains
  `expected_type` (one call site, `orchestrator.py:309`), and something has to
  INSERT the `entity` row before the assertion's FK will accept it. The grants
  already permit it; no entity writer exists on either store protocol.
- **ADR-0009, the candidate classifier** — `pii_class` and `irreversibility`.
  The last decision between here and a runnable `run()`.
- **The entailment wiring**, unchanged: `_score_and_decide` assembles a
  candidate's pairs and awaits one lookup.
- **`checkpoint_b generate`** and a wider transcript. Unchanged.

**Tomorrow's first step**

**ADR-0009.** Then the three implementation pieces together, since they all land
in `_decide_one` and splitting them would mean touching one function three
times.

---

## 2026-09-15 — ADR-0009 (the classifier is deleted, not implemented)

**Shipped**

- **ADR-0009.** §3.3's three undeclared features turned out to be three problems
  with three different answers, not one gap behind one protocol. `scope` was
  always `scope_of_namespace`'s. `pii_class` and `irreversibility` become
  **required `PredicateSpec` fields**. `CandidateClassifier` and `CandidateRisk`
  are deleted.
- **`MEMORY_ENGINE.md` §2.1 and §3.3 amended** in the same commit, per
  `RULES.md` §8 — §2.1's worked YAML now carries both fields on all three
  example predicates, and §3.3's table says where each of the three comes from.
- **No code.** Same shape as ADR-0008: decision first, and every docstring that
  now contradicts it corrected — `deps.py`, `impact_features.py`, `inputs.py`,
  `lifespan.py`, the MCP write tools, `checkpoint_b.py`.

**What broke / what I learned**

- **The argument for the ontology was sitting in `PredicateSpec` the whole
  time.** It already declares `impact`, `min_source_tier` and
  `requires_corroboration` — three policy judgements by the deploying
  organisation, validated at load, versioned. `deps.py` justified the protocol
  by saying `pii_class` and `irreversibility` "are decisions a deploying
  organisation makes", which is *equally true of `impact`*, and `impact` feeds
  the same §3.3 score through the same `{0,.33,.66,1}` shape. No principle
  separated them. One got written down and two didn't, and the protocol was
  built around the omission rather than the omission being noticed.
- **`irreversibility` cannot be a per-candidate question, and its own docstring
  said so without drawing the conclusion.** It asks whether what an *agent did*
  on a belief can be undone — an email sent, a prescription filed. None of that
  has happened when `R` is computed. There is no observation to classify, only a
  prior to declare. I had read that docstring twice before without noticing it
  ruled out the design it was attached to.
- **This ADR removes a gap rather than filling one**, which I did not expect
  going in. Best outcome available: `run()`'s unimplemented dependencies go from
  two to one, and the one left has ADR-0008 behind it.
- **The close call was keeping the protocol as an override seam.** Rejected
  because S11.3's PII detector fits a different shape — a declared *floor* that
  evidence can raise, like `impact` already does to `R` — and because an empty
  protocol in `Deps` is indistinguishable from the outside from the
  unimplemented one that has blocked `run()` for four steps.
- **A test loses its instrument and I would not have predicted which one.**
  `test_orchestrator.py`'s concurrency probe counts overlapping scorings by
  wrapping the classifier, precisely because `_decide_one` awaits it once per
  candidate. Delete the classifier and the probe needs a new per-candidate
  await. The assertion doesn't change; the instrument does.
- **The quiet win is the audit record.** `RiskFeatures` is persisted verbatim so
  the review UI can show why something was flagged. Two of the eight numbers
  would have traced back to whatever a classifier returned; now they trace to a
  named field in a versioned file, and "why special-category?" is answerable by
  reading one line.

**Still open**

- **Implementation, now one coherent change.** `PredicateSpec` gains two
  required fields; `clinical.yaml` gains thirty lines and goes to version 2;
  `CandidateClassifier`/`CandidateRisk` and `Deps.classifier` are deleted;
  `risk_features` takes the spec and namespace; `resolve` gains `expected_type`;
  an entity writer appears; `_score_and_decide` awaits one entailment lookup.
  All of it lands in `_decide_one` and the two files either side of it.
- **§2.1's revalidation sweep does not exist** (open item #28), so the pack's
  version bump re-checks nothing. Not introduced here, but this is the first
  change that would have wanted it.
- **`checkpoint_b generate`** and a wider transcript. Unchanged.

**Tomorrow's first step**

**Implement all three decisions in one commit**, ontology fields first, because
the classifier deletion falls out of them and the other two are independent. No
more decisions are in the way of a runnable `run()`.

---

## 2026-09-15 — ADR-0008 and ADR-0009 implemented; `run()` runs

**Shipped**

- **`memory/entities.py`** — `NamespaceEntityResolver`, ADR-0008. Binds, never
  matches. Derived id, `ON CONFLICT DO NOTHING`, refuses when there is no
  binding. 14 integration tests against real Postgres.
- **`pii_class` and `irreversibility` on `PredicateSpec`**, all fifteen clinical
  predicates classified, pack to version 2. **`CandidateClassifier` and
  `CandidateRisk` deleted.**
- **The entailment wiring** — `_confidence_for` assembles every pair and awaits
  one batched lookup.
- **`run()` executed end to end against real infrastructure**: one candidate in,
  `HITL_REVIEW` out at `C = 0.837`, `R = 0.924`, a real entity row written
  through the assertion's foreign key, real Ollama answering the entailment.
  `pii=1.0 irrev=1.0` off the ontology, `scope=0.5` off the namespace.
- 1538 tests, 98.89%.

**What broke / what I learned**

- **THE GATE CANNOT RUN ON OLLAMA, and I had told myself twice that it could.**
  `ExtractedFact.verbatim` carries `maxLength: 2000`; llama.cpp's grammar
  compiler refuses the whole schema with "failed to parse grammar". Bisected
  against `llama3.1:8b`: remove that one keyword and the same schema compiles.
  `EntailmentBatch` compiles fine, which is why the entailer ran locally on
  2026-09-15 and looked like proof the extractor would. **It was not.** The fix
  is in the adapter, not the schema — a grammar is a *generation* constraint and
  the caller still validates the reply against the full schema, so dropping
  `maxLength` from the grammar loses nothing. Not done here; it is not one of
  the three decisions and deserves its own commit.
- **I deleted `EntityResolver` by accident** and the compiler told me
  immediately. Slicing `deps.py` from `class CandidateRisk` to
  `def scope_of_namespace` took out everything between, and `EntityResolver` was
  between. Ruff's "Protocol imported but unused" was the tell. A slice by
  landmark is only as good as knowing what is inside it.
- **`RULES.md` §2.4's caps fired four times and every split is better.**
  `schemas/risk.py` is the one that matters: the three enums the *ontology*
  declares now sit apart from what the *pipeline* concluded, which is a seam I
  would not have found by looking. `pipeline/per_candidate.py` is the other —
  and `orchestrator.py`'s own docstring had already described that line
  ("Layer 1 runs once for the proposal; Layers 2 and 3 run per candidate")
  before the cap made it structural.
- **`schemas/candidate.py` already existed**, so the first name for the new
  module gave a parametrised test id of `candidate.py0`. Renamed
  `per_candidate.py` before it became open item #35's third recurrence.
- **A test of mine failed in exactly the way a broken write would.** Reading the
  entity row back through a bare `pool.acquire()` with
  `set_config(..., is_local=true)` returns `None`: `SET LOCAL` outside a
  transaction block is a no-op, so RLS sees no tenant. The row was there the
  whole time. Now read through `tenant_transaction`, same as production.
- **Classifying fifteen predicates is a real exercise, not a fill-in.**
  `dietary_restriction` is special-category because a restriction proxies for
  religious belief; `preferred_language` is a quasi-identifier because language
  proxies for ethnic origin. And `weight_kg` is `impact: low` with PARTIAL
  irreversibility, because weight drives dose calculations — the one predicate
  that shows the two fields are independent by design.
- **The decision came out right and that is worth stating.** A critical allergy
  at `R = 0.924` went to HITL_REVIEW, not AUTO_WRITE. The matrix is being fed
  real declared features now and it still refuses to auto-write the thing that
  can hurt someone.

**Still open**

- **The Ollama grammar fix** — one adapter change, and the gate's cheapest route
  back.
- **`checkpoint_b generate`**, and a transcript wider than forty turns.
- **The applier (#44).** `run()` reaches a decision and still writes nothing;
  that needs its own ADR and is the last structural gap in the write path.
- **`make dev-reset && make seed` is required once** — ADR-0008 changed the
  seeded patient's entity id and the derived id is the primary key, so an
  existing database keeps its old rows.
- §2.1's revalidation sweep still does not exist, and the pack just went to
  version 2.

**Tomorrow's first step**

**Fix the Ollama adapter's grammar**, then `checkpoint_b generate`. The gate has
been the highest-value thing in the project for six steps and nothing
architectural is in front of it any more.

---

## 2026-09-15 — the Ollama grammar fix; the gate is now runnable for nothing

**Shipped**

- **`_grammar_safe` in the Ollama adapter.** Strips `maxLength`, `minLength`,
  `maxItems` and `minItems` from the schema sent as `format` when their value
  reaches 2000, recursively, at every depth. Nothing else is touched, and the
  model's own schema is not mutated.
- **The whole pipeline now runs on a local model, end to end, with no API key** —
  real extraction, real conflict adjudication, real entailment, real Postgres,
  real entity write.
- A live test against the *real* `ExtractionBatch`, six unit tests on the
  payload, and `tests/unit/test_ollama_grammar.py` split out at the cap.
- 1546 tests, 98.89%. Live suite green.

**What broke / what I learned**

- **I had the diagnosis right and the cause wrong, and only a second measurement
  showed it.** Yesterday's bisect said "remove `maxLength` and it compiles", and
  I wrote that up as *`maxLength` is unsupported*. It is not: probing each
  keyword on its own, `maxLength`, `minLength`, `pattern`, `format`, `minimum`,
  `maxItems` and the rest **all compile fine**. The real rule is a *magnitude* —
  binary-searched to a sharp boundary, **1999 OK, 2000 REJECTED**. A round
  number that sharp is a constant in the compiler, not a size blow-up.
  Had I shipped the first diagnosis I would have stripped a keyword class that
  mostly works.
- **`verbatim` sits exactly one over the line.** `max_length=2000` mirrors
  `Provenance.verbatim`, so the extraction schema was a single unit past a limit
  nobody knew existed. One character less and this would never have been found —
  and the gate would have been silently unavailable on the only free provider.
- **Clamping to 1999 was the tempting fix and is wrong.** It would forbid a
  legitimate 2500-character value under a `maxLength: 5000` schema: the model
  could not produce it and nothing would say why. Over-constraining silently is
  worse than under-constraining loudly. Dropping the keyword is sound precisely
  because `RULES.md` §3 leaves parsing with the caller — the cap moves from
  prevention to detection, not out of existence.
- **pydantic caches `model_json_schema()` and hands back the same object.**
  Mutating it in place would have stripped the cap from the *validation* this
  fix exists to preserve, in every other caller in the process. There is a test
  for that specifically, because the bug would be invisible and global.
- **The first real run scored `H_norm = 1.000`.** Three draws, three different
  answers, maximum semantic entropy — the measurement S9.1's fixed seed would
  have pinned at 0.000 forever. Seeing it vary against a real model is the first
  evidence the entropy term does anything at all.
- The decision that came out was `REJECT` at `C = 0.440`, just under `τ_lo`.
  A genuinely uncertain extraction, refused. That is the matrix working.

**Still open**

- **`checkpoint_b generate`** and a transcript wider than forty turns. That is
  now the entire distance to the gate.
- **The applier (#44).** `run()` reaches a decision and writes nothing.
- **`make dev-reset && make seed`** still owed from ADR-0008.
- An Ollama tag is still not a digest, and a 7B local model is still not Claude —
  the sign-off has to name the provider.

**Tomorrow's first step**

**`checkpoint_b generate`.** Nothing else is in front of the gate.

---

## 2026-09-15 — `checkpoint_b generate`: the harness is complete

**Shipped**

- **`scripts/checkpoint_b_generate.py`.** The gate's step 3 - "run the pipeline,
  collect `C` for each" - and the last missing piece of a harness that has been
  three-quarters built since Day 5. Reads a proposals JSONL (one conversation
  per line), runs each through `run()`, writes an unlabelled corpus.
- **Driven for real**, against a local model and a real Postgres: two
  conversations in, a corpus out, labelled by hand and fed to `score`, which
  returned FAIL with exit code 2. The whole pipe composes.
- **`GovernedCandidate`** - a candidate paired with its decision - and 14 unit
  tests. 1564 tests, 98.89%.

**What broke / what I learned**

- **A `DecisionRecord` cannot say what it is about, and that stopped the
  generator dead.** `MEMORY_ENGINE.md` §0 gives it eight fields - decision,
  reason codes, three reports, two versions, an escalation flag - and not one is
  an identity. `decide()` takes reports and thresholds, not a candidate, so
  there was nowhere for one to come from. Correct for replay, which is what §0
  designed it for; useless for a corpus, where every row needs a subject, a
  predicate, an object and a verbatim for a human to read.
  Fixed in `PipelineResult`, which is S5.6's own model rather than §0's, so no
  spec amendment was needed. **Whether §0 should carry a `candidate_id` is still
  open** and S18/S19 will force it - a HITL queue and a review UI need the fact
  beside the verdict exactly as this did.
- **The pairing is structural, not positional**, and that was deliberate.
  `decisions` and a parallel `candidates` list would have been the smaller
  change and the same bug `LLMJudge` guards against - "a short list silently
  attributes one incumbent's score to a different incumbent", except here the
  mismatch lands in front of a reviewer.
- **The shipped template ranked entropy backwards.** `auroc` reports the
  probability a kept candidate *outranks* a rejected one, so every score must be
  higher-is-better. `H_norm` is uncertainty; a raw `semantic_entropy` key would
  have reported an AUROC below 0.5 for a working entropy term and read as
  evidence against it. Rows now carry `uncertainty = 1 - H_norm`, which is both
  what §3.2 weighs and what diagnostic 1 asks for in those words. The template
  is corrected too.
- **`Turn.model_validate` on a dict from JSONL is refused**, because `GMModel`
  is strict: `"user"` is not a `TurnRole` and an ISO string is not a `datetime`.
  This is S5.6's audit-payload trap in a new place, and the fix is the same -
  `model_validate_json`. A unit test caught it, which is the only reason it did
  not surface as a confusing failure halfway through a long corpus run.
- **I diagnosed a rejection wrongly and measuring stopped me writing it down.**
  Two of four candidates came back `REJECT(SCHEMA)`, and the obvious story was
  that the clinical pack's critical predicates are `coded` and a 7B model will
  not produce RxNorm. Checked it: `allergy` with a plain `"penicillin"` is
  **admitted**. The real cause is still unknown, and it is hard to know because
  `PipelineResult.rejected` carries ids and nothing else - which is a real gap
  for a corpus workflow, where "why was this dropped" is the question.
- **The 40-turn seed transcript timed out** at a 600-second per-request ceiling
  on a local model. Not a bug; a reason the corpus wants many short
  conversations rather than a few long ones, which is also what gets it to 200.

**Still open**

- **THE GATE. It is now a corpus and a labelling session, nothing else.** Every
  dependency is implemented, every decision is taken, the harness is complete
  and runs for free on a local model.
- **`PipelineResult.rejected` is ids only**, so a generator cannot report what
  the schema gate threw away or why.
- **Whether `DecisionRecord` should carry a candidate id** - a §0 question, and
  S18.1/S19.2 will force it.
- The applier (#44); `make dev-reset && make seed` still owed from ADR-0008.

**Tomorrow's first step**

**Write the transcripts.** Twenty short conversations at roughly ten candidates
each reaches the checkpoint's 200, and nothing in the code is in the way of it.

---

## 2026-09-16 — S6.2's write half: `memory.propose` governs

**Shipped**

- **`memory.propose` runs the pipeline.** Raw text in over MCP, a real decision
  out: noise filter, K-sample extraction, span linking, schema gate, incumbent
  retrieval, conflict detection, confidence, impact, matrix. `_require_pipeline`
  and `MISSING_DEPENDENCIES` are deleted.
- **`llm/providers/selection.py`** - `build_llm`, the one place that turns
  `GM_LLM_PROVIDER` into an adapter. `checkpoint_b_generate` had its own copy;
  now there is one.
- **`tools/governing.py`** - the request composition root (`deps_for`) and
  §2.2's result mapping (`result_of`), split from `pipeline.py` at the cap.
- Four new settings, `.env.example`, and the notebook's correction 6 on S6.2.
- 1592 tests, 98.71%.

**What broke / what I learned**

- **The refusal had rotted before I touched it.** `MISSING_DEPENDENCIES` still
  named `EntityResolver`, `CandidateClassifier` and the entailment wiring after
  all three had landed, so `memory.propose` was declining with reasons that were
  no longer true - and two tests *walked the tuple*, so they passed while the
  tool lied. A refusal is a claim like any other and rots like one; the test
  that walks a list of reasons cannot tell you the list is wrong.
- **I took the read tools down and the suite told me immediately.** I made a
  missing credential fail at startup, reasoning that a server which cannot
  govern should say so at once. Every MCP integration test failed, including the
  six for `memory.search` and `memory.get_entity` - which call no model. A blank
  key is a well-formed environment with one tool unavailable, not a broken
  process. `ServerState.llm` is optional now, the absence is logged once, and
  `deps_for` refuses the two write tools by name.
- **`memory.commit` is not blocked on a missing part, and that took reading §2.3
  properly to see.** It "skips L1 extraction", so nothing samples anything, so
  §3.1's semantic entropy has no distribution to be taken over - and that term
  is `w_H = 0.35` of `C`. Reading §3.1's "H_norm := 0 when K = 1" onto a fact no
  model drew would hand every committed assertion a third of its confidence for
  free, on the one path built for high-trust payloads. The refusal now says
  exactly that. **It is an ADR, not a handler decision.**
- **I guessed the call sequence and was wrong twice.** Scripting a `FakeLLM` for
  a full `run()`, I assumed extraction came first; the noise filter calls the
  model before it, so `FakeLLM` repeated its last reply and `extract` refused
  with "asked for 3 samples and received 4". Measured it - noise, canonical,
  spread(n=k-1) - and the test helper now says so. `extract`'s refusal is
  load-bearing rather than fussy: absorbing a short sample set would *raise*
  confidence exactly when the provider was misbehaving.
- **A scripted edit failed silently again and the assertion caught it.**
  `"Five corrections to this step"` is not unique in the notebook, so an
  `s.index` anchor found the wrong step. Same failure mode as the truncation two
  days ago; the difference was asserting the count first. Anchoring inside the
  S6.2 section fixed it.
- **`applied: false` is the field I am most sure about.** §2.2's example carries
  `assertion_id` on an `auto_write`, nothing writes, and a caller reading
  `auto_write` with no further signal would conclude the fact was stored. That
  is the one output this product must never fabricate.

**Still open**

- **The applier, and its ADR.** `run()` reaches a decision and writes nothing,
  so S6.3's DONE WHEN - "see the row in Postgres with a source span, a
  confidence score, and an audit event" - is unreachable. This is the last
  structural gap in the write path.
- **`memory.commit`'s scoring question**, which is the same ADR's neighbour.
- `review_task_id` and `eta_minutes` need S18.1; `mode: async` needs S8.4.
- CHECKPOINT B: still transcripts and labels, still no code.

**Tomorrow's first step**

**ADR-0010: the applier**, and `memory.commit`'s entropy alongside it. Both are
about what happens at the transaction boundary, and deciding them together is
cheaper than twice.

---

## 2026-09-16 — ADR-0010 (the applier owns one Postgres transaction)

**Shipped**

- **ADR-0010.** The applier is a Postgres-specific composition owning **one
  transaction per candidate**: assertion, provenance, outbox event, supersession
  or merge, and the audit events. `RULES.md` non-negotiable #4 then holds by
  construction rather than by convention.
- **`VectorStore` does not change**, and that is the decision inside the
  decision. `PgVectorStore` grows connection-taking variants; the Protocol stays
  backend-neutral.
- Indexes updated, and the three code comments that said "the applier needs an
  ADR" now point at it. No implementation.

**What broke / what I learned**

- **The two halves were built to opposite conventions and both are right.**
  `VectorStore.upsert` takes no connection because a protocol that handed one
  out would be a protocol about Postgres; `audit_store.append` requires one
  precisely so the audit row commits with the state change. Neither is wrong,
  and they cannot be composed until something decides who owns the transaction.
  That is the whole ADR, and it was legible only once both existed.
- **Widening the Protocol is the cheap edit and the worst outcome.** An optional
  `connection` on `upsert` would make #4 hold on Postgres and be silently
  unenforceable on Qdrant - same call site, same green tests, a guarantee that
  evaporates where nobody looks. Widening the *concrete class* keeps §2.4's
  config-not-code seam and makes the absence of the guarantee a visible fact
  about a deployment. **A silently backend-dependent guarantee is worse than an
  explicitly unavailable one.**
- **I checked the schema before claiming it, and it argued for me.**
  `0001_initial` revokes `DELETE` on `assertion` and `UPDATE`/`DELETE` on
  `audit_event`, so the "two transactions, compensate on failure" alternative is
  not merely inelegant - the role cannot perform the cleanup that design needs.
  The schema refusing to permit a design is the schema being right.
- **A decision is not one effect**, which an applier that only knew `INSERT`
  would get wrong for most of them. A `merge` writes **no new row** - it raises
  the incumbent's `corroboration_count` - so the obvious implementation is wrong
  for §2.4's own example.
- **I corrected yesterday's own framing.** The log said the applier and
  `memory.commit`'s entropy would be decided together because both "are about
  the transaction boundary". They are not: one is atomicity, the other is
  scoring, and bundling them would hide a scoring judgement inside a storage
  ADR. `commit` is **ADR-0011**.
- **Every decision gets audited, including the ones that write nothing.** A
  `REJECT` that leaves no trace cannot be reviewed, explained, or tuned against
  - and `threshold_tuner.py` refits from exactly those outcomes.

**Still open**

- **Implementing it.** `PgVectorStore`'s connection-taking writes, the applier,
  and its call site after `run()`. The existing transaction-owning methods
  should become thin wrappers over the new ones, or the two paths will drift.
- **ADR-0011: `memory.commit`'s confidence** without a sampling distribution.
- S18.1's review task, so a `HITL_REVIEW` produces a ticket rather than only an
  audit row.
- CHECKPOINT B: transcripts and labels, still no code.

**Tomorrow's first step**

**Implement ADR-0010.** It closes open item #18 - the audit chain has been built
and tested since S5.5 and has never had a caller - and it makes S6.3's DONE WHEN
reachable, which is the Day-7 gate.

---

## 2026-09-16 — ADR-0010 implemented: the audit chain has a caller

**Shipped**

- **`memory/applier.py`.** One transaction per candidate: assertion, citation,
  outbox event, supersession, and the audit events. `RULES.md` non-negotiable #4
  holds by construction rather than by convention.
- **`PgVectorStore` split at the seam ADR-0010 named**: `prepare` (embeds,
  outside any transaction) + `write_in` / `supersede_in` (inside the caller's).
  `upsert` and `supersede` are thin wrappers, so the two write paths cannot
  drift. The `VectorStore` Protocol is untouched.
- **`GovernedCandidate` carries `subject_id` and `incumbent`** — both exist only
  inside `_decide_one` and the applier cannot write a row without the first.
- **`memory.propose` returns real `assertion_id`s**, and `not_applied` with a
  reason where it wrote nothing.
- 11 integration tests against real Postgres; 1607 total, 98.25%.

**What broke / what I learned**

- **Open item #18 is closed after eleven steps.** The chain was built, tested
  and tamper-evident at S5.5 and no write path had ever appended to it. It works.
- **The rollback test is the one that earns the design.** Superseding an
  already-retired assertion raises, the transaction rolls back, and *both* the
  successor row and the `DECISION` event that was appended first disappear with
  it. Two transactions would have left a decided-but-unwritten fact on the
  chain, or worse, a successor beside a live incumbent — two live values for a
  `ONE` predicate, invariant I2 broken by a retry.
- **I drafted the applier with merge in it and then cut it, and cutting was
  right.** §2.4's merge is an `UPDATE` of `corroboration_count`, not an insert,
  and the idempotency is not the same shape: a replayed insert is a no-op by
  derived id, a replayed **increment** is not. Getting that wrong inflates the
  one number §3.2 uses to decide a fact is corroborated — which is exactly what
  a poisoning attempt wants. It is audited as a decision, applied as nothing,
  and `Applied.reason` says `merge_not_implemented`.
- **I shaved three docstrings before finding the real seam, and the test said
  so.** `pgvector_store.py` went over the 400-line cap and I trimmed prose
  twice, getting to 424, then 400 with no margin. The failure message reads
  "Split it along a real seam rather than shaving it." The seam was there:
  `embed_batch` belongs beside `EMBEDDING_DIM` in `rowmap` (it checks against
  that constant) and `fetch_citations` beside `SELECT_PROVENANCE` in `queries`.
  381 lines, and both modules read better.
  **I had also duplicated ADR-0010's argument into three docstrings** — the ADR
  owns it, and citing it is what convention #1 asks for.
- **`valid_from` is the citation's capture time, not `now()`.** A fact proposed
  today about a conversation last week became true last week, and using the
  apply-time clock would make a point-in-time query answer wrongly for the
  window in between. There is a test for it because it is invisible otherwise.
- **A row is written invisible and stays that way.** The applier never touches
  `visible`; the relay does, after the graph side lands. Asserted, because it is
  the property that makes a partial write unretrievable rather than briefly
  wrong.

**Still open**

- **The merge path**, with its replay question answered properly.
- **ADR-0011: `memory.commit`'s confidence** without a sampling distribution.
- **S6.3** is now reachable — its DONE WHEN is a row in Postgres with a span, a
  score and an audit event, and that exists. It wants a Claude Desktop
  round-trip to close.
- S18.1's review task, so a `HITL_REVIEW` produces a ticket and not only an
  audit row.
- CHECKPOINT B: transcripts and labels, still no code.

**Tomorrow's first step**

**S6.3** — connect Claude Desktop and have a conversation that writes a fact.
It is the Day-7 gate, and for the first time every piece behind it exists.

---

## 2026-09-16 — a hardening pass over what was already there

No feature. An audit of the whole tree for the failures the gates cannot see,
which turned out to be four leaks and one silently-wrong config read.

**Shipped**

- **The dev stack was published on `0.0.0.0`.** All six port mappings used
  Compose's short `HOST:CONTAINER` form, which binds every interface. Postgres
  is `guardmem`/`guardmem`, Neo4j is `neo4j`/`guardmem123`, and Redis has no
  password at all — fine for a throwaway local stack, and on a shared network an
  unauthenticated Redis is a remote code execution primitive, not an exposed
  cache. All six now bind `127.0.0.1`; the `${VAR:-default}` override is the
  host *port* and still works. `test_every_published_port_is_bound_to_loopback`
  makes it a rule rather than a comment.
- **`build_llm` built two transports and closed neither.** The Anthropic and
  OpenAI SDK clients each own an `httpx.AsyncClient`, and `build_llm` returned a
  bare adapter, so nothing in the process held a reference that could close it.
  It is now an `@asynccontextmanager` and the composition root owns the
  lifetime. `providers/__init__.py` said "it still builds no transport" for
  eight steps; that sentence is corrected rather than deleted.
- **The lifespan is an `AsyncExitStack`.** The old `try/finally` registered each
  resource only once the *next* had been built, so a pool that opened before a
  failing `httpx.AsyncClient(...)` leaked. Registration now happens the moment a
  resource exists, and unwinding is LIFO for free.
- **Both adapters fanned out with bare `gather`, which `RULES.md` §2.2 forbids by
  name.** `gather` propagates the first failure and leaves its siblings running,
  so a draw that failed on sample 2 of 5 still issued, paid for and discarded
  the other four — against a provider that had just said it was in trouble.
  `common.draw_samples` wraps a `TaskGroup` and unwraps the `ExceptionGroup` to
  its first leaf, so `ProviderUnavailable` still arrives as itself and no caller
  changes.
- **`repr(Settings)` printed every credential in clear** — both DSN passwords,
  the Neo4j password and both API keys. Measured, not assumed. `SecretStr` on
  the three credentials, `Field(repr=False)` on the two DSNs (`SecretStr` there
  would cost `PostgresDsn`'s validation, which is what makes a typo fail at
  startup instead of mid-request).
- **`checkpoint_b_generate` silently ignored `.env`.** It read `GM_OLLAMA_URL`
  and `GM_OLLAMA_MODEL` with `os.environ.get`, and `pydantic-settings` reads
  `.env` *directly* without exporting it to `os.environ`. So a model set in
  `.env` — where `.env.example` says to set it — configured the whole process
  and was ignored by the one script whose output is the gate's AUROC. It was
  also a second composition root, which `selection.build_llm`'s own docstring
  says must not exist. Both gone: `_provider_settings` points `Settings` at
  `--provider` and hands it to `build_llm`.

**What broke / what I learned**

- **`min(n, require_samples(n))` cannot clamp.** `require_samples` returns `n`
  or raises, so the expression was `min(n, n)`. It read as a bound for eight
  steps and was never one.
- **The appendix correction was itself the false claim.** `settings.py` said
  `BUILD_NOTEBOOK.md` Appendix B "does not exist and never did; the notebook has
  no appendices". The notebook has A–G, B is "Environment variables (complete
  list)" at line 3707, `PHASES_AND_ROADMAP.md` already recorded the correction
  as reverted on 2026-09-15, and `test_docs_integrity.py` asserts all seven are
  present. `settings.py` was the one file still carrying the retracted version.
  Same lesson as last time, one layer up: the *correction* was the pointer
  nobody re-read.
- **`SecretStr` could have disabled the provider refusal invisibly.** `if not
  settings.anthropic_api_key` is what stops a blank key falling back to a local
  model, and an object without `__bool__` is always truthy. pydantic defines it
  over the wrapped value — checked, and now pinned by a test, because the
  failure mode is a silent pass.
- **Fixing the compose file broke the guard that should have caught it.**
  `test_published_ports_do_not_collide_within_a_stack` read segment 0 as the
  host port, so the new `127.0.0.1:` prefix produced five phantom collisions.
  The parser now resolves `${VAR:-default}` before splitting and counts the
  container port from the right.
- **Nothing was deleted, and that is the result.** Unused imports, dead code,
  layering and formatting are already machine-enforced every commit, so a pass
  by hand finds what the linters find: nothing. The dependency manifests were
  left alone deliberately —
  `test_every_unused_core_dependency_is_documented_with_its_step` makes an
  unused dependency a documented roadmap entry, and pruning would have deleted
  the roadmap.

**Still open**

- **`create_pool` passes no `timeout` or `command_timeout`.** Statements are
  covered — every store call passes `timeout=store_timeout_s` — but
  `pool.acquire()` can block forever once all ten connections are held, so a
  saturated server hangs rather than erroring. `RULES.md` §2.2 makes that a CI
  failure in principle. Not changed here because the value is an operational
  decision: adopting `store_timeout_s` (5s) converts queueing into failures
  under burst, and there is no load test to calibrate against. Probably an ADR.
- **The integration suite did not run** — no Docker on this machine, so the
  `lifespan()` rewrite is unconfirmed end to end. Verified instead by
  `mypy --strict`, a direct test of `build_llm`'s close semantics, and a
  fake-backed check of exit ordering including the failure path. `make test-all`
  on a machine with Docker before trusting it.
- Everything from the previous entry: the merge path, ADR-0011, S6.3, S18.1,
  and CHECKPOINT B's transcripts and labels.

**Tomorrow's first step**

**S6.3**, unchanged — connect Claude Desktop and write a fact. Nothing here was
meant to move the build forward; it was meant to make sure the parts already
built do not leak, hang or print a password.

---

## 2026-09-16 — ADR-0012, and S6.3: Claude Desktop's round trip, run

**Shipped**

- **ADR-0012: the pool bounds the wait for a connection.** `Pool.acquire()` with
  no timeout awaits `queue.get()` over `max_size` holders and nothing bounds it,
  so a process holding all ten connections stops rather than failing, with its
  transport still open. `transaction` now requires a `timeout_s` and spends it
  there. The ADR has the table that matters: `create_pool(timeout=)` is the
  **handshake** timeout (asyncpg default 60s), `acquire(timeout=)` is the wait
  for a free connection (**no default at all**), and the per-statement `timeout=`
  every call site already passes is a third thing. Conflating the first two is
  the easy way to write that fix wrong.
- **Exhaustion reports itself.** `TimeoutError` subclasses `OSError`, so the
  existing `except (OSError, ...)` already caught it - and `wait_for`'s instance
  carries no message, so an operator would have got `postgres connection failed: `
  with nothing after the colon for an incident that is not a connection failure.
  The specific clause goes first and names the wait and `max_size`. Both
  orderings are pinned by a test; both mutations were checked to fail.
- **`CONNECT_TIMEOUT_S = 60.0`**, which is asyncpg's own default written down.
  Nothing changes except that `RULES.md` §2.2 stops being satisfied by a number
  nobody in this repository chose.
- **S6.3 ran, end to end, on a local model.** `docs/MCP_INTEGRATION.md` §1.1 is
  the setup document the step asks for, and everything in it was executed rather
  than drafted: the config block, the three variables people get wrong, the
  preflight line quoted verbatim with its exit code 2, and what the round trip
  leaves in Postgres.
- **The gate is met.** Spawned `guardmem-mcp` over real stdio pipes,
  `memory.propose(mode=strict)` on `llama3.1:8b`: `preferred_pharmacy` =
  `"CVS #4021"`, span `[50, 59)` with `alignment 1.0`, `C = 0.8375`,
  `R = 0.2789`, decision `auto_write` on `C_AT_OR_ABOVE_TAU_HI` and
  `R_BELOW_RHO_LO` - and **`DECISION` and `WRITE` with identical `created_at`**,
  which is ADR-0010's one transaction visible in the data rather than argued for.
  `content[50:59]` of the submitted string is exactly `CVS #4021`.
- **The audit chain had its first real rows.** It went from **0** to 2. Every
  assertion in this database before today was written by the seed, which goes
  through the store directly; this is the first time anything was *governed*
  into it.
- **`scripts/replay_trace.py` could not connect to a correctly configured
  database.** It called `create_pool(str(settings.database_url))` with no
  `libpq_dsn`, and `GM_DATABASE_URL` is specified to carry
  `postgresql+asyncpg://` for Alembic. asyncpg answers `invalid DSN: scheme is
  expected to be either "postgresql" or "postgres"`. Four integration tests drive
  its `main()` and none could see it, because the fixture set `GM_DATABASE_URL`
  to the container's *libpq* DSN. The fixture now uses `sqlalchemy_dsn`, which is
  the form a deployment has.

**What broke / what I learned**

- **Two "bugs" I found were mine.** `memory.propose` looked like it returned
  nothing - `structuredContent` was `None`. That is the *wire* name; the Python
  SDK exposes `result.structured_content`, and reading the alias off the model
  object silently gives `None`, which looks exactly like a server that answered
  with nothing. Then `memory.search` looked like it had lost the row, because I
  filtered `payload["results"]` and the key is `assertions`. Both are now a note
  in §1.1 for anyone writing a client, and neither was a defect. Worth the
  reminder that the first explanation for "the server returned nothing" is
  usually the client.
- **The demo tenant was already seeded and I said it was not.** I read the
  tenant table through `tail -12` and the row I wanted was the one that scrolled
  off. `make seed` then printed `0 released this run`, which is what said so.
- **Searching `"pharmacy"` does not find the fact that was just written.**
  `HashEmbedder` hashes text, so ranking is near-noise; the row is found by
  predicate filter or by its exact verbatim. This is documented behaviour and it
  is still the most surprising thing about a first round trip, so §1.1 says it
  before a reader concludes the write failed.
- **Two believed `preferred_pharmacy` rows is not a cardinality violation.** The
  seed's is on subject `3c723e01…` and the new one on `8dadc187…`: "the patient"
  in a one-line proposal does not resolve to the seeded entity, and ADR-0008 is
  why that is a new binding rather than a fuzzy match.

**Still open**

- **The Phase-1 exit-gate box stays unticked.** It says "from Claude Desktop" and
  this was a programmatic MCP client - same spawned binary, same pipes, same
  `env` handling, same tool surface, but not the app's own UI. Everything under
  that last click is now evidenced.
- **Nothing runs the relay in the server configuration.** A governed row is
  durable, sourced, scored and audited, and invisible until something drains the
  outbox; I did it by hand. `services/worker` is S8.4.
- **`max_size = 10` is still an unmeasured number.** ADR-0012 makes exhaustion
  visible, which is the prerequisite for measuring it, and deliberately does not
  guess a better ceiling.
- The merge path, ADR-0011, S18.1's review task, and CHECKPOINT B's transcripts
  and labels.

**Tomorrow's first step**

**S6.4** - resources and prompts. S6.3's document is written and its gate is
evidenced; what is left of it is a click in an app, and that does not block the
next step.

---

## 2026-09-16 — S6.4: resources and prompts

**Shipped**

- **Three of `MCP_INTEGRATION.md` §3's seven resources**, the ones the notebook
  names: `guardmem://memory/{namespace}` (`text/markdown`),
  `guardmem://audit/{trace_id}` (`application/json`) and
  `guardmem://ontology/{namespace}` (`text/yaml`). Plus
  `resources/templates/list`, which §3 requires and which is the only way the
  audit URI is reachable at all - a trace id is minted per proposal, so it can
  never be *listed*.
- **S6.4's DONE WHEN is met.** Attaching the namespace resource shows the
  believed state: 27 live assertions for the demo tenant, grouped by predicate,
  each with its confidence and the verbatim span it came from, and the retired
  ones underneath. Driven over real stdio pipes against the spawned binary, not
  only in the unit suite.
- **§4's four prompts, two served and two declining** - the same split S6.2 made
  across the tools, for the same reason. `extract_memories` and
  `adjudicate_conflict` render the **pipeline's own versioned files**, which is
  the only way §4's claim (exposing the canonical prompt "reduces schema-gate
  rejections dramatically") is true rather than aspirational. `review_brief` and
  `memory_hygiene_report` are listed and refuse by name, at S18.1 and S20.x.
- **Every served prompt mints a fresh canary and returns it in `_meta`.** The
  pipeline fills that slot itself and raises `InjectionDetected` on an echo.
  Handing the prompt to a client moves that check to the client, so the token has
  to be somewhere it can read without parsing the prompt - otherwise the canary
  is decoration. A fixed one would be worse than none: guessable, and it would
  read as protection.
- **`sys.stderr` is forced to UTF-8 before logging is configured.** Found by
  reading this step's own output: the server log rendered `§` and `…` as
  replacement characters under Windows' cp1252, and there are **thirty-nine**
  section references across the refusal messages, because every one cites the
  clause it enforces. The client saw them correctly; the *log* - the copy an
  operator actually has - did not.

**What broke / what I learned**

- **§4's argument lists and the prompt templates disagree, and the templates are
  not wrong.** §4 gives `extract_memories` as `content, ontology_ref, k`; the file
  needs `content`, `ontology` and `canary`. `ontology_ref` names a pack and the
  slot wants the pack, so the ref is checked against the installed one and
  refused if it names another - substituting `clinical` for a caller who asked
  for `legal` produces candidates the gate quarantines for reasons they cannot
  see. `k` has no slot because it is not in the prompt: it is §1.2's *sample*
  count, so it is validated and returned in `_meta`. `adjudicate_conflict` is
  the same shape: §4 says `incumbent`, the template says `incumbents`, and the
  plural is right because §2.2 retrieves ten - so the published singular fills
  the plural slot and one incumbent is a set of one.
- **`resources.subscribe` was supposed to arrive here and does not.** S6.1's
  correction said its home was "the step that has both a resource and a change
  feed, and the natural home is S6.4". Half arrived: the resources exist and the
  snapshot genuinely changes, because the applier writes and the relay reveals.
  Nothing tells a live *session* - no bus, no `LISTEN/NOTIFY`, and on stdio no
  second process to hear one. It moves to **S8.4**, with the worker, and the
  capability is asserted `false` rather than left to drift.
- **A resource refusal is a protocol error and a tool refusal is not.** Opposite
  choices for a reason worth writing down: a tool's refusal is read by a *model*
  that can act on it, so it is `isError` text. A resource is attached by a
  *person* from a UI with no model in the loop, so an empty-but-successful body
  would put "this namespace has no memory" on their screen when the truth was
  "you typed it wrong". Prompts go the same way as resources, with a sharper
  edge: a refusal rendered as a *message* enters the transcript as though a
  model had said it.
- **The snapshot is a snapshot, not a listing, and that is a real limit.**
  `VectorStore` has `search` and `retired` and no "give me everything", and
  adding one is a protocol change every backend would owe. So it asks for 100
  against a deterministic vector and **says in the body** whether that was all of
  them - because a truncated panel that reads like the whole believed state is
  exactly the failure this resource exists to prevent.
- **Three fixture details I got wrong by assuming**: the builder is
  `stored_assertion` not `assertion`, `StoredAssertion` exposes `.predicate`
  directly rather than through `.claim`, and rows default to `visible=False`
  because the relay is what sets it. Each was a two-minute fix and each would
  have been a green test asserting nothing.
- **One of my own tests would have passed in CI and failed here.** It asserted
  `resources/list` was empty; that list is keyed by `GM_MCP_DEFAULT_NAMESPACE`,
  which my `.env` sets at line 188 and which CI has no `.env` to set. Exactly
  the divergence `test_dependency_consistency.py` exists because of, produced by
  me, in the same session I wrote that file's history into a report. The
  integration fixture now pins the variable empty rather than inheriting it.
- **`Namespace` is an unconstrained `NewType(str)`**, so nothing rejects a slash
  or a space in one - and a slash would have split the URI path, leaving
  `guardmem://memory/a/b` to answer quietly about `a`. `uris.uri_for` now
  encodes (leaving colons readable, because every documented namespace carries
  one) and pairs with the `unquote` in `_split`. Six namespaces round-trip in a
  test, including the two that used to break.
- **The audit resource could not be read at all on a server with no default
  namespace, and my own test found it.** `context_for` resolves a tenant *and* a
  namespace because every tool needs both; `guardmem://audit/{trace_id}` needs
  only the tenant, since a trace is keyed by tenant and trace alone - one
  proposal may write across two namespaces, so scoping its record to one would
  drop half of it. The reader was therefore refused for want of something it
  never uses, with a message about `GM_MCP_DEFAULT_NAMESPACE` that pointed
  nowhere useful. `context_for(..., require_namespace=False)` is the fix, and it
  only surfaced because the new integration fixture pins that variable empty -
  with a developer's `.env` supplying one, this would have shipped and broken
  for the first operator who did not set it.
- **S6.4 pushed `test_mcp_memory_tools.py` past the 400-line cap**, and the
  failure message is the same one that landed last time: "split it along a real
  seam rather than shaving it." The seam was there - the tools and the resources
  are different subjects asked of one session - so the session moved to
  `fixtures/mcp_session.py`, registered as a plugin rather than as a second
  `conftest.py`, and the resource tests to
  `tests/integration/test_mcp_resource_reads.py`. **Not** `test_mcp_resources.py`:
  `tests/` has no `__init__.py`, so two modules with one basename abort
  collection for the whole run, and the unit file already has that name.

**Still open**

- Four of §3's seven resources, each waiting on the feature it describes.
- `subscribe`, at S8.4.
- The Phase-1 exit-gate box, still unticked for the reason yesterday's entry
  gives: the round trip is evidenced, the click in the app is not mine to make.
- The merge path, ADR-0011, S18.1's review task, and CHECKPOINT B's transcripts
  and labels.

**Tomorrow's first step**

**S7.1** - the Neo4j `GraphStore`, behind `GM_GRAPH_BACKEND`. It is the step that
makes `ServerState.graph_durable` true, which `memory.get_entity` already reports
and which the blast-radius score reads.

---

## 2026-09-16 — S7.1: the Neo4j store, and a contract both backends answer to

Day 7 opens. `GraphStore` has had a protocol since S1.7 and one implementation
since S3.4; this is the second, and the step's DONE WHEN is not "it works" but
"the full integration suite passes against **both** backends unchanged".

**Shipped**

- **`Neo4jGraphStore`**, against `ARCHITECTURE.md` §5's model:
  `(:Entity {key, tenant_id})-[:ASSERTS {...}]->(:Entity|:Literal)`. Idempotent
  by `assertion_id` through `MERGE` on the relationship, which is the same
  replay semantics the outbox needs and NetworkX gets from an edge key.
- **`GM_GRAPH_BACKEND=neo4j|networkx`**, the flag the step names, as
  `graph/selection.py`. An **async context manager**, and that is last session's
  lesson applied rather than a preference: an `AsyncDriver` owns a connection
  pool with exactly the problem `build_llm`'s unclosed SDK client had. A test
  asserts the driver is dead after the lifespan unwinds.
- **`build_graph` yields a `BoundGraph`** - the store *and* whether it is
  durable - so `ServerState.graph_durable` is reported by the thing that chose
  the backend rather than inferred by an `isinstance` at the point of use.
- **The schema is applied at startup**: uniqueness on `Entity.key` and
  `Literal.key`, relationship indexes on `assertion_id` and `tenant_id`. The
  constraints are what make `MERGE` *correct* under concurrency rather than
  merely fast - two relay passes can otherwise each create a node for one
  subject, and no later constraint repairs that.
- **Sixteen behavioural assertions moved into one contract class**
  (`fixtures/graph_contract.py`) and now run against all three implementations:
  the fake, NetworkX, and Neo4j in the integration suite. That is what makes
  "unchanged" checkable - the Neo4j module inherits the assertions rather than
  restating them, so a behaviour that differs is a failure and not a footnote.
- **`make seed` writes to the configured backend.** It named `NetworkXGraphStore`
  directly, which was harmless while that was the only graph and would have
  become a quiet bug the moment anyone set `neo4j`: the relay would dispatch
  every event into an in-process graph that dies with the script, leaving
  Postgres seeded, the real graph empty, and no undispatched events to rebuild
  it from.

**What broke / what I learned**

- **The tenancy question has two right answers and I nearly gave the wrong
  one.** NetworkX is single-tenant and enforces it by *refusing* a second
  tenant, because the protocol's reads take an `EntityId` and no tenant so it
  has nothing to filter on. Neo4j exists precisely because that refusal is not
  an answer for a deployment - so it **scopes** instead: every read resolves the
  tenant from the node it starts at and constrains every edge it counts or
  follows. Without that, object nodes bridge tenants: two tenants recording an
  allergy to penicillin legitimately converge on one node, and a two-hop walk
  from one patient comes back with the other's edges, straight into §2.2's
  incumbent set. A cross-tenant read with no symptom. Two integration tests pin
  it; both fail if the filter is dropped.
- **A node that was only ever an *object* has no tenant**, so those reads are
  unscoped. Reachable from the contract suite and not from the pipeline, which
  only ever passes a resolved subject. The honest fix is a tenant on the
  protocol, and **S8.2** is where a request first has an authenticated one to
  thread.
- **Cypher's variable-length path takes a literal bound, not a parameter.** So
  the obvious way to write `neighbors(hops=n)` is to render `n` into the query
  text - which `RULES.md` §4 forbids and which is the one place in this module
  where the shortcut is also an injection. One parameterised `HOP` per hop
  instead, with the frontier as a list. It is also *why* the two backends agree:
  it is the same breadth-first loop NetworkX runs.
- **A Neo4j property cannot hold a map**, so `object` is stored as JSON and
  decoded on read. Reading the node's *key* back instead would turn
  `{"dose": 5}` into the string `literal:{"dose": 5}` somewhere inside a
  conflict check. And `valid_from` comes back as `neo4j.time.DateTime`, which
  pydantic rejects - `to_native()` is the conversion, and both have a test
  because both are invisible until a `ConflictReport` compares one against a
  Postgres row.
- **`_object_key` had to stop being private to NetworkX.** It decides whether a
  two-hop walk can follow an object and whether two writes of one value land on
  one node - so two copies would be two graph models, agreeing until somebody
  edited one, and the disagreement would surface as a blast-radius score that
  differs by backend. It is `graph/keys.py` now.
- **The checkpoint harness deliberately does *not* use `build_graph`.** Its old
  comment said "S7.1 is unbuilt", which was the whole reason; the reason now is
  better. A durable graph lets run N+1 see run N's edges, so `graph_fanout`
  drifts between two runs of one corpus and two AUROCs a week apart stop being
  comparable. Right call for a server, wrong one for a benchmark.

**Still open**

- **`memory.get_entity` has no graph half yet.** It reports `graph_durable` and
  returns no neighbours; now that the graph is durable, the entity card in
  `MCP_INTEGRATION.md` §3 has something real to show.
- The protocol's missing tenant argument, at S8.2.
- S7.2 (coverage to the release gate), S7.3 (MCP contract tests), S7.4 (the
  week-1 retro), and everything carried from yesterday.

**Tomorrow's first step**

**S7.2** - the coverage push. `RULES.md` §5 sets the release gate at 90% for
`guardmem-core` and the step says to look specifically at error branches,
because they are the ones that get skipped.

---

## 2026-09-16 — S7.2: the coverage push, which was not about coverage

The step says "get `guardmem-core` to >= 85%" and then, in the sentence that
turned out to be the whole task, "look specifically at error branches - they are
what you skipped."

**`guardmem-core` was already at 98.85%** when the step opened, past the 85%
interim floor and past `RULES.md` §5's 90% release gate. So the number was never
the work. Every uncovered line was an error branch or an unreachable edge case,
and most of them were mine, written in the last two steps.

**Shipped**

- **`test_graph_selection.py`.** The one that mattered: S7.1 shipped
  `graph/selection.py` and `Neo4jGraphStore._run` with **every** failure path
  unexercised. It now pins both directions - three retryable driver faults
  become a retryable `StoreUnavailable`, and a `ClientError` must **not** be
  dressed as one. That second direction is the important one: translating a
  Cypher bug into an outage tells the relay to retry it until `attempts` reaches
  the cap, which manufactures a poison event out of a typo.
- **`test_llm_selection.py`.** `llm/providers/selection.py` was the
  least-covered module in the package at **61%**, both SDK arms unexercised -
  and it is the module that decides *which model answers*, which §3.1's entropy
  makes a claim about every audit record. Constructing either SDK client opens
  no connection, so the arms, the adapter types and the transport-closing are
  all checkable offline.
- **`test_store_query_builders.py`.** `retired_statement`'s guard, whose failure
  mode is the quiet kind: a caller passing an `as_of` gets rows filtered on two
  contradictory temporal conditions, and an empty result reads as "nothing was
  retired" rather than as a mistake.
- **Provider error branches**: anthropic rate-limiting, and "nothing answered at
  all" on both adapters - the case no status code can express, because a 5xx
  means the provider is there and unwell while a connect error means it is not
  there. Both retryable, both reaching the caller as one domain error, or a
  breaker has to learn two shapes for one outcome.
- **A `usage`-less OpenAI response** reports zero tokens and `cache_hit=False`.
  "Not reported" and "not cached" are different facts and only one fits in a
  boolean; `PRD.md` §6.5's ≥40% cache-hit assumption is read off that field, so
  guessing `True` would inflate the one number the cost model rests on.

**What broke / what I learned**

- **`Neo4jGraphStore.degree` had defensive code that could not run.**
  `if not rows: return 0` in front of `rows[0]`. Cypher's `count()` is an
  aggregate with no grouping key, so it returns one row even when the `MATCH`
  finds nothing - verified against 5.26, which answers `[{'degree': 0}]` for a
  node that does not exist. **Unreachable defensive code is worse than none**:
  it reads as a handled case and is a branch no test can ever cover. Deleted.
  The unknown-entity test still passes, and if `DEGREE` ever stops aggregating
  it now raises `IndexError` rather than returning a zero from a query that
  quietly stopped answering.
- **`_verify` had two `except` clauses with byte-identical bodies**, which is a
  branch no test can tell apart. Collapsed - but the reason it names two base
  classes is worth keeping: `Neo4jError` and `DriverError` are **disjoint**,
  meeting only at `GqlError`. `ServiceUnavailable` is a `DriverError`;
  `TransientError` is a `Neo4jError`. Catching either alone lets half the
  failures escape as bare driver exceptions, past the `StoreUnavailable`
  contract every caller upstream is written against.
- **Unit-only coverage numbers are a trap for this exact task.** Reading them
  first, `applier.py` looked like 39% and `relay.py` like 36% - both are covered
  by the integration suite, and the Makefile already says so in as many words.
  Chasing those would have been an hour spent adding tests for things already
  tested. The authoritative number is `make test-all`.
- **The import contract had stopped being complete and still passed.**
  `pyproject.toml` forbids `pipeline/` from importing `networkx_store`,
  `pgvector_store`, `networkx` and `asyncpg`, and its comment said "both
  concrete stores and both of their drivers". S7.1 added a third of each. The
  rule would have gone on passing while the thing it protects had a new way to
  be broken. Widened, with a note that a new backend means two more lines here.
- **S7.1 pushed `seed()` past the 50-line body cap.** Same lesson as the module
  cap two steps ago: split at a seam rather than shave. A graph whose lifetime
  has to be opened and closed is a unit of work, so `drain_the_outbox` is now
  its own function.

**Still open**

- S7.3 (MCP contract tests over every tool's schemas) and S7.4 (the week-1
  retro), then the Week-1 exit gate.
- `memory.get_entity`'s graph half, now that the graph is durable.
- Everything carried: the merge path, ADR-0011, S18.1, CHECKPOINT B's
  transcripts and labels.

**Tomorrow's first step**

**S7.3** - validate every tool's `inputSchema`/`outputSchema` with `jsonschema`
and assert no drift. `tests/contract/` has been an empty directory with a
`.gitkeep` since S1.1 waiting for it.

---

## 2026-09-16 — S7.3: the tool contract, and who actually enforces it

    "Validate every tool's inputSchema/outputSchema with jsonschema; assert no
    drift."

`tests/contract/` has held a `.gitkeep` since S1.1 waiting for this. The
interesting half of the step was the one S6.2 deferred *to* it by name.

**Shipped**

- **`tools/outputs.py`** - an `outputSchema` for the three tools that return a
  payload. S6.2 left them out with a reason: "§2.1-§2.4 publish *example*
  results rather than schemas, so writing them here means inventing a contract
  the spec of record does not state." The resolution is that every schema is
  **read off the handler that produces it**, and the contract suite validates
  real handler output against it - so it transcribes a contract rather than
  inventing one, and cannot drift from the handler without a test failing.
- **`memory.commit` still has none**, for S6.2's reason unchanged: it declines
  in this build, so it returns no `structuredContent`, and a schema for a
  payload nothing produces is a contract nobody can check. `DECLINES` names it,
  so growing a payload without a schema fails a test.
- **Two layers of contract test.** `test_tool_schemas.py` checks every schema
  against the JSON Schema metaschema, checks the schema against the *code that
  enforces it*, and validates real payloads. `test_tool_result_validation.py`
  drives a real session over real Postgres and lets **the SDK's own validator**
  do the checking.
- **`types-jsonschema` pinned in all four dependency artifacts**, which is the
  convention `types-pyyaml`, `asyncpg-stubs` and `types-networkx` already set.
  `jsonschema` ships no `py.typed`, and the `ignore_missing_imports` alternative
  is the override S3.2 deleted for `asyncpg` after watching it hide a real
  annotation error - it would have turned every validator call into `Any` in the
  one module whose whole job is checking shapes.

**What broke / what I learned**

- **An `outputSchema` is not documentation - the CLIENT enforces it.**
  `mcp/client/session.py` compiles one validator per tool from `tools/list` and
  checks every successful `structuredContent`. So declaring one is a real
  guarantee, and also a real risk: a schema that were wrong would break callers
  rather than tests. That is why they are read off the handlers and why real
  payloads are validated against them in two places.
- **A tool that declares a schema and returns nothing raises**, with "has an
  output schema but did not return structured content". Checked the SDK before
  trusting it: the validation is gated on `not result.is_error`, so refusals are
  exempt - which is the only reason declaring a schema on `memory.search` does
  not turn every refusal into a protocol error. There is a test pinning that
  this server's refusals really do carry `isError`.
- **`additionalProperties` is open on the wire and closed in CI**, deliberately.
  A published schema that forbade unknown keys would make *adding* a field a
  breaking change for every existing client, which is backwards for a wire
  format. Drift still has to fail, so the exact key set is asserted in the
  contract suite instead. Loose on the wire, strict in CI.
- **The drift worth catching is not in the schemas, it is between them and the
  code.** `limit`'s ceiling, its default, `min_confidence`'s floor, the token
  budget, the `source_tier` enum, the `decision` enum - every one is written
  twice, once where a client reads it and once where a handler enforces it. Two
  copies of a fact is a fact that can disagree with itself, and this
  disagreement is the silent kind: a client trusting a default the handler does
  not apply gets different results than it asked for, with nothing raising.
- **Mutation-checked before believing them.** "The assertion is that nothing
  raises" is only a test if something *would*. Adding one required key the
  handler never returns failed six tests across both layers.
- **S6.2's deferral was pinned by a test, and that is why the reversal was
  safe.** `test_no_tool_publishes_an_output_schema` asserted the absence and its
  docstring named S7.3 as the step that owned the question - so adding the
  schemas failed it, loudly, in the right place. Updated rather than deleted,
  the way S6.1's "lists zero tools" was at S6.2: it now pins *which* tools have
  one, and the schemas themselves are `tests/contract/`'s to own.

**Still open**

- S7.4, the week-1 retro, and then the Phase-1 exit gate.
- `memory.commit`'s schema, with its implementation.
- Everything carried: the merge path, ADR-0011, S18.1, and CHECKPOINT B.

**Tomorrow's first step**

**S7.4** - the retro, and the question it asks honestly.

---

---

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
