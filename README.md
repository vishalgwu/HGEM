# GuardMem AI

[![CI](https://github.com/vishalgwu/HGEM/actions/workflows/ci.yml/badge.svg)](https://github.com/vishalgwu/HGEM/actions/workflows/ci.yml)

**A memory governance gateway for long-running AI agents.**

> **Status: Layer 1 works end to end, and it now has somewhere to write to.**
> The design suite, the toolchain and the gates are in place; `guardmem_core`
> carries its typed foundation — settings, domain ids, the error hierarchy, the
> Pydantic schema layer, the store and LLM protocols — the whole of **Layer 1**
> (noise filter, K-sample extractor, span linker), and from S3.1–S3.5 the
> bitemporal Postgres schema, the pgvector store over it, the outbox that
> coordinates the dual write, the graph store on the other side of it, and the
> ontology that says what a predicate is allowed to mean, with `make seed`
> putting all of it together:
> write, search, supersede, point-in-time recall of a fact that has since been
> retired, and a partial write that is never retrievable. Nothing yet validates,
> scores or decides a candidate — that is Layer 2. Every performance
> and quality figure below is a **target**, not a measurement — see
> [Status](#status) before quoting any number.

---

## The problem

An agent is correct on turn 3, absorbs a wrong fact on turn 40, and is
confidently wrong for six weeks — because nothing sat between the model and the
datastore.

Almost every serious agent deployment eventually writes to long-term memory.
Almost none of them govern that write. There is no uncertainty estimate, no
conflict check, no blast-radius assessment, and no lineage. A hallucinated fact
is stored with exactly the same authority as a confirmed one, and six weeks
later nobody can prove where it came from.

## What GuardMem does

It intercepts every candidate fact *before* it becomes durable state:

```
raw turns ──► L1 EXTRACTION ──► L2 VALIDATION & CONFLICT ──► L3 SCORING ──► DECISION
              noise → cands       ontology, NLI, dedupe        H, C, R       AUTO_WRITE
                                                                             HITL_REVIEW
                                                                             REJECT
                                                                             ESCALATE
```

1. **Extract** structured candidates, each anchored to a **verbatim source span**.
   No span, no write — this single rule kills most confabulation before any
   scoring happens.
2. **Validate** against a tenant ontology and check three independent conflict
   signals: NLI contradiction, predicate cardinality, temporal overlap.
3. **Score** confidence `C` from semantic entropy over meaning clusters, and
   impact risk `R` from blast radius. These are deliberately separate axes — a
   perfectly confident write to the account-owner field is still a high-risk
   operation.
4. **Decide** — auto-write the safe majority, queue the ambiguous middle for a
   one-click human review, reject the rest.
5. **Receipt** — every durable decision lands in a hash-chained audit log.

Four stances drive the rest of the design:

- **The agent never blocks on governance.** `propose` returns fast with a
  `trace_id`; evaluation runs on a worker.
- **Nothing is deleted.** Contradiction resolves by supersession + tombstone,
  and bitemporal columns make *"what did the agent believe on 2026-03-14?"* a
  normal query.
- **Fail closed, not quiet.** Provider outage, budget cap, policy error,
  injection detection — all degrade toward human review. Degradation never
  widens the auto-write path.
- **The audit log is the product.** If a decision isn't reconstructable, that's
  a bug of the same severity as a wrong decision.

---

## Repository layout

```
docs/              the design suite - start here
packages/          guardmem-core, the decision engine
tests/             unit / integration / contract / property / security
requirements/      layered, pinned Python dependencies
.github/workflows/ CI gates
Makefile           the developer entry points - run `make` for the list
.env.example       environment template
```

Everything else in [`docs/PROJECT_TREE.md`](docs/PROJECT_TREE.md) is a
**blueprint, not a checklist**. Each file gets created at the build step that
needs it. An empty module that exists is indistinguishable at a glance from a
finished one, which is exactly the confusion the build order exists to prevent.

## Start here

| Read | For |
|---|---|
| [docs/README.md](docs/README.md) | the index, and **which document owns which decision** |
| [docs/PRD.md](docs/PRD.md) | problem, scope, personas, acceptance targets |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, write/read paths, failure behavior |
| [docs/MEMORY_ENGINE.md](docs/MEMORY_ENGINE.md) | **the spec of record** — scoring math, schemas, thresholds |
| [docs/RULES.md](docs/RULES.md) | engineering standards, the seven invariants, testing gates |
| [docs/BUILD_NOTEBOOK.md](docs/BUILD_NOTEBOOK.md) | the step-by-step build, day by day |

Read `docs/README.md` first. It carries a "which document owns what" table, so
you know where a change belongs before you make it.

---

## Local setup

Requires Python 3.12, Docker, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/vishalgwu/HGEM.git && cd HGEM

uv venv --python 3.12 --prompt HGEM --seed .venv
.venv\Scripts\activate                       # PowerShell
# source .venv/Scripts/activate              # Git Bash / macOS / Linux

uv pip install -r requirements.lock.txt      # third-party deps, 311 packages
uv pip install -e packages/guardmem-core     # the workspace package itself
python -m spacy download en_core_web_lg      # presidio needs this; ~400 MB

cp .env.example .env                         # then paste your API keys into .env
```

Install the git hooks, then verify — these are the same gates CI runs:

```bash
make hooks                                   # pre-commit, once per clone
make lint && make typecheck && make test     # all three must pass
```

`make` on Windows: it is not installed by default and does not ship with Git for
Windows. `winget install ezwinports.make` gives GNU Make 4.4.1 with no MSYS
dependency; restart your shell afterwards so the PATH change takes effect. Run
`make` with no target for the full list.

The second install line is not optional and is easy to skip. `requirements.lock.txt`
pins only third-party packages; `guardmem_core` lives in this repo and is
installed from source in editable mode, so your edits take effect without
reinstalling. Omit it and every import of `guardmem_core` fails with
`ModuleNotFoundError` on an otherwise perfectly good environment.

> **Do not run bare `uv sync` here.** It is *exact* — it uninstalls everything
> not in `uv.lock`, which today means roughly 300 of the 311 packages above.
> `uv run` is inexact and safe. See the note at the bottom of `pyproject.toml`.

`requirements.lock.txt` is the fully-resolved transitive set — 311 packages,
verified to reproduce the environment exactly rather than approximately.
`requirements/ml-local.txt` (torch, transformers) is **excluded on purpose** and
is only needed if week-2 latency forces a local cross-encoder.

The dev datastore stack is `infra/docker/docker-compose.dev.yml`, in place since
S1.3: Postgres+pgvector, Redis, Neo4j and Arize Phoenix, every image pinned and
healthchecked. `make dev` brings it up and waits for health. Langfuse is
deliberately **not** in it — from v3 it needs ClickHouse, MinIO and a worker
container, so it arrives at S13.1 in `docker-compose.observability.yml` with the
stack it actually requires.

---

## Status

The engine is not implemented yet. The repository was reset to a documentation
baseline on 2026-09-09; `BUILD_NOTEBOOK.md` Day 1 is complete (S1.1 – S1.7),
Layer 1 is complete (S2.1 – S2.3), and the storage layer is complete — **Day 3
is done, S3.1 – S3.6**.
So the toolchain, the gates, the local datastore stack, the typed foundation of
`guardmem_core` — settings, domain ids, the error hierarchy, the Pydantic schema
layer, and the `LLMClient` / `VectorStore` / `GraphStore` protocols with
in-memory fakes — Layer 1, and durable storage are real.

The noise filter is the first component with a measured number attached, and it
is a narrow one: over a 40-turn hand-labelled corpus, its deterministic rules
drop 17 turns at **precision 1.000** and settle 30 of 40 turns without a model
call. That is a unit-test gate on one small corpus, not a production figure, and
two of the five drop classes are deliberately under-detected until the embedder
(S3.2) and the ontology (S3.5) exist — both now do, and the rules that use them
are Layer 2.

S2.2 and S2.3 add the extractor and the span linker: K samples with a
temperature-0 canonical draw, each proposed fact anchored to a verbatim span of
the source, and anything the source cannot support rejected as unsourced and
counted. That span rule is the anti-confabulation mechanism, and it is not a
judgement — a model that invents a fact must also invent the sentence it came
from, and an invented sentence is not in the source. It is enforced by
construction rather than by policy: a candidate requires provenance, provenance
requires a span, and the linker is the only thing that makes one.

S3.1 adds the schema underneath all of it: bitemporal assertions, provenance as
its own table, an append-only audit chain, the dual-write outbox, and row-level
security. Four of `RULES.md`'s non-negotiables are now properties of the
database rather than promises of the application — `DELETE` on an assertion is
revoked at the role level, the audit chain refuses UPDATE and DELETE, an
assertion without a citation cannot commit, and a session with no tenant set
sees nothing.

S3.2 is what reads and writes it. `PgVectorStore` implements the `VectorStore`
protocol over that schema, and the three properties that matter are structural
rather than conventional: a write lands `visible = false` as a statement
literal, so a half-finished dual write has no parameter through which it could
be made retrievable; the tenant is bound at construction and applied as
`SET LOCAL app.tenant_id`, which is the value row-level security reads; and
supersession is an `UPDATE` of `valid_to` that raises rather than passing
quietly when it matches nothing. A retired fact leaves search and stays
recoverable through `as_of`, which is the claim the product rests on and is
tested against a real Postgres rather than against a double.

S3.3 closes the loop. The assertion row and an outbox event commit in one
transaction; a relay then applies the graph side and sets `visible = true`, and
nothing else in the system may set that column. So a dual write that is
genuinely half-applied — the edge landed, the process died before the flip — is
not briefly wrong but simply absent, and the event that releases it is still in
the queue. Delivery is at-least-once and the effects are exactly-once, which is
the only guarantee a queue with a crashing consumer can honestly offer: the
graph write is idempotent by `assertion_id`, and the completion will not move a
timestamp it has already written. `StoreRouter` sits in front of it as the
app-layer tenant check `RULES.md` §4 asks for alongside row-level security.

S3.4 gives the relay something real to write to. `NetworkXGraphStore` is a
`MultiDiGraph` whose edges are keyed by `assertion_id`, so a replayed dispatch
replaces an edge rather than duplicating it, and `degree()` returns a number the
risk scorer can use — without it `MEMORY_ENGINE.md` §3.3's `graph_fanout`
feature is a constant and the blast-radius half of the decision matrix cannot be
evaluated at all. It is the dev and single-tenant backend, and it enforces that:
the `GraphStore` protocol gives its read methods no tenant to filter on, so it
refuses to hold two. Neo4j swaps in at S7.1 behind the same three methods.

S3.5 is the first thing that can say what a predicate *means*. The clinical
starter pack declares fifteen predicates over six entity types, and each one
states its cardinality, its blast radius, the weakest source allowed to assert
it, and whether one source is enough. Those four fields are what Layer 2 and the
risk scorer read: cardinality is what turns a second live value into a conflict
regardless of what a language model thinks, and impact is what floors the risk
score so a confident write to a critical field cannot auto-write on confidence
alone. Nothing consumes it yet — the schema gate is the first consumer, and it
is Layer 2.

S3.6 puts all of it together. `make seed` writes one tenant, seven entities and
twenty-eight assertions through the real path — router, store, outbox, relay,
graph store, ontology — from a forty-turn synthetic intake call, and every
seeded fact cites a real character span in the turn it was quoted from. Two of
them are superseded by a follow-up call five months later, so the demo database
contains a point-in-time query worth running. Running it twice changes nothing:
every id is derived from a stable key, which is the same property the outbox
relay's replay needed.

Two caveats about seeded data, stated here because they are easy to forget. The
vectors come from a deterministic hash — there is no embedding provider until
S9.1 — so nothing about retrieval quality can be measured on them. And
`confidence` is a placeholder, because Layer 3 does not exist yet; only `risk`
is real, and only because it is the impact floor the ontology declares.

That Postgres is a testcontainer, started by the suite from the repository's own
`initdb` scripts and migrated with `alembic upgrade head`; CI runs it on every
push. The next step is S4.1, the schema gate — the first component of Layer 2,
and the first real consumer of the ontology.

**Nothing in the design suite is evidence of an implemented feature.** All
runtime paths, service URLs, package names, deployment examples, CI gates and
runbook commands describe the *intended* build. Every latency and quality figure
is a target until measured — the measurement happens in week 4, and the honest
numbers replace the targets then.

Two figures worth stating precisely, because they are the ones people quote:

- **Median review time** is owned by `PRD.md` §6.2 as a ≤ 25 s acceptance gate.
  `DESIGN_SYSTEM.md` §3.1 sets a tighter 18 s *design* target, which is not a gate.
- **Coverage** is owned by `RULES.md` §5 — 90% on `guardmem-core` at release,
  with 85% as the Week-1 interim floor.

The single most important milestone is **Checkpoint B** (`BUILD_NOTEBOOK.md`,
Day 5): the AUROC of `C` against 200 hand-labelled candidates must reach 0.80.
If the scoring cannot separate good writes from bad ones, nothing downstream
matters — and that is deliberately learned in week 1, not week 4.

## Licensing

Core engine under **Apache-2.0** — see [LICENSE](LICENSE). The `apps/dashboard`
HITL queue and the SSO/audit-export modules are intended to ship under BUSL-1.1
converting to Apache-2.0 after four years, a standard open-core split. That
dashboard code does not exist yet; the split will be recorded in its own LICENSE
file when it does.

## Security

Please do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).
