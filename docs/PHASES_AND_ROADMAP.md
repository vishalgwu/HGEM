# PHASES_AND_ROADMAP.md — 4-Week Execution Plan

**Assumption:** one focused builder (plus Claude Code), ~6 productive hours/day, 28 days to a
demoable alpha with real evals. Scope is deliberately ruthless — the plan below is what gets built,
and §6 is the list of things that are explicitly *not* getting built.

**The single ordering principle:** the decision engine ships before the gateway, the gateway before
the UI, and the evals before the demo. Anything that can't be measured by day 28 doesn't go in.

---

## Phase 1 — Core Engine & MCP (Days 1–7)

> Goal: `guardmem_core.pipeline.run()` takes raw text and returns an audited decision, and an MCP
> client can drive it end-to-end from Claude Desktop.

| Day | Deliverable | Done when |
|---|---|---|
| 1 | Repo scaffold: uv workspace, ruff/mypy/pre-commit, CI skeleton, docker-compose.dev (PG+pgvector, Neo4j, Redis) | `make dev` boots; `make test` runs an empty green suite |
| 1 | Full Pydantic schema layer (`schemas/*`) + error hierarchy + `NewType` ids | `mypy --strict` clean; schemas round-trip in property tests |
| 2 | L1: noise filter (rules + FAST classifier), K-sample extraction, span linker | golden-file test: 40 transcript turns → expected candidates, 100% have spans |
| 3 | Store layer: `VectorStore`/`GraphStore` protocols, pgvector + NetworkX impls, bitemporal schema, outbox | integration test writes an assertion, both sides land, `visible` flips |
| 3 | Alembic migrations + RLS policies + seed script | `make seed` produces a demo tenant with the clinical ontology |
| 4 | L2: schema gate, conflict detection (NLI + cardinality + temporal), dedupe/merge | contradiction-probe set (60 pairs hand-built) classified ≥ 90% correctly |
| 5 | L3: semantic entropy, confidence composite, impact risk, `decide()` | `decide()` passes determinism + totality property tests (500 examples) |
| 5 | Audit chain (hash-linked events) + `replay_trace.py` | replay of a recorded trace reproduces the identical decision |
| 6 | MCP server: `memory.search`, `memory.propose`, `memory.commit`, `memory.get_entity`; stdio transport | Claude Desktop connects, proposes a fact, and it lands in Postgres with provenance |
| 7 | MCP resources + prompts; Neo4j store impl; hardening pass; unit coverage push | `guardmem-core` ≥ 85% coverage; MCP contract tests green |

**Phase 1 exit gate**
- [ ] End-to-end from Claude Desktop: propose → decide → write → `memory.search` returns it with provenance
- [ ] A contradictory second fact supersedes the first, with tombstone and audit event
- [ ] `replay_trace.py` reproduces any decision deterministically
- [ ] Zero unsourced writes possible (property test + DB constraint both enforce it)

**Risk:** NLI quality on typed/short objects. Mitigation: hybrid — cross-encoder for prose,
deterministic comparators for coded/numeric values, LLM judge only in the ambiguous band.

---

## Phase 2 — Gateway, Routing & Guardrails (Days 8–14)

> Goal: production ingress with model routing, fallback, security armor, and budget control.

| Day | Deliverable | Done when |
|---|---|---|
| 8 | FastAPI gateway: auth (API key + OIDC), tenancy + RLS context, rate limit, idempotency, `/healthz` `/readyz` `/metrics` | OpenAPI published; schemathesis contract suite green |
| 8 | Async path: Redis stream + arq worker; `mode=async` returns 202 with `trace_id` | p95 on `propose` (async) < 80 ms under locust load |
| 9 | LLM router: FAST/BALANCED/FRONTIER tiers, provider adapters (Anthropic, OpenAI, Ollama), pinned model ids | tier selection unit-tested against risk hints and namespace policy |
| 9 | Circuit breakers, cross-provider fallback, hedged requests, prompt caching, semantic response cache | chaos test: kill primary provider mid-load → zero unscored writes, all degrade to HITL |
| 10 | Budget ledger: per-tenant token/$ accounting, soft alerts, hard cap → HITL-only degradation | cost per governed candidate reported per trace |
| 11 | Security armor: injection detection (heuristics + classifier + canary/spotlighting), quarantine namespace | redteam corpus v1 (120 attacks) → 0% land in primary namespace |
| 11 | PII: Presidio recognizers + custom clinical/legal patterns, reversible tokenization vault (separate KMS) | invariant I7 test: no PII in any span attribute, log line, or provider prompt |
| 12 | Poisoning defense: source trust tiers, per-source write rate limits, corroboration quorum | web-tier content cannot auto-write HIGH impact — enforced and tested |
| 12 | Policy engine: Rego-compatible packs, versioned, hot-reload, dry-run mode | policy change is an audited event; dry-run reports projected impact |
| 13 | Observability: OTel spans end-to-end, Langfuse + Phoenix exporters, Prometheus metrics, Grafana SLO dashboard | one trace visible across all three backends with consistent `trace_id` |
| 14 | Compaction worker: decay scoring, tiering, tombstone sweep, episodic rollup; nightly schedules | 30-day simulated namespace: stale-fact rate drops below 2% after GC |

**Phase 2 exit gate**
- [ ] Latency SLAs in `PRD.md` §6.1 met under 50 rps synthetic load
- [ ] Every failure mode in `ARCHITECTURE.md` §4 verified by a chaos test
- [ ] Injection ASR into primary namespace: 0%
- [ ] Blended cost per governed candidate measured and under the $0.0009 envelope

---

## Phase 3 — Dashboard & HITL Queue (Days 15–21)

> Goal: an engineer can tune the system and a nurse can clear a queue, both without reading docs.

| Day | Deliverable | Done when |
|---|---|---|
| 15 | Next.js app shell, design tokens, shadcn setup, BFF route handlers, auth session | Lighthouse ≥ 90; no tenant token reaches the browser |
| 15 | Telemetry aggregation endpoints + SSE stream | dashboard updates < 2 s behind live traffic |
| 16 | Control Panel: metric tiles, extraction funnel (with dropped-item sampling), decision mix | funnel numbers reconcile exactly with audit-log counts |
| 17 | Latency panel, model routing/cost panel, live trace stream with filters | clicking any tile filters every panel |
| 17 | Trace detail: waterfall, per-candidate "why" breakdown, K-sample clusters, source span, replay button | replay diff visible in-app |
| 18 | HITL queue backend: priority scoring, lease-based assignment, SLA timers, escalation, sweeper | two concurrent reviewers never receive the same task (integration test) |
| 19 | Review Task UI: ProvenanceCard, DiffPane, DecisionBar, keyboard shortcuts, undo window | keyboard-only walkthrough of 10 tasks with no mouse |
| 19 | Edit flow with ontology-driven typed inputs + coded-value autocomplete | reviewer edit persists as a labelled correction with reviewer identity |
| 20 | C×R threshold editor with projected-impact preview and audited apply | threshold change writes a versioned policy event |
| 20 | `threshold_tuner.py`: refit τ/ρ and risk β from reviewer labels, κ-gated promotion | tuner run on synthetic labels moves thresholds sensibly and is blocked when κ < 0.6 |
| 21 | Playwright e2e on both surfaces; accessibility pass (WCAG 2.2 AA); empty/loading/offline states | axe clean; e2e green in CI |

**Phase 3 exit gate**
- [ ] Median review time < 25 s in a timed run of 20 seeded tasks
- [ ] Reviewer decisions visibly close the loop (κ reported, tuner consuming labels)
- [ ] Control panel answers "why was this flagged?" in ≤ 2 clicks from any trace

---

## Phase 4 — Evals, Benchmarks & Deployment (Days 22–28)

> Goal: numbers instead of claims, and a URL instead of a localhost demo.

| Day | Deliverable | Done when |
|---|---|---|
| 22 | Eval harness + datasets: LongMemEval subset, contradiction probe (400 pairs), redteam corpus, three vertical sets | `make eval` produces a versioned report |
| 22 | RAG quality suite: faithfulness, answer relevance, context precision/recall | scores reproducible ±2% across runs |
| 23 | Memory integrity suite: write precision (n=300 human-audited sample), contradiction escape rate, stale fact rate | targets in `PRD.md` §6.2 measured, gaps triaged |
| 23 | Drift@N: 100-turn synthetic agent runs across 3 verticals, with and without GuardMem | drift curve chart — this is the money slide |
| 24 | Baseline comparison: raw-RAG, mem0, Zep on the same datasets | honest table including where GuardMem loses (latency, setup cost) |
| 24 | Security suite: injection/poisoning ASR, tenant isolation attempts across REST/MCP/SDK | ASR 0% into primary; isolation attempts all fail |
| 25 | Load benchmarks (locust): write path, read path @ 10M vectors; p50/p95/p99 published | SLA table filled with measured numbers, not targets |
| 25 | Cost benchmark: blended $/governed candidate vs frontier-only baseline | ≥ 55% token savings demonstrated with the routing ladder |
| 26 | Terraform: Cloud Run (gateway, mcp, dashboard) + Cloud Run Jobs (worker) + Cloud SQL + Memorystore + Secret Manager | `terraform apply` on a clean project produces a working env |
| 26 | CI/CD: build → trivy/gitleaks/pip-audit → cosign sign + SBOM → deploy staging → smoke → prod | one-command release; rollback tested |
| 27 | Nightly eval workflow with regression gating vs pinned baseline | a deliberate scoring regression is caught and blocks the merge |
| 27 | Runbooks (poisoning incident, queue backlog, provider outage) + a game-day walkthrough | each runbook executed once against staging |
| 28 | README with the drift chart, 3-minute demo video, ADRs finalized, public eval report | a stranger can run `docker compose up` and reproduce the headline numbers |

**Phase 4 exit gate**
- [ ] Deployed, reachable, load-tested, with published measured SLAs
- [ ] Eval report with baselines and stated limitations
- [ ] Nightly regression gate live
- [ ] Demo runs cold from a clean clone

---

## 5. Milestone Chart

```
Week 1 ████████ Core Engine + MCP        → "it decides, and I can prove why"
Week 2 ████████ Gateway + Guardrails     → "it's fast, safe, and cheap"
Week 3 ████████ Dashboard + HITL         → "a human can actually use it"
Week 4 ████████ Evals + Deploy           → "here are the numbers, here's the URL"

Demo-ready checkpoints:
  Day  7  ▸ Claude Desktop writes a governed fact                 (technical proof)
  Day 14 ▸ injection attempt quarantined, live on a dashboard     (security proof)
  Day 21 ▸ nurse clears a real queue in under 30s                 (product proof)
  Day 28 ▸ drift curve: with vs without GuardMem over 100 turns   (value proof)
```

---

## 6. Explicitly Deferred (post-alpha)

Multi-agent shared-memory arbitration · fine-tuned in-house extraction model · air-gapped installer ·
mobile reviewer app · automatic ontology induction from corpora · federated/cross-tenant learning ·
SOC 2 audit engagement (evidence collection starts now; the audit doesn't) · marketplace of
community policy packs · streaming/incremental extraction mid-turn.

Each of these is a real feature. None of them changes whether the core claim — *governed memory
measurably reduces drift* — is true, which is the only thing week 4 needs to establish.

---

## 7. Weekly Risk Review

| Week | Top risk | Early warning | Response |
|---|---|---|---|
| 1 | Scoring math looks principled but doesn't separate good from bad writes | AUROC of `C` against hand-labelled candidates < 0.75 on a 200-item dev set | Drop to a simpler ensemble; entropy alone is a strong baseline — ship that and iterate |
| 2 | Latency budget blown by NLI + K-sampling | p95 eval > 2.5 s | Batch NLI, quantize the cross-encoder, cut K on LOW risk, move more work off the strict path |
| 3 | HITL volume too high to be usable | > 25% of candidates routed to review on the demo tenant | Tune τ/ρ per namespace with the tuner; tighten the noise filter; raise the corroboration bar only where impact demands it |
| 4 | Evals show no drift improvement vs baseline | Drift@100 curves overlap | This is the falsification point — publish it honestly, and dig into whether the failure is retrieval (GC not aggressive enough) or write-path (thresholds too loose) |
