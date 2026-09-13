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

- **S3.2 — the pgvector store.** `memory/vector/pgvector_store.py` implements
  `VectorStore` over the S3.1 schema, with `pool.py` (the process-wide asyncpg
  pool, `vector` codec registered per connection) and `rowmap.py` (the shape of
  `assertion` and `provenance` in both directions, and the SQL that fills it)
  beside it. Writes land `visible = false` as a statement literal, so there is
  no parameter through which a caller could make a partial dual write
  retrievable; every read filters `visible` and `retracted_at IS NULL`; nothing
  deletes.
- **`as_of` on `VectorStore.search`.** A protocol change, made here because the
  step's DONE WHEN requires a point-in-time query and `MCP_INTEGRATION.md` §2.1
  already publishes the parameter. It is **valid** time —
  `valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)`, half-open —
  because supersession sets `valid_to`. The system axis
  (`recorded_at`/`retracted_at`) answers a different question and belongs with
  `memory.timeline`.
- **An `Embedder` protocol**, injected rather than constructed. §0.4 makes the
  store the owner of write-side embedding, but `guardmem-core` importing a
  provider SDK would invert the dependency `LLMClient` exists to prevent.
  `FakeEmbedder` is the deterministic double: hash in, unit vector out.
- **The tenant is bound at construction**, and every statement runs inside a
  transaction with `SET LOCAL app.tenant_id` — the value S3.1's RLS policies
  read. `SET LOCAL` specifically: it reverts at COMMIT, so a pooled connection
  cannot carry one tenant's setting into the next checkout.
- **Testcontainers, and the integration suite CI actually runs.** S3.1 deferred
  this and said so. `tests/fixtures/postgres.py` starts the pinned `pgvector`
  image, mounts the repository's own `infra/docker/initdb/` into it, and applies
  the schema with `alembic upgrade head` as a subprocess — so the database under
  test is provisioned by the files that ship, not by a copy written for tests.
  The fifteen S3.1 invariant tests stop skipping, `ci.yml` gains an
  `integration` job, and `RULES.md` §5's "green with no skips on main" has
  something enforcing it. Coverage with the integration suite is 100%.
- **`tests/integration/test_pgvector_store.py`** — sixteen tests, including the
  DONE WHEN in full. Three deliberate mutants were each killed by exactly the
  test that claims to cover them: ignoring `as_of`, dropping `visible` from the
  filter, and `DO UPDATE` in place of `DO NOTHING`.
- **`asyncpg-stubs`.** S3.1 deferred it on the belief that regenerating
  `requirements.lock.txt` would drag seventeen unrelated packages forward.
  Measured at S3.2: it moves nothing. `uv pip compile` honours the pins already
  in its output file, so the seventeen were an artefact of resolving from
  scratch. The `ignore_missing_imports` override for `asyncpg` is gone, and it
  immediately caught a real annotation error — `Pool.acquire()` yields a
  `PoolConnectionProxy`, not a `Connection`.
- **S3.1 — the initial migration.** `infra/migrations/alembic/versions/0001_initial.py`:
  eight tables, the bitemporal columns, both partial retrieval indexes,
  row-level security on every tenant-scoped table, and the grants that make
  `RULES.md`'s non-negotiables properties of the *database* rather than promises
  of the application. `make migrate` runs clean from an empty volume.
- **Provenance is its own table.** `ARCHITECTURE.md` §5 sketched `source_hash`
  and `source_span` as columns on `assertion`, and `schemas/entity.py` already
  said S3.1 would settle it. It has to be a list: `MEMORY_ENGINE.md` §2.4
  resolves a duplicate by *appending* a `Provenance` and bumping
  `corroboration_count`, and §3.2's `S_cor` is a function of independent
  sources — a column pair cannot represent a corroborated fact at all, so S3.2
  would have had to split the table one step later. §5 is updated to match.
- **"No unsourced write" survives the split as a deferred constraint trigger.**
  With no column to mark `NOT NULL`, `RULES.md` §1.1 becomes "every assertion
  has at least one provenance row", checked at COMMIT — deferred because the
  assertion and its citations are written in one transaction, which the outbox
  pattern already requires.
- **`infra/docker/initdb/02-app-role.sql`** — the `guardmem_app` role the
  revokes target. Roles are cluster state and grants are schema state, so the
  role is created by the deployment (initdb in dev, Terraform in prod) and the
  migration raises if it is missing rather than skipping the revoke.
- **`tests/integration/test_migration_invariants.py`** — 15 tests turning S3.1's
  three manual DONE WHEN commands into assertions, alongside the four
  non-negotiables the schema now carries. Skips cleanly when no database is
  reachable; testcontainers arrives at S3.2.
- `alembic.ini` and `infra/migrations/alembic/env.py`, with the connection
  string read from `Settings` rather than the ini — `RULES.md` §2.4 wants one
  settings object, and a DSN in a tracked file is the one that ends up pointing
  at the wrong database.

### Changed

- **`supersede` raises `ConcurrencyConflict` on a zero-row `UPDATE`** rather
  than passing quietly, and `FakeVectorStore` was changed to match. The step's
  snippet is silent when the incumbent is already retired — but that result is
  the only place a transposed `supersede(new, old)` can ever surface, since both
  arguments are `AssertionId` and nothing static tells them apart. It is also
  the right answer for a cross-tenant call, which RLS makes match nothing.
- **`make test` and `make test-all` now mean different things on purpose.** The
  first is unit and property only — seconds, no Docker, the inner loop and the
  CI `gates` job. The second includes the integration suite and is the only
  target whose coverage number is real.

### Fixed

- **A `CHECK` on the provenance span passed when it should have failed.**
  `int4range(5, 5)` is an *empty* range; `lower()` and `upper()` return NULL on
  one; and a `CHECK` that evaluates to NULL **passes**. A zero-width span —
  which `Provenance` refuses in Python and §1.1 treats as no span at all — was
  being stored. `NOT isempty(source_span)` is the fix, found by running the
  constraint rather than by reading it.
- **Row-level security did not apply to the table owner.** `ENABLE ROW LEVEL
  SECURITY` exempts the owner from its own policies, and the migration runs as
  the owner — so the obvious "SELECT and see" check would have shown every
  tenant's rows and looked like proof that isolation worked. `FORCE ROW LEVEL
  SECURITY` closes it.
- **Tenant isolation raised a 500 where it should have returned nothing.** A
  session that sets `app.tenant_id` and then `RESET`s it reads back the empty
  string, not NULL, and `''::uuid` raises `invalid input syntax`.
  `NULLIF(current_setting('app.tenant_id', true), '')` collapses unset and empty
  to NULL. A *malformed* tenant id still raises, deliberately: unset is silence,
  malformed is a bug in tenant propagation, and swallowing it would make "this
  patient has no memories" the symptom of a broken caller.

### Changed

- `.env` points at `5433 / 6380 / 7688`, the ports the dev stack actually
  publishes on this machine. `.env.example` keeps the documented defaults —
  the shift is a local collision with another checkout, not a project decision.
- `tests/unit/test_source_limits.py` covers `infra/` now that it holds Python,
  and exempts alembic revisions from **both** line caps. Recorded honestly: the
  first version of that exemption covered the module cap only, arguing the
  function cap "is about complexity" — measuring showed the argument cuts the
  other way, since `_belief_tables` is 59 lines because two `CREATE TABLE`
  statements are 59 lines and its cyclomatic complexity is 1. `C901`, which is
  the actual complexity gate, still applies to migrations.
- `asyncpg` has no `py.typed`, so `mypy --strict` cannot check
  `tests/integration/`. A per-module override carries the justification.
  `asyncpg-stubs` is the better fix and is **not** taken yet: adding it means
  regenerating `requirements.lock.txt`, and `uv pip compile` today resolves
  seventeen unrelated packages forward — a reviewed change with its own commit,
  not a side effect of wanting types for one test module.

### Fixed

- **CI had been red for six commits, and every local run was green.** `mypy
  --strict` failed in CI on `tests/unit/test_compose_stack.py`'s `import yaml`
  with "Library stubs not installed". The cause was not the code: `types-pyyaml`
  was pinned in `requirements/dev.txt` and **not** in `pyproject.toml`'s dev
  group, so it reached a developer's machine (which installs from
  `requirements.lock.txt`) and never reached `uv.lock` (which is what CI
  installs from). It went unnoticed until S1.7 widened `make typecheck` to cover
  `tests/`, and from then on every push was red.

  Five more packages had drifted the same way — `pip-audit`, `pytest-xdist`,
  `schemathesis`, `jsonschema`, `mutmut`. All six are now in the dev group, and
  `uv.lock` is regenerated. `pyproject.toml` already stated the intent in as
  many words: the dev group "carries EXACT pins mirroring requirements/dev.txt".

- **The guard that should have caught it checked only the harmless direction.**
  `test_dev_group_matches_requirements_dev` asserted that dev-group entries
  appear in `requirements/dev.txt`. The direction that breaks CI is the reverse:
  a package the developer has and CI does not. It is now symmetric, and renamed
  `test_dev_group_mirrors_requirements_dev`, because mirroring is symmetric.

- **The `uv.lock` ⊆ `requirements.lock.txt` guard reported a false positive, and
  a false positive is how a guard gets relaxed.** `uv.lock` is a *universal*
  lock carrying entries for every interpreter its resolution markers cover, so
  `libcst` lists `pyyaml-ft` under `python_full_version == '3.13.*'` — which this
  project, pinned to 3.12, can never install. The guard now evaluates PEP 508
  markers against the interpreter `.python-version` names, and walks reachability
  from the root rather than taking every `[[package]]` block. It also parses
  `uv.lock` as TOML instead of matching `name = "..."` immediately followed by
  `version = "..."` with a regex — true of the file today, not a property the
  format guarantees.

- **The noise filter was quadratic, in work it had already done.** Both
  backward-looking rules took `Sequence[Turn]` and normalised every earlier turn
  inside the check, so the same strings were re-derived on every turn: **5,350
  `normalise` calls for a 100-turn conversation** where linear is about 200, and
  **159 ms for 400 turns against 2.8 ms for 50**. `TurnHistory` normalises once
  on `add` and keeps a `set` alongside the list, making the exact-match half O(1)
  instead of a scan. Measured after: **4.4 ms at 400 turns (36× faster)**, and
  10.8 ms at 1,000 — near-linear where it had been quadratic. In a product whose
  premise is long-running conversations, that curve was the bug.

  Guarded by counting `normalise` calls rather than by timing, because a
  wall-clock assertion on a shared runner measures the runner.

- **`render()` would resolve a path outside the prompt root.**
  `_PROMPT_ROOT / name` happily accepts `../../../../etc/passwd`; the only thing
  stopping a traversal was that no `v1.md` happened to sit at the far end, which
  is a property of the filesystem rather than of the code. `name` is a module
  constant at every call site today, so this was defence in depth rather than a
  live hole — but `render` is a public function of a library package. A prompt
  name is now validated as one lowercase path segment, and a version below 1 is
  refused.

### Removed

- `_tokens` and `TurnHistory.__len__`, both dead after the history refactor —
  surfaced by coverage rather than by reading. Nothing called either.

### Changed

- The extractor now records a **known gap** rather than half-fixing it:
  `content` is interpolated between `<untrusted_content>` delimiters and nothing
  stops the content from containing the closing delimiter. The canary catches an
  echo, not an escape. Sanitising `content` is *not* the fix — every
  `source_span` indexes into exactly that string and `source_hash` is its digest,
  so rewriting it would silently invalidate the provenance of every candidate.
  The answer is the pre-flight injection detector at S11.1, which quarantines
  rather than rewrites.

### Added

- **S2.3 — the span linker's fuzzy fallback, and invariant I1's property
  suite.** `link_span` tries an exact match, then
  `rapidfuzz.partial_ratio_alignment` at score ≥ 92 as `MEMORY_ENGINE.md` §1.3
  specifies, and Layer 1 is complete: raw turns in, span-anchored candidates out.
- **A fuzzy span is snapped to whole words and stripped of surrounding
  whitespace before it is stored.** The aligner optimises a similarity score,
  not readability: measured, the claim `allergic to penicilin` scores 95.24 and
  its raw span is `allergic to penicilli`, truncated mid-word. The reviewer's
  highlight is rendered from `source_span` (`DESIGN_SYSTEM.md` §3.2), and half a
  word is not a quote a human can act on. Widening is safe in the only direction
  that matters — the result still contains the matched region, so it cannot turn
  a true citation into a false one.
- **`tests/property/test_i1_sourced_writes.py`** — the invariant, at the
  boundary where it is actually enforced. Neither `REJECT` nor a store exists
  yet, so the literal form of I1 has to wait for S5.4; what can be asserted
  today is stronger than a policy anyway, because it holds by construction:
  `MemoryCandidate` requires a `Provenance`, `Provenance` requires a span, and
  `link_span` is the only thing that makes one. Plus the conservation law that
  makes the rule observable — candidates plus `dropped_unsourced` always equal
  the facts the model proposed.
- **`tests/unit/test_layer1_end_to_end.py`** — the notebook's END OF DAY 2
  CHECK, as a test rather than a manual look. It composes the noise filter and
  the extractor, which is what makes the seam between them visible: the document
  the spans index into is the *denoised* one, so a caller that joins the kept
  turns differently here and in S5.6 would put every stored span a few
  characters off — onto real text, which is why it would not look like a bug.

### Fixed

- **A blank `verbatim` produced a span that quoted nothing.** `" "` is a
  substring of almost any source, so the exact pass found it and returned a
  one-character span of whitespace — non-empty, so `Provenance` accepted it, and
  a citation of nothing, which `RULES.md` §1.1 treats as no span at all. The
  fuzzy path already refused it, so the two halves of one function disagreed
  about the same input. **Found by the I1 property test**: the hand-written case
  that was supposed to cover this used three spaces, which are not a substring
  of the test source, so it took the fuzzy path and passed for the wrong reason.

### Changed

- **ADR-0007 amends `MEMORY_ENGINE.md` §0's `Provenance`.** `verbatim` is now
  the *source* text at the span, never the model's claim — its own contract
  already required that ("what the reviewer reads and what NLI compares against,
  so it is the text itself and never a paraphrase"), and once matching is fuzzy
  the two are different strings. A `verbatim` that disagreed with its span would
  show a reviewer one thing and highlight another, and feed §2.2's NLI a
  paraphrase. `alignment: float = 1.0` records how far the claim was from what
  was stored, which is the input §3.2's fuzzy-match penalty needs and which
  cannot be recovered later — with `verbatim` being the source text, comparing
  the two returns 1.0 by construction.
- `link_span` returns a `SpanMatch` rather than a bare tuple, because the caller
  now needs the span, the text at it, and the alignment.
- **No minimum-length guard on fuzzy matches, and that was measured rather than
  assumed.** At 92 the threshold is self-limiting on short strings: one wrong
  character in a three-character needle scores 67, and `hivez` against a source
  containing `hives` scores 80. An unspecified extra rule would have been a
  guess dressed as caution.
- `_snap` is total, with no unreachable "matched only whitespace" branch.
  Reaching one would need the aligner to return an all-whitespace window at
  score ≥ 92, and the score *is* the similarity between needle and window — a
  non-blank needle scores 0 against a blank one, and a blank needle is refused
  before the call. The invariant is asserted over 500 generated examples instead
  of guarded by code that cannot run.

- **S2.2 — K-sample structured extraction.** `pipeline/l1_extract/extractor.py`
  and `prompts/extract_memories/v1.md`: denoised text in, span-anchored
  `MemoryCandidate`s out, plus the K samples Layer 3 will cluster.
- **Sample 0 is canonical and is drawn at temperature 0**, which takes two calls
  rather than the one the step's snippet makes. `LLMClient.complete` takes a
  single temperature for all `n` samples, so `K > 1` draws `n=1` at 0.0 and then
  `n=k-1` at 0.7. The snippet's `temperature=0.0 if k == 1 else 0.7` draws every
  sample at 0.7, which leaves no canonical text and makes `MEMORY_ENGINE.md`
  §3.1's minority-cluster drop — "a candidate that appears in zero clusters
  containing sample 0's meaning" — a statement about nothing.
- **`pipeline/l1_extract/span_linker.py`** — §1.3's rule, the exact-match half.
  It lands a step early because `extract` cannot build a `MemoryCandidate`
  without a `Provenance`, and a `Provenance` has no valid state without a span.
  S2.3 adds the rapidfuzz fallback and invariant I1's property test; the
  signature does not change. An empty `verbatim` returns `None` rather than
  `(0, 0)`, because a zero-width span satisfies a `NOT NULL` constraint while
  quoting nothing.
- **`ExtractionContext`** — the five caller-owned fields grouped into one object
  because they share a property: each is something the extraction model must
  never be in a position to assert. A hallucinated `tenant_id` is a
  tenant-isolation bug; a hallucinated `source_tier` lifts the cap `RULES.md`
  §4 puts on auto-writable impact. `ExtractedFact` carries only subject,
  predicate, object and verbatim, so the rest is unreachable by construction
  rather than by validation.
- **A short sample count is refused, not absorbed.** If the provider returns
  fewer samples than asked for, K collapses toward 1 — and §3.1 sets
  `H_norm := 0` at K=1, which is *maximum* confidence on that term. Absorbing it
  would let a degraded provider widen the auto-write path, which
  `ARCHITECTURE.md` §0 forbids in as many words. An unparseable sample fails the
  whole extraction for the neighbouring reason: dropping one quietly makes the
  entropy denominator a lie in the same direction.
- **Canary spotlighting across every sample of every call**, not just the
  canonical one — checking only sample 0 would leave K-1 completions unexamined.
- `prompts/extract_memories/v1.md`, whose rules are tightened from the step's
  draft in the directions that cost recall rather than precision: one claim per
  fact, copy the verbatim character for character, and an empty result is
  explicitly a valid answer, because a model that believes it must return
  something will invent it.

### Changed

- **ADR-0006 amends `MEMORY_ENGINE.md` §0's `ExtractionResult`**, adding
  `samples: list[list[ExtractedFact]]` and `dropped_unsourced: int`. Without the
  first, the K samples §3.1 clusters have no route from Layer 1 to Layer 3 and
  S5.1 would have to re-extract — K more calls, different samples, and replay
  stops reproducing decisions. Without the second, the rule §1.3 calls the one
  that "kills most confabulated facts" has an activation count nobody can see,
  which is the failure §1.1 forbids one layer up in the same words. A validator
  keeps `k_samples` and `len(samples)` in agreement, because a mismatch
  normalises entropy against a sample set that was never drawn and produces a
  plausible number in the right range.
- **`RULES.md` §2.4's function cap now states that it counts the body, not the
  docstring**, and `tests/unit/test_source_limits.py` enforces both caps. The
  two readings had coexisted until this step made them contradict: §8 requires
  every public function to document what it returns *and* raises, which no
  function with seven parameters and five raise conditions can do inside 50
  lines measured from `def`. The proof that the body reading is intended was
  already in the tree — `llm/base.py::complete` sits at exactly 50 lines
  measured from `def` while its body is the single token `...`. §0 of that
  document says a rule nothing checks should be deleted from it, so the caps are
  now a test rather than a script pasted into a terminal once per step.
- **`tests/fixtures/strategy_primitives.py`** — the shared generative vocabulary
  split out of `strategies.py`, which reached the 400-line cap exactly where the
  previous step predicted it would.
- **`tests/fixtures/extraction.py`** and `tests/unit/test_extractor_refusals.py`
  — the extraction tests split along the seam between the path where nothing
  goes wrong and everything the extractor refuses.

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
