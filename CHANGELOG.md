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

- **S2.1 — the Layer-1 noise filter.** `pipeline/l1_extract/noise_filter.py`
  and `noise_rules.py`: the five drop classes of `MEMORY_ENGINE.md` §1.1, with
  deterministic rules settling the cheap majority and the ambiguous remainder
  going to a FAST-tier classifier in **one batched call**. Measured on the
  golden corpus: 17 rule drops at precision 1.000, 30 of 40 turns settled with
  no model call — §1.1's "cheap 70%", measured rather than assumed.
- **Two of the five classes are deliberately under-detected**, because the
  machinery to decide them does not exist yet. §1.1 defines a restatement at
  cosine ≥ 0.93 (the embedder is S3.2) and a third-party claim by the absence of
  an ontology licence (S3.5). Approximating a semantic threshold with a lexical
  one is the confusion §3.1 warns about, so each rule fires only on the part it
  can decide soundly — an exact echo; no third-party drop at all — and routes
  the rest to the classifier. `rapidfuzz` appears only as *routing* triage, and
  never decides a drop.
- **`schemas/turn.py`** — `Turn`, `TurnRole`, `NoiseReason`, `DecidedBy`,
  `DroppedTurn`, `NoiseResult`, plus `TurnId` in `types.py`. S2.1's snippet uses
  `Turn` and `NoiseResult` without defining them anywhere in the spec suite. The
  drop record carries a reason and the tier that decided it, because §1.1 is
  emphatic that dropped turns are sampled into the funnel — "you must be able to
  see what the filter is eating" — and an over-eager *rule* is a lexicon edit
  while an over-eager *classifier* is a prompt change and an eval run.
- **`prompts/loader.py` and `prompts/classify_noise/v1.md`** — versioned prompt
  files with frontmatter, per `RULES.md` §3. The notebook introduces `render()`
  at S2.2, but S2.1 is the first step that sends a prompt to a model, so the
  rule binds here. Frontmatter is parsed as flat `key: value` and **refuses**
  anything richer rather than guessing; `pyyaml` stays out of the package
  manifest until S3.5, the step that reads real YAML. Validation is routed
  through JSON so `GMModel`'s `strict=True` does not reject a file whose every
  value is text, and so `extra="forbid"` reaches the frontmatter — a misspelled
  key there is a load error, not an ignored line. The `.md` files were confirmed
  present in the built wheel, the way `py.typed` was at S1.5.
- **The prompt's declared tier is load-bearing.** `filter_noise` routes its call
  on `spec.tier` rather than a literal, so a prompt authored for FAST cannot be
  put on FRONTIER by an edit at the call site.
- **Canary spotlighting on the classifier call.** Untrusted turns are delimited
  and the prompt states they are data; a canary in the model's output raises
  `InjectionDetected`, which `RULES.md` §3 treats as confirmed rather than
  suspected. A fresh canary per call, asserted by test.
- **"Fail closed" means *keep* in Layer 1**, and the module says so. An
  unparseable reply, a verdict for a turn that was never sent, a turn answered
  twice, a drop with no reason, and a turn never answered for all resolve to
  keeping — a kept turn stays inside governance, and dropping is the only
  irreversible act the filter can perform. A provider failure is the exception
  and propagates untouched: the extractor needs the same provider two steps
  later, and `ARCHITECTURE.md` §4 already parks the proposal on a dead one.
- **`tests/fixtures/noise_corpus.py`** — forty hand-labelled turns as one
  continuous clinical intake call, because a restatement is only definable
  against what came before. Labelled before the rules were run, and containing
  three drops the rule tier cannot catch, so the corpus measures the
  implementation rather than reflecting it. The gate asserts precision ≥ 0.95
  *and* a recall floor, since precision alone is free for a filter that drops
  nothing.

### Changed

- **The property suite's coverage guard is now satisfied by construction.**
  `ANY_SCHEMA` is a `st.one_of` over the registry, and `one_of` weights its
  branches by the entropy each consumes — so per-model depth falls as the layer
  grows. Seven new models pushed two *existing* ones below the sampling floor at
  500 examples and the guard failed, correctly, for a reason having nothing to
  do with either of them. Raising the example count buys time, not a fix: the
  same failure returns at every step that adds a schema. The union keeps
  supplying depth; a short second pass over a new `EVERY_SCHEMA` — a tuple of
  every strategy, so one example is one draw of each — supplies breadth.
- **Three `RULES.md` §2.4 size limits were breached while building this and paid
  down in the same commit**: the lexicons pushed `noise_rules.py` over the
  400-line module cap, `filter_noise` over the 50-line function cap, and
  `tests/fixtures/strategies.py` over 400 as well.
- `CONTRIBUTING.md` still opened "Status: pre-implementation … `guardmem_core`
  is still an empty package", four steps stale. Corrected.

- **S1.7 — the three infrastructure protocols.** `llm/base.py` (`Tier`,
  `LLMResponse`, `LLMClient`), `memory/vector/base.py` (`VectorStore`) and
  `memory/graph/base.py` (`GraphStore`). Structural typing per `RULES.md` §2.1,
  so no provider or driver SDK is ever a dependency of the decision engine.
  Neither `Tier` nor `LLMResponse` is defined by the step; the first is
  `ARCHITECTURE.md` §2.8's ladder, the second carries everything `RULES.md` §3
  requires be recorded of a call — except `prompt_version`, which the client
  cannot know, since it is handed a rendered string and the caller chose the
  file. Deliberately **not** `@runtime_checkable`: that compares method names
  and ignores signatures, arity and async-ness, so it reads like a guarantee
  while checking almost nothing.
- **`tests/fixtures/fakes.py`** — `FakeLLM`, `FakeVectorStore`,
  `FakeGraphStore`, how every unit test runs without Docker for the next four
  days. Fakes rather than mocks, because three of the contracts are
  behavioural: `search` hides tombstoned and invisible rows (invariant I6),
  `supersede` retires rather than deletes, and `upsert` is idempotent by id for
  the outbox replay S3.3 requires. `tests/unit/test_fakes.py` holds them to all
  three — the step asks only that they typecheck, but a fake that permits what
  pgvector forbids makes the whole week-1 suite a measurement of the wrong
  system, silently and with every test green.
- **`tests/fixtures/strategies.py`** — the hypothesis strategies moved out of
  the property module, which was at the 400-line cap with three quarters of it
  being data generation. Ids are now `NewType`-typed rather than bare `str`.
- **The vector store owns write-side embedding**, recorded on the protocol
  because the two signatures otherwise disagree: `upsert` takes assertions,
  `search` takes a vector, and `StoredAssertion` carries no embedding field.
  `BUILD_NOTEBOOK.md` §0.4 names `pgvector_store.py` as the owner module for
  embeddings and `GM_EMBED_MODEL` arrives at S3.2, which is that store.
- **S1.6 — the Pydantic schema layer.** `guardmem_core.schemas`, seven modules
  and 16 models: `base` (`GMModel` plus the shared `ObjectValue` alias),
  `candidate`, `entity`, `verdict`, `policy`, `receipt`, `review`. Every model
  is `extra="forbid"`, `frozen=True`, `strict=True` and `validate_default=True`,
  built on the `NewType` ids from S1.5 rather than the spec's bare `str` —
  `MEMORY_ENGINE.md` §0 and `RULES.md` §2.1 disagree only in spelling, and the
  stricter reading is free because `NewType` erases at runtime. Validators
  where a malformed value would otherwise fail silently downstream: a source
  span must be a real half-open interval (a zero-width one satisfies a `NOT
  NULL` constraint while quoting nothing), a validity interval may not run
  backwards (an inverted one intersects nothing, so the temporal-overlap check
  in §2.2c never fires), and an obligation must be a known `ObligationKind` (an
  unrecognised one is inert at S5.4, leaving the auto-write path open with
  nothing in the record to show for it).
- **`ReviewTaskId` and `ReviewerId`** — added to `types.py` by the step that
  introduced review tasks, as its docstring said they would be.
- **`tests/property/test_schemas.py`** — 500 generated examples asserting that
  every model round-trips through `model_dump_json` → `model_validate_json`,
  rejects an unknown field, and refuses assignment. Deliberately not
  parametrised across the models: hypothesis costs ~0.45 s per test to set up
  regardless of example count, which measured at 198 s for the file, against
  13 s when drawing from a union of all sixteen strategies. The run records
  which types it produced and fails if any was missed, so the coverage stays a
  guarantee rather than a claim about probability. A registry test fails when a
  schema is added without a strategy.
- **`tests/unit/test_schema_models.py`** — the example-based half: every
  validator branch, and the pydantic behaviours surprising enough to pin.
  `strict=True` still widens an `int` to a `float`, so `object=500` stores
  `500.0`; `frozen=True` makes a model hashable only while every field is;
  and an `AuditEvent.payload` holding a `datetime` validates, serialises to a
  string and comes back a string — which would break invariant I5 for every
  later link in the chain, so S5.5 has to handle it. The name is not
  `test_schemas.py` because `tests/` has no `__init__.py`: two test modules
  with one basename fail collection for the whole run.
- **`conftest.all_schema_models()`** — the recursive walk from `GMModel`,
  shared by the suite that checks every schema has a strategy and the one that
  checks every schema is exported.
- **`tests/conftest.py`** — `REPO_ROOT` defined once instead of copy-pasted into
  five test modules, and resolved by walking up for the `pyproject.toml` marker
  rather than by a `parents[2]` index that silently points outside the
  repository if a test file moves.
- **S1.5 — domain types and the error hierarchy.** `types.py` with six `NewType`
  ids, and `errors.py` with `GuardMemError` plus the seven subclasses
  `RULES.md` §2.3 names. Each carries `code`, `http_status`, `mcp_code` and
  `retryable` - the MCP code included because §2.3 asks for the HTTP and MCP
  mapping "in one table", which the step's snippet omitted, leaving
  `MCP_INTEGRATION.md` §6 as a second source for the MCP server to re-derive.
- **`py.typed`** — the PEP 561 marker was absent, so every consumer of
  `guardmem-core` saw the package as untyped and the `NewType` ids collapsed
  back to `str` outside the package. `make typecheck` cannot see this, because
  it checks the package's own source where annotations are visible regardless.
  Confirmed present in the built wheel, not only the editable install.
- **S1.4 — typed settings.** `guardmem_core.settings` with 21 fields: DSN-typed
  store URLs, `Literal` environment, bounded numerics, and validators for
  threshold ordering and the Neo4j URI scheme. `frozen`, `strict`,
  `extra="forbid"`, and an explicit `env_file_encoding="utf-8"` so a `.env` does
  not parse differently on Windows and Linux. Built lazily through an
  `@lru_cache` `get_settings()` plus a PEP 562 module `__getattr__`, so
  `from guardmem_core.settings import settings` still fails loudly on bad
  configuration while importing the `Settings` class stays side-effect free -
  without which CI, which has no `.env`, could not import the module at all.
  `tests/unit/test_settings.py` covers it to 100%, branches included, and pins
  `.env.example`'s active keys to the declared fields.
- **ruff `known-first-party`** — `guardmem_core` is installed editable, so isort
  classified it as third-party and interleaved it among pytest and pydantic.
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

- **`make typecheck` could not see the test suite, and 19 errors were hiding
  there.** The target ran `mypy` over `packages/guardmem-core/src` alone, which
  made S1.7's DONE WHEN — "fakes satisfy the protocols under `mypy --strict`" —
  impossible to verify, since `Protocol` is structural and nothing at runtime
  notices a signature mismatch. Extended to `tests`. Fourteen of the errors were
  a raw `str` passed where a `CandidateId` or `TenantId` was declared: exactly
  the mistake `RULES.md` §2.1 introduced those types to prevent, inside the
  suite whose job is to prove the layer holds.
- **The "every schema is covered" guards depended on import order.**
  `__subclasses__()` only sees classes that have been imported, so
  `all_schema_models()` was answering "every model some test happened to pull
  in". S1.7 proved it: `LLMResponse` is a `GMModel` in `guardmem_core.llm.base`,
  nothing imported that module, and all four guards passed while not covering
  it. `conftest` now walks and imports the whole package first — which is only
  safe because S1.4 made settings construction lazy.
- **Protocol bodies would have broken the coverage gate.** A `...` body is a
  statement that never executes, because nothing calls a Protocol. Added a
  narrowly scoped `exclude_also` rather than letting the number drift or the
  floor drop.
- **Both READMEs claimed the build was three steps behind where it is.** The
  root `README.md` still opened "Status: pre-implementation … there is no
  runtime code yet", and both it and `docs/README.md` named S1.3 as the next
  step after S1.4 and S1.5 had shipped. Now stated as S1.1–S1.6 complete, with
  the boundary spelled out: the typed foundation exists and no pipeline stage
  does.
- **`PROJECT_TREE.md`'s `schemas/` listing did not match what the layer holds.**
  It named `Predicate` in `entity.py` (it is ontology content and arrives at
  S3.5), `PolicyPack`/`Rule` in `policy.py` (S12.2), and no `base.py` at all.
  Corrected, with the deferrals labelled by the step that lands them.
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
