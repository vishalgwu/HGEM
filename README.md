# GuardMem AI

**Datadog + a compliance officer for AI agent memory.**

A memory governance gateway that sits between long-running AI agents and their
persistent state. Every candidate fact is scored for confidence and blast radius,
the safe majority is auto-written, the ambiguous middle is routed to a human, and
every decision is captured in a hash-chained audit trail.

> Long-lived agents should be *auditable state machines*, not text files that grow
> until they rot.

---

## Why

Enterprise agents — clinical intake bots, contract-review copilots, underwriting
assistants — write to memory with zero validation. One hallucinated fact silently
corrupts every downstream decision, and nobody can prove afterward where it came
from. GuardMem intercepts the write path and makes it auditable.

Five failure classes we govern against:

- **Silent memory drift** — low-confidence extractions stored with the same
  authority as confirmed ones.
- **Thread rot** — superseded facts never retire; retrieval returns both.
- **Catastrophic state corruption** — 1:1 predicate overwrites, wide-blast-radius
  deletes, org-namespace poisoning.
- **Memory poisoning attacks** — injected instructions that survive session resets.
- **No audit lineage** — "why did the agent believe this?" has no answer today.

Full problem framing: [docs/PRD.md](docs/PRD.md).

---

## Architecture at a glance

```
agent runtime
    │  guardmem.remember(...)
    ▼
GATEWAY (FastAPI) ──► SECURITY ARMOR ──► REDIS STREAM ──► WORKER (arq)
                     injection · PII                     │
                     · provenance                        ▼
                                            PIPELINE (L1 extract →
                                             L2 validate → L3 score →
                                             decide) ──► VECTOR + GRAPH
                                                          + AUDIT CHAIN
                                                          + HITL QUEUE
```

- **Agent never blocks on governance.** `propose` returns in <80ms with a
  `trace_id`; evaluation happens on a worker.
- **Nothing is deleted.** Supersession + tombstone. Bitemporal columns make
  "what did the agent believe on 2026-03-14?" a normal query.
- **Fail closed, not quiet.** Provider outage, budget cap, policy error,
  injection hit — all degrade toward HITL or quarantine.
- **The audit log is the product.** Every component feeds it. If a decision
  isn't reconstructable, it's a bug of the same severity as a wrong decision.

Full: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Repository layout

```
guardmem-ai/
├── docs/           product / architecture / rules / roadmap / ADRs / runbooks
├── packages/
│   ├── guardmem-core/         the brain — zero web-framework deps
│   ├── guardmem-sdk-python/   pip install guardmem
│   └── guardmem-sdk-ts/       @guardmem/sdk (dashboard + edge agents)
├── services/
│   ├── gateway/     FastAPI ingress
│   ├── worker/      arq — async eval + compaction
│   └── mcp_server/  stdio + streamable-HTTP MCP transports
├── apps/
│   └── dashboard/   Next.js 15 · control panel + HITL queue
├── infra/           docker-compose · terraform · k8s · migrations
├── evals/           quality, drift, security, HITL-agreement suites
├── bench/           locust load profiles + p95 targets
├── tests/           unit · integration · contract · property · security
└── scripts/         seed, replay_trace, threshold_tuner
```

Blueprint with per-file responsibilities: [docs/PROJECT_TREE.md](docs/PROJECT_TREE.md).

Import direction is enforced: `apps → services → packages → stores`. Cycles fail
CI (`import-linter` contract).

---

## Documentation

| Doc | What it covers |
|---|---|
| [PRD](docs/PRD.md) | Problem, personas, target verticals, KPIs |
| [Architecture](docs/ARCHITECTURE.md) | System data flow, components, stance |
| [Memory Engine](docs/MEMORY_ENGINE.md) | Bitemporal model, retrieval, compaction |
| [Rules](docs/RULES.md) | Policy pack DSL, obligations, decision matrix |
| [MCP Integration](docs/MCP_INTEGRATION.md) | Tool/resource/prompt surface |
| [Design System](docs/DESIGN_SYSTEM.md) | Dashboard + review-queue UX |
| [Phases & Roadmap](docs/PHASES_AND_ROADMAP.md) | 4-week execution plan |
| [Project Tree](docs/PROJECT_TREE.md) | Full repository blueprint |
| [Build Notebook](docs/BUILD_NOTEBOOK.md) | Deep-dive engineering notes |

Architecture Decision Records live under [docs/adr/](docs/adr/).
Incident runbooks live under [docs/runbooks/](docs/runbooks/).

---

## Getting started (local dev)

Prereqs: Python 3.12, Node 20, Docker, `uv`, `pnpm`.

```bash
cp .env.example .env
make dev          # boot postgres+pgvector, neo4j, redis, langfuse, phoenix
make migrate      # alembic + neo4j cypher
make seed         # demo tenant with the clinical ontology
make test         # unit + integration
make eval         # nightly eval suites against a fixed baseline
```

Then point Claude Desktop (or any MCP client) at `services/mcp_server` and call
`memory.propose` → `memory.commit` → `memory.search`.

---

## Roadmap (4-week alpha)

| Phase | Days | Exit gate |
|---|---|---|
| **1** Core engine + MCP | 1–7 | end-to-end from Claude Desktop; replay reproduces decisions deterministically |
| **2** Gateway + Worker + Guardrails | 8–14 | propose <80ms p95; injection corpus regression clean |
| **3** Dashboard + HITL queue | 15–21 | reviewer can adjudicate a task in <15s with keyboard only |
| **4** Evals + hardening + demo | 22–28 | drift@100 flat; ASR on redteam set below threshold |

Detail: [docs/PHASES_AND_ROADMAP.md](docs/PHASES_AND_ROADMAP.md).

---

## Licensing

Open core / commercial split:

- **`packages/guardmem-core`**, SDKs, services, MCP server — **Apache-2.0**.
- **`apps/dashboard`** HITL queue + SSO / audit-export modules — **BUSL-1.1**,
  converting to Apache-2.0 after 4 years.

Self-hosters get the engine. Enterprises pay for the control plane.

---

## Status

Pre-alpha. Scaffold + design docs are in; implementation is in progress against
the Phase 1 exit gate. See [CHANGELOG.md](CHANGELOG.md).
