# ARCHITECTURE.md — GuardMem AI

## 0. Architectural Stance

Four decisions drive everything else:

1. **The agent never blocks on governance.** `propose` returns in <80 ms with a `trace_id`. Evaluation
   happens on a worker. Agents that need synchronous certainty opt into `mode=strict`.
2. **Nothing is deleted.** Contradiction resolves by supersession + tombstone. Bitemporal columns make
   "what did the agent believe on 2026-03-14?" a normal query rather than an archaeology project.
3. **Fail closed, not quiet.** Provider outage, budget cap, policy engine error, injection detection —
   all degrade toward HITL or quarantine. Degradation never widens the auto-write path.
4. **The audit log is the product.** Every other component is instrumented to feed it. If a decision
   isn't reconstructable, it's a bug of the same severity as a wrong decision.

---

## 1. System Data Flow — Memory Write Path

```
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │  AGENT RUNTIME   LangGraph · CrewAI · Claude Desktop · Cursor · custom loop       │
 │                  guardmem.remember(turns, namespace="patient:8812")               │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 │  REST /v1/memory:propose   |   MCP tool memory.propose
                                 ▼
 ╔══════════════════════════════════════════════════════════════════════════════════╗
 ║ ①  GATEWAY  (FastAPI, stateless, Cloud Run)                                      ║
 ║    authN(OIDC/mTLS) → tenancy resolve → RLS ctx → rate limit → idempotency key    ║
 ║    OTel root span opened  ▸ trace_id minted  ▸ payload hashed → blob store        ║
 ╚═══════════════════════════════╤══════════════════════════════════════════════════╝
                                 │
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ ②  SECURITY ARMOR  (pre-flight, runs on RAW text before any model sees it)       │
 │    injection detector  ·  PII detect + tokenize → VAULT  ·  source trust tier     │
 │    ───────────────────────────────────────────────────────────────────────────   │
 │    INJECTION HIT ──────────────────────────────► QUARANTINE ns + SECURITY ALERT  │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 │ clean, tokenized text
                       ┌─────────┴─────────┐
        mode=async     │                   │   mode=strict
        (default)      ▼                   ▼
              202 + trace_id       ┌─────────────────┐
              ┌──────────────┐     │  inline run of  │
              │ REDIS STREAM │     │  ③–⑦ (K=1 FAST) │
              │  eval queue  │     └────────┬────────┘
              └──────┬───────┘              │
                     ▼                      │
 ┌──────────────────────────────────────────┴───────────────────────────────────────┐
 │ ③  MEMORY ENGINE — LAYER 1: EXTRACTION & NOISE REDUCTION                         │
 │    noise_filter (ephemeral/imperative/chit-chat drop)                            │
 │    → LLM ROUTER ─── FAST tier (Haiku / gpt-4o-mini) ── K samples (K=1|3|5)        │
 │    → structured output → MemoryCandidate[] + span_linker(char offsets, doc hash)  │
 │    NO SPAN ──────────────────────────────────────────────► REJECT(unsourced)      │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ ④  LAYER 2: ENTITY SCHEMA VALIDATION & CONFLICT DETECTION                        │
 │    schema_gate(tenant ontology) ─ unknown predicate ──► QUARANTINE ns             │
 │    retrieve top-k neighbours ◄──────── STORE ROUTER (read) ────────────┐          │
 │    conflict.py:  (a) NLI contradiction   (b) cardinality   (c) temporal │          │
 │    dedupe.py:    cosine ≥ .95 ∧ bi-entailment → MERGE (no new row)      │          │
 └───────────────────────────────┬────────────────────────────────────────┘          │
                                 ▼                                                   │
 ┌──────────────────────────────────────────────────────────────────────────────────┐│
 │ ⑤  LAYER 3: RISK / ENTROPY SCORING            (EVALUATOR)                        ││
 │    semantic entropy H over K clusters   →  confidence C ∈ [0,1]                   ││
 │    blast-radius features (graph fan-out, cardinality, PII class, scope, revers.)  ││
 │                                         →  impact risk R ∈ [0,1]                  ││
 │    corroboration quorum · source trust · policy obligations                       ││
 └───────────────────────────────┬──────────────────────────────────────────────────┘│
                                 ▼                                                   │
 ┌──────────────────────────────────────────────────────────────────────────────────┐│
 │ ⑥  DECISION MATRIX  (pure function; deterministic given C,R,policy,thresholds)   ││
 │                                                                                  ││
 │      REJECT ◄── C<τ_lo ──┬── ESCALATE (once, FRONTIER re-score) ──┐               ││
 │                          │                                        │               ││
 │                    HITL_REVIEW ◄── mid band / high R ──────────────┘              ││
 │                          │                                                       ││
 │                    AUTO_WRITE ◄── C≥τ_hi ∧ R≤ρ_lo ∧ policy.ok                     ││
 └──────┬──────────────────────────────────────┬────────────────────────────────────┘│
        │ AUTO_WRITE                           │ HITL_REVIEW                          │
        │                                      ▼                                      │
        │                        ┌──────────────────────────────┐                     │
        │                        │ ⑦a HITL QUEUE                │                     │
        │                        │  priority = impact×stale×SLA │                     │
        │                        │  assignment by skill/ns      │                     │
        │                        └───────┬──────────────┬───────┘                     │
        │                                │              │ SLA breach                  │
        │                    reviewer: APPROVE│EDIT      ▼                             │
        │                                │        escalate / policy-reject             │
        │                                ▼                                             │
        └────────────────┬───────────────┘                                             │
                         ▼                                                             │
 ┌──────────────────────────────────────────────────────────────────────────────────┐ │
 │ ⑦  STORE ROUTER (write, outbox-coordinated, single logical txn)                  │─┘
 │                                                                                  │
 │    ┌────────────────────────┐   ┌──────────────────────────┐   ┌───────────────┐ │
 │    │ VECTOR STORE           │   │ KNOWLEDGE GRAPH          │   │ PII VAULT     │ │
 │    │ pgvector | Qdrant      │   │ Neo4j | NetworkX         │   │ tokenised PII │ │
 │    │ embedding + payload    │   │ (Entity)-[ASSERTS]->(..) │   │ separate KMS  │ │
 │    │ conf/recency ranking   │   │ bitemporal edges         │   └───────────────┘ │
 │    └────────────────────────┘   └──────────────────────────┘                     │
 │    supersede: prior.valid_to = now  ▸ tombstone row  ▸ never DELETE              │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │ ⑧  WRITE RECEIPT + AUDIT CHAIN                                                   │
 │    receipt{assertion_id, decision, C, R, model@ver, prompt@ver, policy@ver,       │
 │            source_hash+span, reviewer?, prev_digest, digest=H(event‖prev)}        │
 └───────────────────────────────┬──────────────────────────────────────────────────┘
                                 ▼
 ╔══════════════════════════════════════════════════════════════════════════════════╗
 ║ ⑨  OBSERVABILITY BUS   OTel Collector → Langfuse · Arize Phoenix · Prometheus     ║
 ║    → Dashboard (SSE): funnel, cost, latency, HITL ratio, drift probes             ║
 ╚══════════════════════════════════════════════════════════════════════════════════╝

 ── ASYNC SIDE LOOP (worker cron) ──────────────────────────────────────────────────
   compaction.decay → cold-tier / episodic rollup ▸ reindex ▸ drift probe ▸ SLA sweep
```

> The decision box above is a **simplification for the flow diagram** — it shows the two extremes and
> collapses the middle. The normative form is the 4×3 matrix plus seven ordered overrides in
> `MEMORY_ENGINE.md` §3.4, which uses five thresholds (`τ_lo`, `τ_mid`, `τ_hi`, `ρ_lo`, `ρ_hi`).
> Implement from that section, not from this diagram.

### Read path (short)

```
agent.recall(query, ns)
  → Gateway (authz, budget)
  → Retrieval: dense(top-50) ⊕ BM25(top-50) → RRF fuse → graph 1-hop expand
  → filter: valid_to IS NULL ∧ confidence ≥ ns.min_read_confidence ∧ not tombstoned
  → rerank(cross-encoder | LLM-lite) with recency + confidence features
  → context packer (token budget, dedupe, provenance footnotes)
  → response + inclusion/exclusion rationale in trace
```

---

## 2. Component Breakdown

### 2.1 Gateway (`services/gateway`)
Stateless FastAPI ingress. Responsibilities: authN/authZ, tenant resolution and Postgres RLS
context, rate limiting (token bucket in Redis, per tenant + per API key), idempotency keys on
`propose`/`commit`, request body hashing to blob store, OTel root span creation, and mode dispatch
(async enqueue vs strict inline). Holds **no** business logic — it imports `guardmem_core.pipeline`
and calls one function. Horizontal scale, min instances 1 on Cloud Run to avoid cold-start on the
read path.

### 2.2 Evaluator (`guardmem_core.pipeline`)
The three-layer engine plus scoring. Runs identically in the Gateway (strict mode), the Worker
(async mode), and the eval harness — this is why it has no framework dependencies. Pure-ish:
side effects confined to injected `LLMClient`, `VectorStore`, `GraphStore` protocols, which makes
`replay_trace.py` possible with recorded fixtures. Detailed math in `MEMORY_ENGINE.md`.

### 2.3 Guardrails (`guardmem_core.guardrails`)
Two placements. **Pre-flight** (before any model call): injection detection, PII tokenization,
source trust assignment. **Pre-write**: policy engine obligations, poisoning quorum, toxicity, and
namespace ACL. Policy packs are Rego-compatible, versioned, hot-reloadable, and support `dry_run`
so a tenant can measure a rule's blast radius before enforcing it. Every guardrail returns a
structured `Obligation` (e.g. `require_review`, `require_corroboration`, `redact_field`) rather than
a boolean — the decision matrix composes obligations rather than short-circuiting.

### 2.4 Store Router (`guardmem_core.memory.router`)
Decides destination per assertion: semantic/episodic content → vector; typed relational predicates
→ graph; most facts → both, joined by `assertion_id`. Coordinates the dual write via a Postgres
**outbox**: the assertion row + outbox event commit atomically; a relay applies the vector/graph
side effects and marks the outbox row done. Readers filter on `visible=true`, set only after both
sides land, so a partial write is never retrievable. Backend swaps are config, not code
(`VectorStore`/`GraphStore` Protocols), which also lets GuardMem front an existing mem0/Zep install.

### 2.5 Observability Bus (`guardmem_core.observability`)
OTel spans with a stable naming convention (`guardmem.l1.extract`, `guardmem.l3.score`,
`guardmem.decision`, `guardmem.store.write`). Span attributes carry `candidate_id`, `namespace`,
`decision`, `confidence`, `risk`, `model`, `tokens_in/out`, `cache_hit`. Exporters: OTLP →
Collector → Langfuse (LLM-native traces, prompt/version linkage), Arize Phoenix (eval + drift),
Prometheus/Grafana (RED metrics, SLO burn). Separate from all of it: the **audit log**, an
append-only hash-chained Postgres table — telemetry can be sampled and lossy, audit cannot.

### 2.6 MCP Interface (`services/mcp_server`)
The primary agent-facing surface. Exposes tools, resources, and prompts over stdio (desktop
clients) and streamable HTTP (server agents), with the same authZ scopes as REST. Full contract in
`MCP_INTEGRATION.md`.

### 2.7 HITL Subsystem (`guardmem_core.hitl` + dashboard)
Priority queue in Postgres (`SELECT ... FOR UPDATE SKIP LOCKED`) with a Redis-backed lease so two
reviewers never open the same task. SLA timers per policy; breach triggers escalation or
policy-defined default. Reviewer decisions are written as labelled examples and consumed by
`threshold_tuner.py` to refit τ/ρ per namespace — the queue is a training signal, not a cost center.

### 2.8 LLM Router & Fallback (`guardmem_core.llm`)
Tiering: **FAST** (Haiku/gpt-4o-mini) for noise filtering and low-risk extraction; **BALANCED**
(Sonnet-class) for conflict adjudication and NLI; **FRONTIER** (Opus-class) only on `ESCALATE`.
Per-provider circuit breakers (half-open probes), hedged requests above p95 latency, cross-provider
fallback with prompt-template equivalence tests in CI. Budget ledger per tenant; on hard cap the
router stops serving AUTO_WRITE paths and routes everything to HITL.

---

## 3. Deployment Topology

```
                       ┌────────── Cloud Load Balancer + Cloud Armor ──────────┐
                       │                                                       │
        ┌──────────────▼───────────┐   ┌──────────────────┐   ┌────────────────▼──────┐
        │ Cloud Run: gateway       │   │ Cloud Run: mcp   │   │ Cloud Run: dashboard  │
        │ min 1 / max 50, 2vCPU    │   │ streamable-http  │   │ Next.js (BFF only)    │
        └──────┬───────────────┬───┘   └────────┬─────────┘   └───────────────────────┘
               │               │                │
     ┌─────────▼───┐   ┌───────▼────────┐  ┌────▼──────────────┐
     │ Memorystore │   │ Cloud SQL PG16 │  │ Neo4j Aura /      │
     │ Redis       │   │ + pgvector     │  │ self-hosted GKE   │
     │ queue+cache │   │ + RLS + outbox │  └───────────────────┘
     └──────┬──────┘   └───────┬────────┘
            │                  │            ┌──────────────────┐
     ┌──────▼───────────┐      └───────────►│ GCS: payload blobs│
     │ Cloud Run Jobs:  │                   │ + audit exports   │
     │ worker (arq)     │                   └──────────────────┘
     │ + Scheduler cron │
     └──────────────────┘

 Sidecar everywhere: OTel Collector → Langfuse (self-host) + Phoenix + Managed Prometheus
 Secrets: Secret Manager · Images: Artifact Registry (cosign-signed, SBOM attached)
```

**Dev:** `docker compose -f infra/docker/docker-compose.dev.yml up` brings up Postgres+pgvector,
Neo4j, Redis, Langfuse, Phoenix, and Ollama for offline model calls. `make seed` loads a demo
tenant with the clinical ontology and 200 synthetic turns.

---

## 4. Failure Modes & Degradation Matrix

| Failure | Detection | Behavior | Never |
|---|---|---|---|
| Extraction provider down | circuit breaker open | fallback provider → if all down, park proposal in `pending_eval`, agent still reads | auto-write unscored |
| Graph store down | health probe | vector-only write, `graph_pending` flag, outbox retries | drop the assertion |
| Vector store down | health probe | reject writes, serve graph-only recall, alert | serve stale index silently |
| Budget cap hit | ledger check | AUTO_WRITE disabled → all candidates to HITL | unlogged write |
| Policy engine error | exception | fail closed → HITL | skip policy |
| Injection detected | classifier + heuristics | quarantine namespace, alert, no primary write | proceed with "sanitized" text |
| HITL queue backlog > SLA | queue depth metric | escalate, widen auto-write **only** if policy explicitly permits, else park | silently auto-approve |
| Reviewer disagreement spike (κ drop) | nightly eval | freeze threshold auto-tuning, page owner | keep tuning on bad labels |

---

## 5. Data Model (essentials)

```sql
-- Postgres: source of truth for assertions + audit
CREATE TABLE assertion (
  id            UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL,
  namespace     TEXT NOT NULL,
  subject_id    UUID NOT NULL,             -- entity
  predicate     TEXT NOT NULL,
  object_json   JSONB NOT NULL,
  confidence    REAL NOT NULL,
  risk          REAL NOT NULL,
  -- bitemporal
  valid_from    TIMESTAMPTZ NOT NULL,
  valid_to      TIMESTAMPTZ,               -- NULL = currently believed
  recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  retracted_at  TIMESTAMPTZ,
  superseded_by UUID REFERENCES assertion(id),
  -- provenance lives in its own table; see below
  corroboration_count INT NOT NULL DEFAULT 1,
  trace_id      TEXT NOT NULL,
  visible       BOOLEAN NOT NULL DEFAULT false,   -- set true after dual-write lands
  embedding     VECTOR(1024)
);

-- One row per citation, not a column pair on `assertion`. Settled at S3.1.
-- MEMORY_ENGINE 2.4 resolves a duplicate by *appending* a Provenance and
-- bumping corroboration_count, and 3.2's S_cor term is a function of how many
-- independent sources a fact has - so a single pair of columns cannot represent
-- a corroborated fact at all. `alignment` is ADR-0007's.
CREATE TABLE provenance (
  id            UUID PRIMARY KEY,
  assertion_id  UUID NOT NULL REFERENCES assertion(id) ON DELETE CASCADE,
  source_hash   TEXT NOT NULL,
  source_span   INT4RANGE NOT NULL,
  source_tier   TEXT NOT NULL,
  verbatim      TEXT NOT NULL,
  alignment     REAL NOT NULL DEFAULT 1.0,
  captured_at   TIMESTAMPTZ NOT NULL,
  CHECK (NOT isempty(source_span))                -- an empty range cites nothing
);
CREATE INDEX ON assertion USING hnsw (embedding vector_cosine_ops)
  WHERE valid_to IS NULL AND visible;
ALTER TABLE assertion ENABLE ROW LEVEL SECURITY;
ALTER TABLE assertion FORCE ROW LEVEL SECURITY;   -- the owner is exempt without this

-- RULES.md 1.1 ("no unsourced write") was a NOT NULL column before provenance
-- moved to its own table. It is now a DEFERRED constraint trigger, checked at
-- COMMIT because the assertion and its citations are written in one
-- transaction - which the outbox pattern in 2.4 already requires.
CREATE CONSTRAINT TRIGGER assertion_requires_provenance
  AFTER INSERT ON assertion DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION assert_provenance_exists();

CREATE TABLE audit_event (
  seq         BIGSERIAL PRIMARY KEY,
  tenant_id   UUID NOT NULL,
  trace_id    TEXT NOT NULL,
  kind        TEXT NOT NULL,               -- DECISION | WRITE | REVIEW | POLICY_CHANGE | ...
  payload     JSONB NOT NULL,
  prev_digest BYTEA NOT NULL,
  digest      BYTEA NOT NULL,              -- sha256(payload ‖ prev_digest)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

```cypher
// Neo4j: relational entity state
(:Entity {id, tenant_id, type, canonical_name})
  -[:ASSERTS {assertion_id, predicate, confidence, valid_from, valid_to, trace_id}]->
(:Entity|:Literal)
(:Assertion {id})-[:SUPERSEDES]->(:Assertion {id})
(:Assertion {id})-[:SOURCED_FROM]->(:Source {hash, tier})
```

**`:Entity.id` is derived, not opaque** (ADR-0008). For a subject-bound
namespace — `<type>:<id>`, whose type names a declared ontology entity — it is
`uuid5(NAMESPACE_URL, "guardmem/{tenant_id}/entity/{namespace}")`, so the id of
"the subject of namespace N in tenant T" is a pure function available before any
I/O, and creation is idempotent under `ON CONFLICT DO NOTHING`.
`canonical_name` is the first surface form seen for that entity and is display
only: **nothing matches on it.** A subject that is neither namespace-bound nor
named by `hints.subject` is refused rather than guessed at.
