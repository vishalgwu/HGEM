# PRD.md — GuardMem AI

**Status:** v1.0 draft · **Owner:** Founding Eng · **Horizon:** 4 weeks to design-partner alpha

---

## 1. Product Vision

Every serious agent deployment eventually writes to long-term memory. Almost none of them govern
that write. The result is a class of failure that looks nothing like a bad answer: an agent that
was correct on turn 3, absorbed a wrong fact on turn 40, and has been confidently wrong for six
weeks because nothing sat between the model and the datastore.

GuardMem AI is that missing layer — a **memory governance gateway**. It intercepts every candidate
fact before it becomes durable state, scores it for confidence and blast radius, routes the
ambiguous middle to a human, and keeps a hash-chained lineage of who decided what and why.

> **Vision statement:** Long-lived agents should be *auditable state machines*, not text files that
> grow until they rot.

### YC Elevator Pitch

> **GuardMem AI is Datadog + a compliance officer for AI agent memory.**
> Enterprise teams are shipping agents that remember things across months — clinical intake bots,
> contract review copilots, underwriting assistants. Today those agents write to memory with zero
> validation, so one hallucinated fact silently corrupts every downstream decision, and nobody can
> prove afterward where it came from. GuardMem sits in front of the memory store: it scores each
> candidate fact for uncertainty and impact, auto-writes the safe 80%, queues the risky 15% for a
> one-click expert review, and rejects the rest — with full provenance on every write. Drop-in
> middleware, MCP-native, self-hostable. We make agent memory something a regulator can read.

---

## 2. Problem Statements

**P1 — Silent memory drift.** Agents extract facts with no uncertainty estimate. A single low-
confidence extraction ("patient is allergic to penicillin" from an ambiguous transcript) is stored
with the same authority as a confirmed one. There is no signal distinguishing them at read time.

**P2 — Thread rot / context pollution.** Memory grows monotonically. Superseded facts are never
retired, so retrieval returns both "lives in Boston" (2023) and "lives in Austin" (2026). The model
picks one, effectively at random, per turn. Context windows fill with dead weight; cost rises and
accuracy falls simultaneously.

**P3 — Catastrophic state corruption.** Some writes are unrecoverable in practice: overwriting a
1:1 predicate, deleting an entity with 400 downstream edges, or poisoning a shared org-level
namespace. Nothing today distinguishes "append a hobby" from "rewrite the account owner."

**P4 — Memory poisoning as an attack surface.** Injected instructions inside retrieved documents,
user messages, or tool outputs can write attacker-chosen facts into persistent memory — a durable
prompt injection that survives every session reset.

**P5 — No audit lineage.** When a regulated customer asks *"why did the agent believe this?"*, the
honest answer today is a vector ID. There is no source span, no model version, no decision record,
no reviewer signature.

**P6 — Unbounded and unattributable cost.** Every extraction hits a frontier model because nobody
built the routing tier. Memory writes are 5–15% of quality impact and 40%+ of token spend.

---

## 3. Target Verticals

| Vertical | Memory that must not drift | Regulatory anchor | Reviewer persona | Wedge |
|---|---|---|---|---|
| **Healthcare** | allergies, meds, dx history, care-plan state, consent flags | HIPAA, 21 CFR Part 11 (e-sig on review), state scope-of-practice | RN / clinical documentation specialist | Intake & scribe agents already generate structured facts nobody validates |
| **Legal** | party names, effective dates, obligation ownership, matter status, privilege flags | ABA Model Rule 1.1/1.6, privilege preservation, e-discovery holds | Paralegal / associate | Contract-review copilots that must never assert an unsupported clause term |
| **FinTech** | KYC identity attributes, risk tier, beneficial ownership, adverse-media findings | SR 11-7 model risk, FCRA adverse action, GDPR Art. 22 explainability | Compliance analyst | Underwriting/AML agents where every stored attribute needs a source citation |

**Beachhead:** Healthcare intake & clinical documentation agents. Highest cost of a wrong durable
fact, an existing culture of human sign-off (so HITL is not a new behavior), and an existing budget
line for documentation QA.

---

## 4. Personas & Jobs

| Persona | Job to be done | Success looks like |
|---|---|---|
| **AI Platform Engineer** (buyer) | Ship an agent that survives 6 months of production without state decay | Drop-in `remember()` call; drift dashboard flat over 90 days |
| **Domain Reviewer** (nurse/paralegal/analyst) | Clear the flagged queue without leaving their workflow | median review time within the §6.2 target, keyboard-only, source span visible without clicking out |
| **Compliance Officer** | Prove why the agent believed X on date Y | Exportable, tamper-evident lineage per assertion |
| **Eng Manager** | Cut memory-path token spend without hurting quality | Cost per governed write trending down, write precision flat |

---

## 5. Functional Requirements

### FR-1 Memory Ingestion & Extraction
- **FR-1.1** Accept a `MemoryProposal` (raw turn(s), tool outputs, or documents) over REST, MCP, or SDK.
- **FR-1.2** Layer-1 noise reduction MUST drop ephemeral/imperative/chit-chat content before extraction billing.
- **FR-1.3** Extraction MUST produce structured `MemoryCandidate` objects with a **verbatim source span** (char offsets + document hash). A candidate without a span is rejected, not stored.
- **FR-1.4** Extraction MUST support K-sample generation for uncertainty estimation, with K set by risk hint: 1 (FAST) on `risk_hint=LOW`, 3 (FAST) by default, 5 (BALANCED) on `risk_hint=HIGH`. See `MEMORY_ENGINE.md` §1.2.

### FR-2 Validation & Conflict Detection
- **FR-2.1** Every candidate validated against a tenant-scoped entity/predicate ontology (Pydantic + JSON-Schema); unknown predicates go to `quarantine` namespace, never to primary.
- **FR-2.2** Conflict detection MUST run three independent checks: NLI contradiction vs top-k neighbors, predicate cardinality violation, and temporal-validity overlap.
- **FR-2.3** Semantic deduplication MUST merge (not duplicate) candidates with cosine ≥ 0.95 **and** bidirectional entailment.
- **FR-2.4** Contradictions MUST resolve by **supersession with tombstone**, never destructive overwrite. Prior assertion remains queryable with `valid_to` set.

### FR-3 Scoring & Decision
- **FR-3.1** Compute `ConfidenceReport` (semantic entropy, source grounding, schema fit, corroboration) and `RiskVerdict` (impact/blast radius) per candidate — see `MEMORY_ENGINE.md`.
- **FR-3.2** Emit exactly one `Decision ∈ {AUTO_WRITE, HITL_REVIEW, REJECT, ESCALATE}`; `ESCALATE` re-runs scoring on a frontier model exactly once, then must terminate in a non-escalate decision.
- **FR-3.3** Decision thresholds MUST be per-tenant, per-namespace configurable and versioned; a threshold change is an audited event.
- **FR-3.4** Every decision MUST carry a machine-readable `rationale` (contributing feature vector + triggered rules), not free text alone.

### FR-4 Dual-Store Persistence
- **FR-4.1** Vector store for semantic recall (pgvector default, Qdrant at >10M vectors); graph store for relational entity state (Neo4j; NetworkX for dev/single-tenant).
- **FR-4.2** Writes to both stores MUST be transactionally coordinated via outbox pattern — no partially-written assertion is ever visible to retrieval.
- **FR-4.3** All assertions bitemporal: `valid_from/valid_to` (world time) + `recorded_at/retracted_at` (system time). Point-in-time reconstruction is a first-class query.
- **FR-4.4** Hybrid retrieval: dense + BM25 + 1-hop graph expansion, with confidence and recency as ranking features.

### FR-5 Compression & Garbage Collection
- **FR-5.1** Nightly compaction computes a decay score per assertion; below threshold → cold tier or episodic rollup summary.
- **FR-5.2** Context assembly MUST enforce a token budget per request and return the *why* of every inclusion/exclusion in the trace.
- **FR-5.3** GC MUST never remove an assertion referenced by an open review task, a legal hold, or an audit export in progress.

### FR-6 Guardrails
- **FR-6.1** Prompt-injection detection on all untrusted content prior to extraction; detections quarantine the whole proposal and raise an alert.
- **FR-6.2** PII detection + reversible tokenization; the vault is separately encrypted and separately access-controlled from the memory stores.
- **FR-6.3** Poisoning defense: source trust tiers, per-source write rate limits, and a corroboration quorum (≥2 independent sources) required for HIGH-impact writes.
- **FR-6.4** Policy packs evaluated as code (Rego-compatible), hot-reloadable, versioned, with dry-run mode.

### FR-7 Gateway, Routing & Fallback
- **FR-7.1** Three model tiers (FAST / BALANCED / FRONTIER) selected by risk hint + namespace policy.
- **FR-7.2** Circuit breaker per provider with automatic cross-provider fallback; degraded mode MUST fail *closed* into HITL, never silently auto-write.
- **FR-7.3** Prompt-cache headers and semantic response caching on extraction prompts.
- **FR-7.4** Per-tenant token/$ ledger with soft alert and hard cap; hard cap degrades to HITL-only, not to unlogged writes.

### FR-8 Observability & Audit
- **FR-8.1** One OTel trace per proposal spanning extract → validate → score → decide → write, exported to Langfuse and Arize Phoenix.
- **FR-8.2** Append-only, hash-chained audit log (each event embeds the prior event's digest); export as signed JSONL.
- **FR-8.3** `replay_trace.py` MUST deterministically reproduce any past decision given pinned model/prompt/policy versions.

### FR-9 Human-in-the-Loop
- **FR-9.1** Priority queue ordered by `impact × staleness × SLA-burn`, assignment by reviewer skill + namespace.
- **FR-9.2** Review UI shows candidate, conflicting incumbent, source span, and model rationale on one screen; approve/edit/reject in ≤2 keystrokes.
- **FR-9.3** Reviewer decisions are labelled data — they feed `threshold_tuner.py` and are reported as Cohen's κ against the model's decision.
- **FR-9.4** SLA breach escalates and (per policy) may auto-reject rather than auto-accept.

### FR-10 MCP & Integrations
- **FR-10.1** MCP server exposing tools/resources/prompts (see `MCP_INTEGRATION.md`), stdio + streamable-HTTP.
- **FR-10.2** First-party adapters: LangGraph, LangChain, LlamaIndex, CrewAI, Claude Desktop, Cursor.

---

## 6. Non-Functional Requirements

### 6.1 Latency SLAs

| Path | p50 | p95 | p99 | Notes |
|---|---|---|---|---|
| `memory:propose` (async accept) | 25 ms | **80 ms** | 150 ms | write-ahead accept; eval runs off-queue |
| `memory:propose` (sync, `mode=strict`, K=1 FAST) | 180 ms | **450 ms** | 900 ms | single fast-tier extraction |
| Full eval, K=5 + conflict NLI | 600 ms | **1.6 s** | 3.0 s | worker path; not user-blocking |
| `memory:search` @ 10M vectors | 30 ms | **80 ms** | 160 ms | hybrid, top-20, 1-hop expand |
| HITL enqueue | 10 ms | **40 ms** | 90 ms | |
| Dashboard telemetry SSE lag | — | **2 s** | 5 s | |

**Availability:** 99.9% monthly on read path, 99.5% on eval path. Read path MUST remain
available when the eval path is down (degrade to no-new-writes, not no-recall).

### 6.2 Quality Targets (alpha gate)

| Metric | Definition | Target |
|---|---|---|
| Write Precision | approved-correct writes / total auto-writes (human-audited sample n=300) | ≥ 0.97 |
| Contradiction Escape Rate | contradictory pairs both live after 100 turns | ≤ 1% |
| Stale Fact Rate | superseded facts still returned in top-5 recall | ≤ 2% |
| HITL Volume | share of candidates routed to human | 10–18% |
| Reviewer Agreement (κ) | model decision vs reviewer | ≥ 0.75 |
| Injection Attack Success Rate | redteam corpus writes that land in primary namespace | 0% (quarantine allowed) |
| Token Savings | vs naive "extract everything on frontier model" baseline | ≥ 55% |
| Median Review Time | wall-clock per HITL task, timed run of 20 seeded tasks | ≤ 25 s |

**Median review time** is the one number three documents used to disagree about, so it is owned here.
≤ 25 s is the alpha acceptance gate and the only figure a gate or exit criterion may cite.
`DESIGN_SYSTEM.md` §3.1 sets a *design* target of 18 s — that is the bar the interface is built to,
deliberately tighter than the gate, and it is not an acceptance criterion.

### 6.3 Security & Compliance
- Tenant isolation at row level (Postgres RLS) **and** per-tenant namespace prefixing in vector/graph stores.
- Encryption at rest (CMEK-capable) and in transit (mTLS between services).
- Secrets in Secret Manager/Vault; zero secrets in env files in prod; gitleaks in CI.
- AuthN: OIDC (SSO) for humans, mTLS + scoped API keys for services. AuthZ: RBAC roles
  `agent.writer`, `memory.reader`, `review.approver`, `policy.admin`, `audit.reader`.
- Data residency selectable per tenant; PII vault regionally pinned.
- Roadmap: SOC 2 Type I evidence collection from day one (audit log is the primary artifact),
  HIPAA BAA readiness, GDPR Art. 17 erasure via crypto-shredding of vault tokens.

### 6.4 Audit Lineage Requirement
Every stored assertion resolves to: source document hash + char span, extraction model + prompt
version, K-sample entropy, confidence & risk vectors, policy pack version, decision, reviewer
identity + timestamp (if any), and the previous audit-chain digest. Retention: 7 years default,
tenant-configurable.

### 6.5 Cost Envelope
Target ≤ **$0.0009** blended per governed candidate at 1M candidates/month (FAST-tier majority,
cache hit ≥ 40%, FRONTIER escalation ≤ 6%).

---

## 7. Scope

**In (v1):** write-path governance, dual store, HITL queue, MCP server, dashboard, evals, Cloud Run deploy.
**Out (v1):** multi-agent shared-memory arbitration, fine-tuned in-house extraction model, on-prem
air-gapped installer, mobile reviewer app, automatic ontology induction.

## 8. Risks

| Risk | Mitigation |
|---|---|
| HITL volume exceeds reviewer capacity → queue is theater | Threshold auto-tuning from reviewer labels; SLA sweeper; per-namespace volume caps with policy-defined overflow behavior |
| "Just use mem0/Zep" objection | Those are memory *stores*; GuardMem is the *control plane* and integrates with them as a backend rather than replacing them |
| Latency added to agent loop | Write-ahead accept: agent never blocks on evaluation |
| Semantic entropy cost (K samples) | K=1 fast path on LOW risk; K scales with risk hint; cached prefixes |
| Ontology authoring burden | Ship vertical starter packs (clinical, legal, fintech) with the product |
