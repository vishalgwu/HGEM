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

- `hooks` has not yet gone green in CI. Verified in a Linux container against
  the actual commit before pushing this time.
- CI warns `actions/checkout@v4`, `actions/cache@v4` and `astral-sh/setup-uv@v5`
  run on deprecated Node 20. Worth bumping; not urgent, and deliberately not
  bundled into a fix commit.
- Branch protection on `main` (S0.3); API keys blank in `.env`.

**Tomorrow's first step**

`S1.3` — the docker-compose dev stack.

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
