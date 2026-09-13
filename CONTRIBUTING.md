# Contributing to GuardMem AI

> **Status: early build.** `guardmem_core` now carries settings, domain ids, the
> error hierarchy, the schema layer, the store and LLM protocols, the versioned
> prompt loader, and Layer 1 end to end — noise filter, K-sample extractor and
> span linker (S1.1 – S2.3). The gates around it have been real since S1.2 —
> `make lint`, `make typecheck`, `make test`, the pre-commit hooks and CI all
> run today, at 100% branch coverage. The suite-level gates below (integration,
> contract, e2e, eval) become enforceable at the step that creates them. They
> are written down now because, as `BUILD_NOTEBOOK.md` S1.2 puts it: *"if the
> gates are not in place on day 1, you will not add them on day 15."*

## Before you change anything

Read [`docs/README.md`](docs/README.md) first — specifically the **"Which
document owns what"** table. Every fact in this project has exactly one home.
Change it there; everywhere else cites it. That table exists because the first
review of the spec suite found the same number stated three different ways in
three documents.

The two that catch people most often:

- **`MEMORY_ENGINE.md` is the spec of record for scoring.** If the code and that
  document disagree, **the code is wrong** until an ADR says otherwise.
- **`RULES.md` owns engineering gates** — invariants, coverage, testing.

## Environment

```bash
uv venv --python 3.12 --prompt HGEM --seed .venv
.venv\Scripts\activate                       # or: source .venv/Scripts/activate
uv pip install -r requirements-dev.txt       # third-party deps + dev toolchain
uv pip install -e packages/guardmem-core     # the workspace package, editable
python -m spacy download en_core_web_lg
cp .env.example .env                         # paste your own keys; never commit .env
```

Install the hooks and confirm the environment before you start:

```bash
make hooks                                   # pre-commit, once per clone
make lint && make typecheck && make test     # the same gates CI runs
```

All three must exit 0. `make lint` covers ruff, ruff-format and the
import-linter contracts; run `make` with no target for the full list.

`make` is not installed on Windows and does not ship with Git for Windows —
`winget install ezwinports.make`, then restart your shell.

If `pytest` reports `ModuleNotFoundError: guardmem_core`, you skipped the
editable install on the second line — `requirements-dev.txt` carries only
third-party packages.

**Never run bare `uv sync`.** It is *exact* and uninstalls everything absent from
`uv.lock` — about 300 of the 311 installed packages. Use `uv run`, which is
inexact and safe, or `uv pip install`. The reasoning is recorded at the bottom of
`pyproject.toml`.

Python 3.12, `uv` for Python packaging, `pnpm` for TypeScript. Never
`pip install` into the system interpreter.

Adding a dependency means editing the right layer in `requirements/` — the
layering mirrors the `import-linter` contract, so `requirements/base.txt`
(`guardmem-core`) must never gain a web framework — then regenerating the lock:

```bash
uv pip compile requirements-dev.txt -o requirements.lock.txt
```

Each entry cites the step or spec clause that requires it. Keep that habit; it
is what lets the next reader tell a real dependency from an accumulated one.

## The non-negotiables

From `docs/RULES.md` §1. These are not style preferences — a violation is a P0.

1. **No unsourced write.** Persisting an assertion without `source_hash` +
   `source_span` is a P0 bug, enforced by a DB constraint *and* a property test.
2. **No destructive mutation.** Retirement is `valid_to = now()` +
   `superseded_by`. `DELETE` is revoked at the role level.
3. **Fail closed.** Every `except` in the decision path resolves to
   `HITL_REVIEW` or `REJECT`.
4. **The audit write is in the same transaction as the state change.** Not
   after. Not best-effort.
5. **No PII in logs, traces, exceptions, or prompts** to non-vault-scoped
   providers. Span attributes are allow-listed, not deny-listed.
6. **Determinism where it's claimed.** `decide()` is a pure function — no I/O,
   no clock, no settings read, no feature-flag lookup. That is what makes replay
   meaningful.

### The seven invariants

Every one has a test. **If a test for one of these goes red, stop feature work.**

```
I1  every AUTO_WRITE assertion has a non-null source_span
I2  no two visible assertions share (subject, predicate) when cardinality == ONE
I3  supersession is acyclic
I4  decide() is deterministic and total over (C, R, policy)
I5  audit chain verifies: digest_n == sha256(payload_n || digest_{n-1})
I6  a tombstoned assertion never appears in retrieval results
I7  tokenized PII never appears in any span attribute or log line
```

## Code standards

Full detail in `docs/RULES.md` §2. The short version:

- `mypy --strict` on `guardmem-core`. A bare `Any` in a public signature fails
  review; anywhere else it needs an inline justification.
- Domain ids are `NewType`, not `str`. Passing a raw `str` where an
  `AssertionId` is expected must be a type error.
- Public boundaries take and return Pydantic models — never loose
  `dict[str, Any]`. Models are `extra="forbid"`, `frozen=True`, `strict=True`.
  `extra="forbid"` matters most on LLM structured output: a hallucinated field
  should raise, not vanish silently.
- All I/O is `async`. Every outbound call has an explicit timeout — no timeout
  fails CI. Concurrency uses `asyncio.TaskGroup`; fan-out is bounded by a
  semaphore, never an unbounded `gather`.
- Module ≤ 400 lines, function ≤ 50 lines, complexity ≤ 10.
- No business logic in routers. No global mutable state.
- Prompts live in versioned files (`prompts/<name>/v<N>.md`) with frontmatter —
  never f-string-assembled inline. Model ids are pinned exactly; a floating alias
  makes replay dishonest.

## Tests

| Suite | Gate |
|---|---|
| `unit` | ≥ 90% on `guardmem-core`, ≥ 85% repo-wide |
| `integration` | testcontainers; all green, no skips on main |
| `contract` | schemathesis on OpenAPI + MCP tool schemas; no drift |
| `property` | hypothesis; invariants hold for 500 examples |
| `security` | injection/poisoning corpus; 0% success into primary namespace |
| `e2e` | Playwright; critical paths green |
| `eval` (nightly) | no metric regresses more than 2 points absolute |

Coverage is a floor, not a goal. A PR that raises coverage while lowering
mutation score gets rejected.

**Test hygiene:** no network in unit tests, no `sleep` (use fake clocks), LLM
calls mocked from recorded fixtures. Every bug fix ships with its regression
test in the same PR.

## Pull requests

- Trunk-based, short-lived branches named for the step: `feat/s1-3-settings-module`.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`,
  `perf:`, `sec:`, `chore:`, `test:`, `docs:`.
- **One step, one commit.** If a step takes more than ~90 minutes it was written
  wrong — split it.
- ≤ 400 changed lines where humanly possible. Separate large mechanical
  refactors from behavior changes.
- `guardrails/`, `pipeline/l3_score/` and `migrations/` require a second reviewer.
- Migrations are forward-only and backward-compatible for one release
  (expand → migrate → contract).
- **Any PR touching decision logic must state its expected effect on HITL volume
  and attach the eval delta from CI.** *"Shouldn't change anything"* is an answer
  that requires the eval run to back it.
- Never leave a day with a red test suite.

## When to write an ADR

Anything expensive to reverse: store choice, scoring formula changes, API
surface changes, licensing. An ADR in [`docs/adr/`](docs/adr/) is also the *only*
legitimate way to change a decision that a spec document owns — including
changing `MEMORY_ENGINE.md`.

If you find a step in `BUILD_NOTEBOOK.md` that turns out to be wrong, fix the
notebook **in the same commit** as the code. A notebook that drifts from the code
is worse than no notebook.
