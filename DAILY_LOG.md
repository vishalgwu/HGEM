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
