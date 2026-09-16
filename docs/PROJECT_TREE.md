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
├── alembic.ini                          # S3.1; script_location only, URL comes from Settings
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
│   │   ├── 0006-extraction-result-carries-the-sample-sets-and-the-unsourced-count.md
│   │   ├── 0007-provenance-records-its-span-alignment.md
│   │   ├── 0008-entity-resolution-binds-not-matches.md
│   │   └── 0009-pii-class-and-irreversibility-are-ontology-fields.md
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
│   │       │   ├── risk.py              # ADR-0009; ImpactLevel, PiiClass, Irreversibility
│   │       │   │                        #   - what a PREDICATE declares about its danger
│   │       │   ├── object_spec.py       # S3.5; the ObjectSpec union, split out of
│   │       │   │                        #   ontology.py at RULES 2.4's cap
│   │       │   ├── verdict.py           # ImpactLevel, Conflict*, ConfidenceReport,
│   │       │   │                        #   RiskVerdict, Decision, DecisionRecord
│   │       │   ├── policy.py            # ObligationKind, Obligation; Rule + PolicyPack at S12.2
│   │       │   ├── receipt.py           # SourceTier, Provenance, WriteReceipt, AuditEvent
│   │       │   ├── review.py            # ReviewTask, ReviewDecision, Diff
│   │       │   ├── turn.py              # S2.1; Turn, NoiseReason, DroppedTurn, NoiseResult
│   │       │   └── ontology.py          # S3.5; Ontology, PredicateSpec, the ObjectSpec union,
│   │       │                            #   parse_ontology + load_ontology
│   │       ├── prompts/                 # RULES 3: versioned prompts, never inline f-strings
│   │       │   ├── loader.py            # S2.1; render(name, version, variables), frontmatter
│   │       │   ├── classify_noise/v1.md # S2.1; the Layer-1 noise classifier
│   │       │   ├── extract_memories/v1.md   # S2.2; K-sample extraction
│   │       │   ├── adjudicate_conflict/v1.md  # S4.3; one call for the whole incumbent set
│   │       │   └── review_brief/v1.md
│   │       ├── ontology/                # tenant starter packs, loaded by schemas/ontology.py
│   │       │   └── clinical.yaml         # S3.5; 15 predicates, 6 entity types. legal/fintech
│   │       │                             #   arrive at the step that needs them
│   │       ├── pipeline/                # LAYER 1-3
│   │       │   ├── orchestrator.py      # MemoryPipeline.run() — the single entrypoint
│   │       │   ├── l1_extract/
│   │       │   │   ├── extractor.py     # S2.2; ExtractionContext, K-sampling, span link
│   │       │   │   ├── noise_filter.py  # S2.1; rules tier + one batched FAST call
│   │       │   │   ├── noise_rules.py   # S2.1; the deterministic half - pure, no I/O
│   │       │   │   └── span_linker.py   # S2.3; SpanMatch, exact then fuzzy >= 92, snapped
│   │       │   ├── per_candidate.py  # S5.6; Layers 2-3 for ONE candidate, split at
│   │       │   │                    #   RULES 2.4's cap along the seam run() already
│   │       │   │                    #   described: per-proposal vs per-candidate
│   │       │   ├── orchestrator.py   # S5.6; run() - one proposal, every layer. Writes
│   │       │   │                    #   NOTHING: the audit/write transaction is unowned
│   │       │   ├── deps.py           # S5.6; Deps + EntityResolver and EntailLookup.
│   │       │   │                    #   CandidateClassifier was here until ADR-0009
│   │       │   ├── inputs.py         # S5.6; the joins between stages, each pure
│   │       │   ├── l2_validate/
│   │       │   │   ├── schema_gate.py   # S4.1; pass / coerce / quarantine / reject against
│   │       │   │   │                    #   the tenant ontology, carrying S_sch forward
│   │       │   │   ├── incumbents.py    # S4.2; top-10 by cosine within (namespace,
│   │       │   │   │                    #   subject, predicate) plus 1-hop graph neighbours
│   │       │   │   ├── conflict.py      # S4.3; detect() - (b) cardinality and (c) temporal
│   │       │   │   │                    #   overlap answer without a model, (a) NLI last
│   │       │   │   ├── nli.py           # S4.3; NLIJudge protocol + LLMJudge. Separate
│   │       │   │   │                    #   because the cross-encoder swap is scheduled
│   │       │   │   └── dedupe.py        # S4.4; §2.3's resolution table and §2.4's merge.
│   │       │   │                        #   classify() is pure; merge() adds no row
│   │       │   └── l3_score/
│   │       │       ├── entropy.py       # S5.1; §3.1 bidirectional-entailment clusters,
│   │       │       │                    #   H_norm, and the minority-hallucination drop
│   │       │       ├── confidence.py    # S5.2; §3.2's five terms, the weight set and
│   │       │       │                    #   its version. Pure - no I/O, no model call
│   │       │       ├── impact.py        # S5.3; §3.3's linear score, sigmoid, then the
│   │       │       │                    #   impact floor - which is the safety property
│   │       │       ├── impact_features.py  # S5.3; the eight features. The three §3.3
│   │       │       │                    #   named and nothing produced now have sources:
│   │       │       │                    #   scope off the namespace, the other two
│   │       │       │                    #   declared per predicate (ADR-0009)
│   │       │       ├── decision.py      # S5.4; §3.4's twelve cells + the escalation
│   │       │       │                    #   clamp. Pure, total, deterministic (I4)
│   │       │       └── overrides.py     # S5.4; the seven hard overrides. `tighten` is
│   │       │                            #   what makes "never relax" mechanical
│   │       ├── guardrails/
│   │       │   ├── injection.py         # prompt-injection & instruction-smuggling detect
│   │       │   ├── pii.py               # Presidio recognizers + tokenization vault
│   │       │   ├── poisoning.py         # provenance trust tiers, corroboration quorum
│   │       │   ├── toxicity.py
│   │       │   └── policy_engine.py     # OPA/Rego-compatible rule evaluation
│   │       ├── memory/
│   │       │   ├── entities.py          # ADR-0008; NamespaceEntityResolver. Binds a
│   │       │   │                        #   subject to an entity and NEVER matches names
│   │       │   ├── router.py            # S3.3; StoreRouter — write entry point, tenant + visibility guards
│   │       │   ├── outbox.py            # S3.3; the `outbox` table's shape, both directions
│   │       │   ├── relay.py             # S3.3; drains the outbox: graph side, then visible=true
│   │       │   ├── vector/
│   │       │   │   ├── base.py          # VectorStore + Embedder Protocols (S1.7, S3.2);
│   │       │   │   │                    #   embed_text + Claim moved here at S4.2
│   │       │   │   ├── pool.py          # S3.2; process-wide asyncpg pool, vector codec registered;
│   │       │   │   │                    #   S3.3 added tenant_transaction, shared with the relay
│   │       │   │   ├── rowmap.py        # S3.2; assertion/provenance row shape, both directions
│   │       │   │   ├── hash_embedder.py # S3.6 audit; the one deterministic Embedder,
│   │       │   │   │                    #   shared by the seed and the suite, so they cannot drift
│   │       │   │   ├── pgvector_store.py
│   │       │   │   └── qdrant_store.py
│   │       │   ├── graph/
│   │       │   │   ├── base.py          # GraphStore Protocol (S1.7)
│   │       │   │   ├── neo4j_store.py
│   │       │   │   └── networkx_store.py   # S3.4; dev / single-tenant fallback -
│   │       │   │                        #   MultiDiGraph keyed by assertion_id, and the
│   │       │   │                        #   single-tenant half is enforced, not assumed
│   │       │   ├── retrieval.py         # hybrid BM25 + dense + graph-expand
│   │       │   └── compaction/
│   │       │       ├── decay.py         # recency × usage × salience scoring
│   │       │       ├── gc.py            # tombstoning, TTL tiers, thread-rot sweep
│   │       │       └── summarizer.py    # rollup of cold assertions → episodic digest
│   │       ├── llm/                     # S1.7 protocol; providers + router at S9.x
│   │       │   ├── base.py              # Tier, LLMResponse, LLMClient Protocol
│   │       │   ├── providers/           # anthropic.py openai.py bedrock.py vertex.py ollama.py
│   │       │   ├── selection.py         # S6.2; build_llm - which adapter a composition
│   │       │   │                        #   root builds, from settings. Refuses, never falls back
│   │       │   ├── entailment.py        # S5.1; EntailFn's only producer. Here and not
│   │       │   │                        #   in l3_score/ because that package is pure
│   │       │   ├── router.py            # tier routing: FAST | BALANCED | FRONTIER
│   │       │   ├── fallback.py          # circuit breaker + hedged requests
│   │       │   ├── cache.py             # prompt-cache headers, semantic response cache
│   │       │   └── budget.py            # per-tenant token/$ ledger + hard caps
│   │       ├── observability/
│   │       │   ├── tracing.py           # OTel spans, trace_id propagation
│   │       │   ├── exporters/           # langfuse.py phoenix.py otlp.py
│   │       │   ├── metrics.py           # prometheus counters/histograms
│   │       │   ├── audit.py             # S5.5; I5's digest, canonical JSON and
│   │       │   │                        #   verify_chain. PURE - no connection here
│   │       │   ├── audit_store.py       # S5.5; the audit_event table. `append` takes a
│   │       │   │                        #   CONNECTION, so the row commits with the state
│   │       │   │                        #   change (RULES non-negotiable #4)
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
│   │       │        outbox_relay.py}   # S3.3 put the relay's logic in
│   │       │                           #   guardmem_core.memory.relay; this is the
│   │       │                           #   arq binding that calls run_once()
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
│   │   ├── initdb/                      # Postgres first-boot SQL: extensions (01),
│   │   │                                #   the guardmem_app role (02, S3.1)
│   │   ├── docker-compose.test.yml
│   │   └── docker-compose.observability.yml   # otel-collector, prometheus, grafana, tempo
│   │                                    # + langfuse, which needs clickhouse/minio from v3 on
│   ├── terraform/
│   │   ├── modules/{cloud_run,cloud_sql,memorystore,secret_manager,artifact_registry}/
│   │   └── envs/{dev,staging,prod}/
│   ├── k8s/                             # optional helm chart for self-hosted enterprise
│   │   └── helm/guardmem/{Chart.yaml,values.yaml,templates/}
│   └── migrations/                      # alembic + neo4j cypher migrations
│       ├── alembic/env.py               # S3.1; URL from Settings, no autogenerate
│       ├── alembic/versions/
│       │   └── 0001_initial.py          # S3.1; bitemporal assertions, provenance, RLS
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
│   │                                    #   RULES 2.4 size caps enforced by
│   │                                    #   unit/test_source_limits.py (S2.2)
│   ├── unit/                            # per-module, no I/O, >90% on guardmem-core
│   │                                    #   test_networkx_graph_store.py (S3.4) runs every
│   │                                    #   shared case against BOTH GraphStore impls
│   ├── integration/                     # testcontainers Postgres, from S3.2
│   │   ├── test_migration_invariants.py #   RULES 1.1/#2/#4 and RLS, against the schema
│   │   ├── test_pgvector_store.py       #   S3.2 DONE WHEN: write, search, supersede, as_of
│   │   ├── test_outbox_enqueue.py       #   S3.3; the assertion and its event, one transaction
│   │   ├── test_outbox_relay.py         #   S3.3 DONE WHEN: killed mid-flight, visible once
│   │   ├── test_seed_demo_tenant.py     #   S3.6 DONE WHEN: `make seed` twice, same row count
│   │   ├── test_incumbent_retrieval.py  #   S4.2 DONE WHEN: the seeded incumbent comes back
│   │   └── test_audit_chain.py   #   S5.5 DONE WHEN: a real UPDATE on a real row,
│   │                         #   and verify_chain names that exact seq
│   ├── contract/                        # schemathesis on OpenAPI + MCP tool schemas
│   ├── property/                        # hypothesis: pipeline invariants
│   │                                    #   test_i4_decision_totality.py (S5.4): the bands
│   │                                    #   tile the unit square, over generated thresholds
│   │                                    #   test_i2_single_live_value.py (S4.4): I2 over
│   │                                    #   500 generated write sequences, plus the three
│   │                                    #   properties that stop it passing vacuously
│   ├── security/                        # injection corpus regression
│   ├── fixtures/                        # a package, so mypy resolves one module name
│   │   ├── fakes.py                     # FakeLLM / FakeVectorStore / FakeGraphStore (S1.7).
│   │   │                                #   The embedder moved to the package at the S3.6 audit
│   │   ├── postgres.py                  # S3.2; the migrated testcontainer, as a pytest plugin
│   │   ├── pgvector.py                  # S3.2; tenant, pool and store fixtures over it
│   │   ├── outbox.py                    # S3.3; relay fixtures and the owner-side row probes
│   │   ├── graph_faults.py              # S3.3; GraphStore doubles, one failure mode each
│   │   ├── extraction.py                # shared extraction scaffolding (S2.2)
│   │   ├── noise_corpus.py              # 40 hand-labelled turns, the S2.1 gate
│   │   ├── contradiction_corpus.py      # S4.3; PAIRS - the 60-pair probe, assembled from
│   │   │                                #   the three modules below. Split by which mistake
│   │   │                                #   a row guards against, not by size
│   │   ├── conflict_pair.py             # S4.3; ConflictPair + the pair() shorthand
│   │   ├── corpus_settled.py            # S4.3; the 20 rows (b)/(c) answer with no judge.
│   │   │                                #   S4.4 relabelled the restatements DUPLICATE
│   │   ├── corpus_contradictions.py     # S4.3; rows a judge has to catch
│   │   ├── corpus_coexist.py            # S4.3; rows that must NOT be flagged, plus the
│   │   │                                #   0.3-0.65 band that must escalate instead
│   │   ├── conflict.py                  # S4.3; shared detect() scaffolding, split from
│   │   │                                #   test_conflict_detection.py at the 400-line cap
│   │   ├── assertions.py                # cleanup-to-S4.1; the ONE StoredAssertion builder.
│   │   │                                #   Five modules had a near-identical copy of a
│   │   │                                #   fourteen-field model
│   │   ├── strategies.py                # hypothesis strategies, one per schema
│   │   ├── strategy_ontology.py         # S3.5; the ontology models, whose validator makes
│   │   │                                #   entity types have to be drawn before predicates
│   │   ├── strategy_l2.py               # S4.1/S4.2/S4.4; the Layer 2 result models
│   │   ├── strategy_observability.py    # S5.5; ChainVerification, drawn coherently
│   │   ├── strategy_l3.py               # S5.1; MeaningClusters, drawn coherently -
│   │   │                                #   `minority` is derived, not independent
│   │   ├── strategy_verdict.py          # S5.1/S5.4; verdict.py's models, split
│   │   │                                #   out when strategies.py crossed the cap
│   │   ├── seed.py                      # S4.2; the seeded demo tenant, as a plugin
│   │   └── strategy_primitives.py       # the vocabulary those draw from (S2.2)
│   └── conftest.py                      # REPO_ROOT, all_schema_models(), pytest_plugins.
│                                        #   The ONLY conftest: a second one is a duplicate
│                                        #   module name and mypy refuses the pair (S3.2)
│
├── scripts/                             # in scope for `make typecheck` from S3.6
│   ├── normalise_secrets_baseline.py    # pre-commit: POSIX-ify .secrets.baseline paths
│   ├── seed_demo_tenant.py              # S3.6; `make seed` - the END OF DAY 3 CHECK, executable
│   ├── demo_tenant_data.py              # S3.6; the 40-turn transcript and the facts it sources
│   ├── checkpoint_b.py                  # CHECKPOINT B; verify/template/score/agreement.
│   │                                    #   The gate itself is BLOCKED on S9.1 - no LLMClient
│   ├── replay_trace.py                  # S5.6; re-runs decide() from the audit chain and
│   │                                    #   diffs. NOT the model calls - see its docstring
│   ├── __init__.py                      # S5.6; a package, so mypy sees one module name
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
| `scripts/*` | `guardmem-core`, stdlib | `services/*` and `tests/*` internals |

**One-way arrow:** `apps → services → packages → stores`. A cycle fails CI (`import-linter` contract in `pyproject.toml`).

**This tree is a blueprint, not a checklist.** Create each file at the step that needs it; do not
scaffold empty modules ahead of time. An empty module that exists is indistinguishable at a glance
from a finished one, which is exactly the confusion the build order exists to prevent.

## Licensing note
Core engine Apache-2.0. `apps/dashboard` HITL queue + SSO/audit-export modules under BUSL-1.1
converting to Apache-2.0 after 4 years — standard open-core split so self-hosters get the engine
and enterprises pay for the control plane.
