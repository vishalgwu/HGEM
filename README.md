# GuardMem AI

**A memory governance gateway for long-running AI agents.**

> **Status: pre-implementation.** This repository currently contains the design
> suite, the pinned dependency set, and the environment template. There is no
> runtime code yet. Every performance and quality figure below is a **target**,
> not a measurement — see [Status](#status) before quoting any number.

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
requirements/      layered, pinned Python dependencies
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

uv pip install -r requirements.lock.txt
python -m spacy download en_core_web_lg      # presidio needs this; ~400 MB

cp .env.example .env                         # then paste your API keys into .env
```

`requirements.lock.txt` is the fully-resolved transitive set — 311 packages,
verified to reproduce the environment exactly rather than approximately.
`requirements/ml-local.txt` (torch, transformers) is **excluded on purpose** and
is only needed if week-2 latency forces a local cross-encoder.

The dev datastore stack (Postgres+pgvector, Neo4j, Redis, Langfuse, Phoenix)
arrives at build step S1.3 as `infra/docker/docker-compose.dev.yml`.

---

## Status

Implementation has not started. The repository was reset to a documentation
baseline on 2026-09-09, and the build begins at `BUILD_NOTEBOOK.md` step S1.1.

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
