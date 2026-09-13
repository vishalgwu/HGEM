# PROJECT_TREE.md — GuardMem AI Repository Blueprint

**Repo strategy:** single monorepo, `uv` workspace for Python packages + `pnpm` workspace for TS.
Deployable units are thin; all intelligence lives in `packages/guardmem-core` so the Gateway,
Worker, and MCP Server share one decision engine and one audit trail.

```
guardmem-ai/
├── README.md
├── DAILY_LOG.md                          # created S0.4, written every day (BUILD_NOTEBOOK 0.6)
├── LICENSE                              # Apache-2.0 (core) — see licensing note below
├── CHANGELOG.md
├── SECURITY.md                          # disclosure policy, threat model summary
├── CONTRIBUTING.md
├── Makefile                             # make dev / test / eval / bench / migrate / seed
├── pyproject.toml                       # uv workspace root
├── uv.lock
├── .python-version                      # 3.12 - keeps lock, venv and ruff target in agreement
├── requirements.txt                     # runtime aggregate over requirements/
├── requirements-dev.txt                 # runtime + dev toolchain
├── requirements.lock.txt                # fully-resolved pip-installable lock
├── requirements/                        # layered, one file per dependency layer
├── pnpm-workspace.yaml
├── turbo.json
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml              # ruff, ruff-format, mypy, import-linter, gitleaks, detect-secrets
├── .secrets.baseline                    # detect-secrets reviewed findings - POSIX paths, UTF-8 hashes
├── .gitleaks.toml                       # allowlists .secrets.baseline only; useDefault = true
├── .gitattributes                       # line-ending determinism; *.pdf binary protects the master notebook
├── .dockerignore
│
├── docs/                                 # specification suite
│   ├── README.md                         # index + spec ownership
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── RULES.md
│   ├── MEMORY_ENGINE.md
│   ├── DESIGN_SYSTEM.md
│   ├── MCP_INTEGRATION.md
│   ├── PHASES_AND_ROADMAP.md
│   ├── PROJECT_TREE.md                   # this file
│   ├── BUILD_NOTEBOOK.md                 # editable master notebook
│   ├── GuardMem_AI_Master_Build_Notebook.pdf   # frozen original - never edit or delete
│   ├── adr/                             # architecture decision records
│   │   ├── 0001-dual-store-vector-plus-graph.md
│   │   ├── 0002-bitemporal-tombstones-over-hard-delete.md
│   │   ├── 0003-write-ahead-accept-async-eval.md
│   │   ├── 0004-semantic-entropy-as-confidence-primitive.md
│   │   ├── 0005-mcp-as-primary-agent-surface.md
│   │   └── 0006-extraction-result-carries-the-sample-sets-and-the-unsourced-count.md
│   ├── runbooks/
│   │   ├── incident-memory-poisoning.md
│   │   ├── incident-hitl-queue-backlog.md
│   │   └── incident-model-provider-outage.md
│   └── diagrams/                        # nine exported PNG diagrams
│
├── packages/
│   ├── guardmem-core/                   # ← the brain. zero web framework deps.
│   │   ├── pyproject.toml
│   │   └── src/guardmem_core/
│   │       ├── __init__.py
│   │       ├── py.typed                 # PEP 561 - without it consumers see this package as untyped
│   │       ├── settings.py              # pydantic-settings, 12-factor
│   │       ├── types.py                 # NewType ids: TraceId, CandidateId, EntityId
│   │       ├── errors.py                # GuardMemError hierarchy → HTTP/MCP mapping
│   │       ├── schemas/                 # S1.6; __init__.py re-exports the layer
│   │       │   ├── base.py              # GMModel (extra=forbid/frozen/strict) + ObjectValue
│   │       │   ├── candidate.py         # MemoryCandidate, ExtractedFact, ExtractionResult
│   │       │   ├── entity.py            # Cardinality, Entity, StoredAssertion, Edge
│   │       │   ├── verdict.py           # ImpactLevel, Conflict*, ConfidenceReport,
│   │       │   │                        #   RiskVerdict, Decision, DecisionRecord
│   │       │   ├── policy.py            # ObligationKind, Obligation; Rule + PolicyPack at S12.2
│   │       │   ├── receipt.py           # SourceTier, Provenance, WriteReceipt, AuditEvent
│   │       │   ├── review.py            # ReviewTask, ReviewDecision, Diff
│   │       │   ├── turn.py              # S2.1; Turn, NoiseReason, DroppedTurn, NoiseResult
│   │       │   └── ontology.py          # Ontology, PredicateSpec - loader + validation (S3.5)
│   │       ├── prompts/                 # RULES 3: versioned prompts, never inline f-strings
│   │       │   ├── loader.py            # S2.1; render(name, version, variables), frontmatter
│   │       │   ├── classify_noise/v1.md # S2.1; the Layer-1 noise classifier
│   │       │   ├── extract_memories/v1.md   # S2.2; K-sample extraction
│   │       │   ├── adjudicate_conflict/v1.md
│   │       │   └── review_brief/v1.md
│   │       ├── ontology/                # tenant starter packs, loaded by schemas/ontology.py
│   │       │   └── {clinical,legal,fintech}.yaml
│   │       ├── pipeline/                # LAYER 1-3
│   │       │   ├── orchestrator.py      # MemoryPipeline.run() — the single entrypoint
│   │       │   ├── l1_extract/
│   │       │   │   ├── extractor.py     # S2.2; ExtractionContext, K-sampling, span link
│   │       │   │   ├── noise_filter.py  # S2.1; rules tier + one batched FAST call
│   │       │   │   ├── noise_rules.py   # S2.1; the deterministic half - pure, no I/O
│   │       │   │   └── span_linker.py   # S2.2 exact match; S2.3 adds the fuzzy fallback
│   │       │   ├── l2_validate/
│   │       │   │   ├── schema_gate.py   # entity/predicate ontology validation
│   │       │   │   ├── conflict.py      # NLI contradiction + cardinality + temporal
│   │       │   │   └── dedupe.py        # cosine + bidirectional entailment merge
│   │       │   └── l3_score/
│   │       │       ├── entropy.py       # semantic entropy over K samples
│   │       │       ├── confidence.py    # weighted confidence composite C
│   │       │       ├── impact.py        # blast-radius risk R
│   │       │       └── decision.py      # DecisionMatrix → AUTO_WRITE|HITL|REJECT|ESCALATE
│   │       ├── guardrails/
│   │       │   ├── injection.py         # prompt-injection & instruction-smuggling detect
│   │       │   ├── pii.py               # Presidio recognizers + tokenization vault
│   │       │   ├── poisoning.py         # provenance trust tiers, corroboration quorum
│   │       │   ├── toxicity.py
│   │       │   └── policy_engine.py     # OPA/Rego-compatible rule evaluation
│   │       ├── memory/
│   │       │   ├── router.py            # StoreRouter — vector vs graph vs both
│   │       │   ├── vector/
│   │       │   │   ├── base.py          # VectorStore Protocol (S1.7); store owns write-side embedding
│   │       │   │   ├── pgvector_store.py
│   │       │   │   └── qdrant_store.py
│   │       │   ├── graph/
│   │       │   │   ├── base.py          # GraphStore Protocol (S1.7)
│   │       │   │   ├── neo4j_store.py
│   │       │   │   └── networkx_store.py   # dev / single-tenant fallback
│   │       │   ├── retrieval.py         # hybrid BM25 + dense + graph-expand
│   │       │   └── compaction/
│   │       │       ├── decay.py         # recency × usage × salience scoring
│   │       │       ├── gc.py            # tombstoning, TTL tiers, thread-rot sweep
│   │       │       └── summarizer.py    # rollup of cold assertions → episodic digest
│   │       ├── llm/                     # S1.7 protocol; providers + router at S9.x
│   │       │   ├── base.py              # Tier, LLMResponse, LLMClient Protocol
│   │       │   ├── providers/           # anthropic.py openai.py bedrock.py vertex.py ollama.py
│   │       │   ├── router.py            # tier routing: FAST | BALANCED | FRONTIER
│   │       │   ├── fallback.py          # circuit breaker + hedged requests
│   │       │   ├── cache.py             # prompt-cache headers, semantic response cache
│   │       │   └── budget.py            # per-tenant token/$ ledger + hard caps
│   │       ├── observability/
│   │       │   ├── tracing.py           # OTel spans, trace_id propagation
│   │       │   ├── exporters/           # langfuse.py phoenix.py otlp.py
│   │       │   ├── metrics.py           # prometheus counters/histograms
│   │       │   ├── audit.py             # append-only, hash-chained audit log
│   │       │   └── SPANS.md             # span registry - RULES 6 needs an entry per span
│   │       └── hitl/
│   │           ├── queue.py             # priority queue, SLA timers, escalation
│   │           ├── assignment.py        # reviewer routing by skill/namespace
│   │           └── learning.py          # reviewer decisions → threshold auto-tuning
│   │
│   ├── guardmem-sdk-python/             # pip install guardmem
│   │   └── src/guardmem/
│   │       ├── client.py                # AsyncGuardMem — remember(), recall(), forget()
│   │       ├── middleware/              # langchain.py llamaindex.py crewai.py langgraph.py
│   │       └── testing.py               # InMemoryGuardMem fake for user test suites
│   │
│   └── guardmem-sdk-ts/                 # npm i @guardmem/sdk (dashboard + edge agents)
│       └── src/{client.ts,types.ts,react/useMemory.ts}
│
├── services/
│   ├── gateway/                         # FastAPI — the ingress plane
│   │   ├── Dockerfile
│   │   └── src/gateway/
│   │       ├── main.py                  # lifespan: pools, OTel, circuit breakers
│   │       ├── deps.py                  # DI container
│   │       ├── middleware/              # auth.py ratelimit.py tenancy.py request_id.py
│   │       └── routers/
│   │           ├── memory.py            # POST /v1/memory:propose|commit, GET /v1/memory:search
│   │           ├── review.py            # HITL queue REST
│   │           ├── policy.py
│   │           ├── audit.py             # lineage + provenance replay
│   │           ├── telemetry.py         # dashboard aggregates (SSE)
│   │           └── health.py            # /healthz /readyz /metrics
│   │
│   ├── worker/                          # arq (Redis) — async eval + compaction
│   │   ├── Dockerfile
│   │   └── src/worker/
│   │       ├── main.py
│   │       ├── tasks/{evaluate.py,compact.py,reindex.py,digest.py,sla_sweeper.py,
│   │       │        outbox_relay.py}
│   │       └── schedules.py             # cron: nightly GC, hourly drift probe
│   │
│   └── mcp_server/                      # stdio + streamable-HTTP transports
│       ├── Dockerfile
│       └── src/mcp_server/
│           ├── server.py
│           ├── tools/{search.py,propose.py,commit.py,get_entity.py,timeline.py,
│           │        forget.py,policy.py,audit.py,review.py}
│           ├── resources/{memory.py,policy.py,ontology.py,audit.py}
│           └── prompts/{extraction.py,adjudication.py,review_brief.py}
│
├── apps/
│   └── dashboard/                       # Next.js 15 (App Router) + React 19 + TS
│       ├── Dockerfile
│       ├── app/
│       │   ├── (control)/overview/      # Engineer Control Panel
│       │   ├── (control)/traces/[traceId]/
│       │   ├── (control)/cost/
│       │   ├── (control)/policies/
│       │   ├── (review)/queue/          # Human Review Queue
│       │   ├── (review)/queue/[taskId]/ # diff + provenance viewer
│       │   └── api/                     # BFF route handlers (server-only tokens)
│       ├── components/
│       │   ├── charts/{LatencyHistogram,CostSankey,FunnelExtraction}.tsx
│       │   ├── review/{DiffPane,ProvenanceCard,DecisionBar,KeyboardHUD}.tsx
│       │   └── ui/                      # shadcn primitives
│       ├── lib/{api.ts,sse.ts,keys.ts}
│       └── e2e/                         # Playwright
│
├── infra/
│   ├── docker/
│   │   ├── docker-compose.dev.yml       # postgres+pgvector, redis, neo4j, phoenix
│   │   ├── initdb/                      # Postgres first-boot SQL: vector, pgcrypto, pg_trgm
│   │   ├── docker-compose.test.yml
│   │   └── docker-compose.observability.yml   # otel-collector, prometheus, grafana, tempo
│   │                                    # + langfuse, which needs clickhouse/minio from v3 on
│   ├── terraform/
│   │   ├── modules/{cloud_run,cloud_sql,memorystore,secret_manager,artifact_registry}/
│   │   └── envs/{dev,staging,prod}/
│   ├── k8s/                             # optional helm chart for self-hosted enterprise
│   │   └── helm/guardmem/{Chart.yaml,values.yaml,templates/}
│   └── migrations/                      # alembic + neo4j cypher migrations
│       ├── alembic/versions/
│       └── cypher/
│
├── evals/
│   ├── datasets/
│   │   ├── longmemeval_subset/          # long-horizon recall
│   │   ├── contradiction_probe/         # hand-built: 60 pairs at S4.3, grown to 400 by S22.1
│   │   ├── poisoning_redteam/           # injection + poisoning attempts
│   │   └── vertical_{clinical,legal,fintech}/
│   ├── suites/
│   │   ├── rag_quality.py               # faithfulness, answer relevance, context recall
│   │   ├── memory_integrity.py          # write precision, contradiction escape rate
│   │   ├── drift.py                     # Drift@N over 100-turn synthetic agent runs
│   │   ├── security.py                  # ASR (attack success rate) on redteam set
│   │   └── hitl_agreement.py            # Cohen's κ, reviewer-vs-model agreement
│   ├── runners/{run_suite.py,report.py}
│   ├── reports/                          # versioned eval reports, <date>.md + .json (S22.1)
│   └── baselines/                       # mem0 / zep / raw-RAG comparison configs
│
├── bench/
│   ├── locust/{write_path.py,read_path.py}
│   └── profiles/{p95_targets.yaml}
│
├── tests/                                # in scope for `make typecheck` from S1.7
│   ├── unit/                            # per-module, no I/O, >90% on guardmem-core
│   ├── integration/                     # testcontainers: pg, neo4j, redis
│   ├── contract/                        # schemathesis on OpenAPI + MCP tool schemas
│   ├── property/                        # hypothesis: pipeline invariants
│   ├── security/                        # injection corpus regression
│   ├── fixtures/                        # a package, so mypy resolves one module name
│   │   ├── fakes.py                     # FakeLLM / FakeVectorStore / FakeGraphStore (S1.7)
│   │   ├── extraction.py                # shared extraction scaffolding (S2.2)
│   │   ├── noise_corpus.py              # 40 hand-labelled turns, the S2.1 gate
│   │   ├── strategies.py                # hypothesis strategies, one per schema
│   │   └── strategy_primitives.py       # the vocabulary those draw from (S2.2)
│   └── conftest.py                      # REPO_ROOT + all_schema_models()
│
├── scripts/
│   ├── normalise_secrets_baseline.py    # pre-commit: POSIX-ify .secrets.baseline paths
│   ├── seed_demo_tenant.py
│   ├── replay_trace.py                  # deterministic re-run of any audited decision
│   └── threshold_tuner.py               # fit τ/ρ from labelled reviewer decisions
│
└── .github/workflows/
    ├── ci.yml                           # lint → typecheck → unit → integration → coverage gate
    ├── eval-nightly.yml                 # eval suites + regression diff vs main
    ├── security.yml                     # trivy, gitleaks, pip-audit, semgrep
    └── release.yml                      # build, sign (cosign), SBOM, deploy Cloud Run
```

---

## Ownership & Dependency Rules

| Layer | May import | May **not** import |
|---|---|---|
| `guardmem-core` | stdlib, pydantic, httpx, store drivers | fastapi, mcp, next.js, anything HTTP-server |
| `services/*` | `guardmem-core`, its own framework | another service's internals |
| `apps/dashboard` | `@guardmem/sdk` via BFF only | direct DB/store access |
| `evals/*` | `guardmem-core`, `guardmem-sdk-python` | `services/*` internals |

**One-way arrow:** `apps → services → packages → stores`. A cycle fails CI (`import-linter` contract in `pyproject.toml`).

**This tree is a blueprint, not a checklist.** Create each file at the step that needs it; do not
scaffold empty modules ahead of time. An empty module that exists is indistinguishable at a glance
from a finished one, which is exactly the confusion the build order exists to prevent.

## Licensing note
Core engine Apache-2.0. `apps/dashboard` HITL queue + SSO/audit-export modules under BUSL-1.1
converting to Apache-2.0 after 4 years — standard open-core split so self-hosters get the engine
and enterprises pay for the control plane.
