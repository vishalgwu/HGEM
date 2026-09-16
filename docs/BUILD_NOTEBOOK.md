**As of 2026-09-15 every dependency is implemented**, and `run()` has been
driven end to end against a real Postgres and a real local model: one candidate
in, one `HITL_REVIEW` out at `C = 0.837`, `R = 0.924`, with a real entity row
written through the assertion's foreign key. `EntailFn` is `llm/entailment.py`
(S5.1 correction 4), `EntityResolver` is `memory/entities.py` (ADR-0008), and
`CandidateClassifier` was deleted rather than implemented (ADR-0009) — its two
remaining features are `PredicateSpec` fields and the third always came off the
namespace.

What is left is not architectural:

- **The harness still has no `generate` subcommand.**
- **The corpus needs more transcript**: forty turns for one patient supporting
  28 facts is not 200 candidates.

**Extraction on Ollama was blocked and is not any more.**
`ExtractedFact.verbatim` carries `maxLength: 2000`; Ollama compiles `format`
into a grammar where a bounded length is a repetition, and repetitions of 2000
or more are refused - the whole schema, not the field. Bisected against Ollama
0.34.0 with `llama3.1:8b`: **1999 compiles, 2000 does not**. The adapter now
strips those keywords from the grammar it sends and the caller still validates
the reply against the full schema, so the cap moves from prevention to
detection. The whole pipeline has since been driven end to end on a local model.

### Sign-off

```
CHECKPOINT B: PASS / MARGINAL / FAIL
Date:
AUROC:              (n=200, human-labelled)
AUROC entropy-only:
Coverage:           %
Decision:
Notes:
```

**Recorded 2026-09-14: BLOCKED. Partly unblocked 2026-09-15 by S9.1 — still not run.**

S9.1 shipped the three provider adapters, so the blocker written above is gone:
`LLMClient` has implementations and a real model can be called. **That was one
of four missing dependencies, not all of them**, and the entry recorded on
2026-09-15 said "nothing is in its way now but an API key", which was wrong.
`pipeline/deps.py` and `mcp_server/tools/pipeline.py::MISSING_DEPENDENCIES` both
named the other three the whole time; the correction is reading them.

Still missing, and `run()` cannot be called without any of them:

| Missing | What it feeds | Share of `C` |
|---|---|---|
| `EntityResolver` | incumbents → conflict → `S_con` | 0.15 |
| `CandidateClassifier` | §3.3's `pii_class`, `irreversibility` → `R` | none, but `run()` needs it |

`EntailFn` was the third and the largest at 0.60, and it has a producer as of
2026-09-15 - `llm/entailment.py`, see S5.1's correction 4. It is **not** wired:
the callable is sync and the producer is async, so `_score_and_decide` has to
assemble a candidate's pairs and await one lookup before scoring it. That is the
remaining work on this row, and it is wiring rather than a decision - unlike the
two above, each of which needs an ADR first.

Two more gaps that are not dependencies. The harness has **no `generate`
subcommand** — `verify`, `template`, `score` and `agreement` ship, and the loop
that fills a corpus does not. And step 1 says "200 candidates from the seed
transcript": that transcript is forty turns for one patient supporting 28
facts, so the corpus needs widening before 200 is reachable at all.

**This gate needs no API key.** `GM_ANTHROPIC_API_KEY` has been blank since
S0.2 and a local Ollama model runs the whole pipeline for nothing - verified,
after the grammar fix above, which is the correction to the two earlier times
this was asserted without being tried. Correction 1 below applies to whichever
provider is used, and the sign-off still has to name it: an AUROC measured on a
7B local model and one measured on Claude are two different numbers.

**Read correction 1 on S9.1 before running it.** Anthropic has no temperature
parameter at all, so §1.2's 0-then-0.7 spread is not what is drawn there; the
entropy term measures the model's own variance rather than a tuned one. That
does not invalidate the gate, but it does mean an AUROC measured against
Anthropic and one measured against a local Ollama model are two different
numbers, and the sign-off has to say which provider produced it.

```
CHECKPOINT B: BLOCKED (discrimination test not runnable until S9.1)
Date:               2026-09-14
Manual checks:      8/8 pass (B1-B8, run by `checkpoint_b verify`)
Automated checks:   lint, typecheck, imports green; 1236 unit and property
                    tests plus 87 integration; I1/I2/I3/I4/I5 green; replay
                    prints "identical"
AUROC:              not measured - no LLMClient implementation exists
AUROC entropy-only: not measured, same reason
Coverage:           94% (unit + property), 100% on every Layer 3 module
Decision:           proceed to Day 6 with the gate explicitly OPEN, and run it
                    the day S9.1 lands. Everything after this point assumes the
                    scoring discriminates and that remains UNVERIFIED.
Notes:              I3 (supersession acyclic) was also not property-tested -
                    the automated-check list names I1-I4 and only three files
                    existed. Closed on 2026-09-14 by
                    tests/property/test_i3_supersession_acyclic.py, which also
                    records what I3 does NOT rest on: `supersede` compares
                    nothing about the two ids it is handed, so a caller can
                    drive the store into a two-cycle. The invariant holds
                    because the applier mints its successor, and that is a
                    property of the write path rather than of the store.
```

---

## DAY 6 — MCP server (first user-facing surface)

### S6.1 -- Server skeleton, stdio transport

WHERE: `services/mcp_server/src/mcp_server/server.py`, and `lifespan.py` beside it
TIME: 60 min

```bash
uv add "mcp[cli]"
```
Wire lifespan: settings, Postgres pool, graph store, embedder, ontology. Advertise capabilities:
tools, resources and prompts, each by registering its list handler and returning an empty list.

DONE WHEN: `npx @modelcontextprotocol/inspector uv run guardmem-mcp` connects and lists zero tools
without error. Mechanised as `tests/integration/test_mcp_stdio.py`, which asks the same question
over the SDK's in-memory transport so it runs in CI on every commit.

COMMIT: `feat(s6.1): mcp server skeleton`

**Five corrections to this step, found by building it.** The version above already includes them.

1. **The lifespan cannot wire an `LLMClient` or `pipeline.Deps`, and the reason is the one that
   has CHECKPOINT B recorded as BLOCKED.** There is no `LLMClient` implementation in this
   repository - `llm/base.py` declares the Protocol, `FakeLLM` implements it for the suite, and
   **S9.1** builds the provider adapters. `Deps` is worse off: `EntityResolver` and
   `CandidateClassifier` are Protocols nothing implements either, so it cannot be constructed at
   all. Binding `FakeLLM` to make this step's sentence come true would put a scripted model behind
   the project's first user-facing surface. **This means S6.2 is blocked on S9.1 plus an entity-
   resolution decision, not on writing tool handlers** - a `memory.propose` tool is a call to
   `run()`, and `run()` cannot be called. Worth knowing before Day 6 is planned as four steps.
2. **`resources.subscribe` must not be advertised yet.** The other three capabilities are claims
   about what can be *listed* and are true as soon as a handler exists. `subscribe` is a claim
   about what the server will *send*: a subscribed client is promised
   `notifications/resources/updated`, and nothing here can produce one - there is no resource until
   S6.4 and no write path to change the state behind it. A client would wait forever and could not
   distinguish that from "nothing has changed". It belongs at **S6.4**, with the resource and the
   change feed together. Pinned by `test_resources_subscribe_is_not_advertised`.
3. **A capability is advertised by registering its handler, not by asking for it.** The SDK derives
   `ServerCapabilities` from which methods are served, so "advertise tools and list zero of them"
   is one decision rather than two: no `tools/list` handler means no tool capability, and a client
   then reads "this server does not do tools" rather than "this server has none". The three
   handlers return empty lists for exactly this reason.
4. **The distribution is `guardmem-mcp` and the import package is `mcp_server`,** which needs
   `[tool.hatch.build.targets.wheel] packages = ["src/mcp_server"]` or hatchling's name-based
   detection fails the build outright. `guardmem-core` needs no such block because its two names
   match. The root `pyproject.toml` also has to *depend* on `guardmem-mcp`, for the same reason
   correction 1 on S1.1 gives about `guardmem-core`: `[tool.uv.sources]` says where to resolve a
   workspace member, and nothing installs one until something depends on it - so CI's
   `uv sync --locked` would lock the service and not install it.
5. **Validate configuration BEFORE opening the transport, and this is the correction with the
   most operational value in the step.** The obvious shape puts the `Settings` read in the
   lifespan, where it looks like startup. But the lifespan runs inside `Server.run`, which runs
   inside `stdio_server()` — so a bad environment escapes through anyio as a `BaseExceptionGroup`
   and reaches the operator as **78 lines** of asyncio and contextlib frames with `9 validation
   errors for Settings` buried in the middle, exit code 1, and the pipe already accepted. Measured,
   by running the console script from a directory with no `.env`.

   That directory is the point. `Settings` resolves `.env` against the **working directory**, and
   an MCP client chooses it — Claude Desktop does not use the repository. The MCP SDK compounds it:
   a spawned server inherits only `DEFAULT_INHERITED_ENV_VARS`, so `GM_*` exported in a shell does
   **not** reach it and the values must come from the client config's own `env` block. Neither fact
   is reachable from a pydantic traceback, and a GUI client renders all of it identically as
   "server disconnected".

   `preflight()` in `lifespan.py` now runs first and raises `ConfigurationError` naming the missing
   fields, the directory it looked in, whether a `.env` was there, and the inheritance rule — one
   line, exit code **2** (distinct from a crash, so a supervisor does not restart a misconfigured
   server forever). **This is a prerequisite for S6.3**, which is where a user meets it.

---

### S6.2 -- The four core tools

TIME: 120 min

Implement in this order: `memory.search`, `memory.propose`, `memory.commit`, `memory.get_entity`.
JSON schemas come from MCP_INTEGRATION.md sections 2.1-2.4 — copy them exactly, including the
descriptions. The descriptions are prompt engineering, not documentation.

DONE WHEN: from the Inspector you can propose a fact, get a decision back, and then find it via
`memory.search` with its provenance.

COMMIT: `feat(s6.2): core mcp tools`

**Five corrections to this step, found by building it.**

1. **THE DONE WHEN IS HALF-SATISFIABLE, AND THIS IS THE STEP THAT PROVES IT.** "Find it via
   `memory.search` with its provenance" works and is mechanised against the seeded tenant, over the
   protocol, in CI. "Propose a fact, get a decision back" **cannot happen**: `memory.propose` is a
   call into `pipeline.run`, which needs an `LLMClient` (**S9.1**), an `EntityResolver` (specified
   in no document; needs an ADR) and a `CandidateClassifier` (deployment policy). `memory.commit`
   needs the same three plus the applier, which is its own ADR. So **two of the four tools work and
   two decline**, and the declining two name every missing dependency rather than returning an
   invented decision — a `memory.propose` that answered `auto_write` with a plausible confidence
   would be the most harmful thing this repository could ship, because the product's whole claim is
   that a fact was governed before it was believed. **Do S9.1 before S6.3.**
2. **`memory.search` could not answer §2.1 at all until the store grew a read path.** §2.1 publishes
   an `excluded` array so an agent can tell "we have no record" from "we retired that record" — and
   invariant I6 means `search` must *never* return a retired assertion, `search(as_of=...)` selects
   rows whose validity contains an instant (so asking about now returns exactly the live set), and
   `valid_to IS NOT NULL` is not a filter it can express. `excluded` would have been permanently
   empty. S6.2 therefore adds **`VectorStore.retired`**, which answers the other question in its own
   field and is also what §2.5's `memory.timeline` will need.
3. **Scope `excluded` by the predicates the caller asked about.** Found by driving the seeded tenant
   by hand: an unscoped version told a search for `allergy` that two facts had been retired, and both
   were a `home_address` and a `preferred_pharmacy`. A footnote that is usually wrong is worse than
   no footnote, and §2.1's whole point is that this field be *trustworthy*.
4. **The server needs `GM_MCP_TENANT_ID` and `GM_MCP_DEFAULT_NAMESPACE`, because authentication does
   not exist.** §1's `GUARDMEM_API_KEY` is what a tenant is resolved from in the finished system, by
   the gateway (S8.1) and its RLS middleware (S8.2). Neither is built and this server talks straight
   to Postgres, so the tenant is configuration and the tools **refuse** without it — a default would
   be a cross-tenant read that succeeds and returns the wrong rows.
5. **Four fields §2.1-§2.2 publish have no producer, and each is named rather than faked.**
   `reviewed_by`/`reviewed_at` need the review queue (**S18.3**) and are omitted rather than emitted
   as `null`, which would claim the fact was *not* reviewed. `token_budget` is trimmed against a
   deliberately pessimistic character estimate, because the real packer is **S14.3** and the obvious
   interim — `tiktoken` — is OpenAI's tokenizer *and* downloads its BPE ranks on first use, which is
   the wrong failure for a stdio server inside a desktop app. `mode: async` is §2.2's default and is
   refused, since "return now, decide later" needs the worker from **S8.4** and with no queue the
   second half never happens. `review_task_id`/`eta_minutes` need **S18.1**.

Also worth knowing: **`memory.get_entity`'s `neighbors` is empty in a fresh process**, because
`NetworkXGraphStore` holds the graph in memory and a seeded database has no undispatched outbox
events to replay. The response carries `graph_backed: false` so a caller cannot read a limitation as
a finding; **S7.1** is the fix.

---

### S6.3 -- Connect Claude Desktop

TIME: 30 min

Add the config block from MCP_INTEGRATION.md section 1, restart Claude Desktop, and have a
conversation that writes a fact.

DONE WHEN: **this is the day-7 gate, hit early.** You tell Claude Desktop "remember that the
patient's preferred pharmacy is CVS #4021", and you can see the row in Postgres with a source span,
a confidence score, and an audit event.

COMMIT: `docs(s6.3): claude desktop setup instructions`

---

### S6.4 -- Resources and prompts

TIME: 60 min

`guardmem://memory/{namespace}`, `guardmem://audit/{trace_id}`, `guardmem://ontology/{namespace}`,
plus the four prompts from MCP_INTEGRATION.md section 4.

DONE WHEN: attaching the namespace resource in Claude Desktop shows the current believed state.

COMMIT: `feat(s6.4): mcp resources and prompts`

---

## DAY 7 — Neo4j, hardening, coverage

### S7.1 -- Neo4j store
TIME: 75 min. Implement `GraphStore` against Neo4j using the model in ARCHITECTURE.md section 5.
Config flag `GM_GRAPH_BACKEND=neo4j|networkx`.
DONE WHEN: the full integration suite passes against both backends unchanged.

### S7.2 -- Coverage push and gap fixing
TIME: 90 min. Get `guardmem-core` to >= 85%. That is the **Week-1 interim floor**; RULES 5 sets the
release gate at 90%, and the gap closes before Phase 1 is signed off. Look specifically at error
branches - they are what you skipped.

### S7.3 -- MCP contract tests
TIME: 45 min. Validate every tool's inputSchema/outputSchema with jsonschema; assert no drift.

### S7.4 -- Week 1 retro
TIME: 30 min. Write the DAILY_LOG entry. Answer honestly: does the scoring separate good from bad?

WEEK 1 EXIT GATE: the Phase 1 checklist in PHASES_AND_ROADMAP.md section 1, which owns it. In
short: Claude Desktop round-trip with provenance, supersession with tombstone and audit event,
deterministic replay, no unsourced write possible, Checkpoint B signed off, coverage at the gate,
suite green. Do not start week 2 with a box unticked — every week after this one assumes all of it.

Note on coverage: >= 85% is the Week-1 interim floor, and RULES 5 sets the release gate at 90%.

---

# PART 3 — WEEK 2: GATEWAY, ROUTING, GUARDRAILS (Days 8-14)

Goal: the engine becomes a service that is fast, safe, cheap, and observable.

---

## DAY 8 — Gateway and the async path

### S8.1 -- FastAPI skeleton
WHERE: `services/gateway/src/gateway/main.py`
TIME: 60 min
```bash
uv add fastapi uvicorn[standard] asyncpg redis arq
```
Lifespan creates: Postgres pool, Redis pool, one `httpx.AsyncClient` per provider, OTel tracer.
Routers: `health`, `memory`, `review`, `policy`, `audit`, `telemetry`.
Routers contain no logic — validate, call one core function, shape response (RULES 2.4).

DONE WHEN: `uvicorn gateway.main:app` serves `/healthz`, `/docs` shows the OpenAPI schema, and the
schemathesis contract suite runs green against the published spec (RULES 5, `contract` suite).
COMMIT: `feat(s8.1): fastapi gateway skeleton`

### S8.2 -- Auth, tenancy, RLS context
TIME: 75 min
Middleware order matters: `request_id -> auth -> tenancy -> ratelimit -> body_hash`.
Tenancy middleware sets the Postgres session var used by RLS:
```python
await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
```
DONE WHEN: `tests/security/test_tenant_isolation.py` — a token for tenant A cannot read tenant B's
assertions through REST, MCP, or the SDK. All three must fail.
COMMIT: `feat(s8.2): auth, tenancy, row-level isolation`

### S8.3 -- Rate limiting and idempotency
TIME: 45 min. Token bucket in Redis keyed by `tenant:api_key`. Idempotency keys cached 24h; a
repeat returns the stored result without re-running the pipeline.
DONE WHEN: replaying the same request twice produces one assertion and two identical responses.
COMMIT: `feat(s8.3): rate limiting and idempotent writes`

### S8.4 -- Async path: Redis stream + arq worker
WHERE: `services/worker`
TIME: 75 min
`mode=async` -> validate, hash payload to blob store, enqueue, return 202 + `trace_id` in under
80 ms. `mode=strict` runs the pipeline inline with K=1 FAST.
DONE WHEN: locust run at 50 rps shows p95 < 80 ms on the async propose endpoint.
COMMIT: `feat(s8.4): async evaluation path`

---

## DAY 9 — LLM router, fallback, budget

### S9.1 -- Provider adapters
TIME: 60 min. Anthropic, OpenAI, Ollama, all behind `LLMClient`. Pin model ids from settings; never
use floating aliases (RULES 3).
DONE WHEN: the same prompt runs against all three in an integration test.

**Five corrections to this step, found by building it.**

1. **`anthropic` 1.4.0's `messages.create` HAS NO `temperature`, `top_p` OR `top_k`.** Sampling
   controls were removed on the current Claude models and the SDK dropped the parameters with them
   — verified by introspecting the installed SDK, not recalled. `LLMResponse.temperature` is
   therefore `float | None`, the same shape and the same reasoning as `seed`: recording the value a
   *caller asked for* on a request that never carried one would put a number in the audit record
   that no provider ever saw.

   **This changes what CHECKPOINT B is measuring.** `MEMORY_ENGINE.md` §1.2 draws the canonical
   sample at temperature 0 and the other K-1 at 0.7, and §3.1 takes semantic entropy over that
   spread. On Anthropic neither number can be sent. The measurement survives — K independent
   requests to a non-deterministic model do vary — but the variance is *the model's own, not a
   spread this system tuned*, and `H_norm` on Anthropic and on Ollama are not the same instrument.
   An AUROC that mixes them is comparing two.
2. **A FIXED SEED MAKES EVERY SAMPLE IN A DRAW IDENTICAL, AND NOTHING RAISES.** The first Ollama
   adapter sent one seed on every request in a K-sample draw. Ollama honours a seed exactly:
   measured, at temperature 0.7 with K=3, the three "independent" samples came back
   **character-identical**. That is one meaning cluster, so `H_norm` is 0, so §3.2's
   `w_H(1 - H_norm)` term scores maximum confidence on every candidate forever — which is
   word-for-word the failure this notebook's own CHECKPOINT B list calls "a scorer that produces
   plausible numbers with no discriminative power ... invisible to unit tests". It was invisible to
   unit tests. **Only a real model call found it.** Sample `i` is now seeded `BASE_SEED + i`, which
   keeps the draw reproducible *and* the samples independent.
3. **`guardmem-core` had to declare `anthropic` and `openai`, and would have failed CI without it.**
   Both were pinned in `requirements/llm.txt` — the human inventory — and neither was a dependency
   of the *package*, so neither was in `uv.lock`, so CI's `uv sync --locked --dev` would not have
   installed them and every import of `llm/providers/` would have failed there while passing
   locally. Exactly the `types-pyyaml` failure from S1.7, in a new place.
   `test_no_dependency_is_documented_as_deferred_once_it_is_used` caught the related half (`httpx`
   left in the "DECLARED AND NOT YET IMPORTED" block); the missing declarations it could not see.
4. **The DONE WHEN cannot run live in CI, and a skip would have been the wrong repair.** Two of the
   three providers bill per token and need a secret. `RULES.md` §5 says LLM calls are "mocked with
   recorded fixtures in unit, live only in the nightly eval job" *and* that the integration suite is
   green **with no skips**. Both hold by mocking at the **transport** — the vendors' own SDKs still
   parse, validate and type every response, so what is replaced is the socket — plus a `live` marker
   in `pyproject.toml` that **deselects** rather than skips. `uv run pytest -m live` calls the real
   providers; S27.1's nightly job is what should select it.
5. **OpenAI returns quota exhaustion and plain rate limiting on the SAME 429**, and `exc.body` is
   the *inner* error object (`{"message", "code"}`), not the envelope. A breaker reading the status
   alone retries an empty account until its attempt cap, every time; the first version of the code
   that was supposed to prevent that walked `body["error"]["code"]` and so always returned `None`.
   `exc.code` is the SDK's own parsed attribute and is what it reads now.

Also settled here: a 400/401/403/404 from either vendor SDK **propagates unwrapped** rather than
becoming `ProviderUnavailable`. Those are configuration errors, and a wrong API key dressed as
"retryable" is one S9.3's circuit breaker would retry forever.
COMMIT: `feat(s9.1): provider adapters`

### S9.2 -- Tier routing
TIME: 45 min. FAST for noise + low-risk extraction, BALANCED for NLI/adjudication, FRONTIER only on
ESCALATE. Selection is a pure function of `(risk_hint, namespace_policy, stage)`.
DONE WHEN: table-driven test covers every combination.
COMMIT: `feat(s9.2): model tier routing`

### S9.3 -- Circuit breakers and cross-provider fallback
TIME: 75 min. Per-provider breaker (closed/open/half-open), jittered retry on `retryable=True`
only, hedged request above p95.
DONE WHEN: chaos test kills the primary provider mid-load. Result: zero unscored writes, everything
degrades to HITL. Not one silent auto-write.
COMMIT: `feat(s9.3): circuit breakers and provider fallback`

### S9.4 -- Prompt caching and semantic response cache
TIME: 45 min. Cache-control headers on the stable prompt prefix; Redis-backed semantic cache keyed
by `(prompt_version, content_hash)`.
DONE WHEN: cache hit rate on a repeated seed run exceeds 40%; `cache_hit` shows up in traces.
COMMIT: `perf(s9.4): prompt and response caching`

---

## DAY 10 — Budget and cost accounting

### S10.1 -- Token/cost ledger
WHERE: `llm/budget.py`, table `usage_ledger`
TIME: 75 min. Record tokens in/out, tier, cost estimate, cache hit, per trace and per tenant.
DONE WHEN: every trace reports a cost; daily totals reconcile with provider dashboards within 5%.
COMMIT: `feat(s10.1): per-tenant token and cost ledger`

### S10.2 -- Soft alerts and hard caps
TIME: 45 min. At the hard cap, AUTO_WRITE is disabled and everything routes to HITL. Never an
unlogged write.
DONE WHEN: test sets a $0 cap and confirms every candidate becomes HITL_REVIEW with reason code
`BUDGET_CAP`.
COMMIT: `feat(s10.2): budget caps degrade to hitl`

---

## DAY 11 — Security armor part 1

### S11.1 -- Prompt injection detection
WHERE: `guardrails/injection.py`
TIME: 90 min
Three layers: heuristic patterns (instruction verbs near delimiters, role-switch phrases,
base64/homoglyph obfuscation), a FAST-tier classifier, and the canary check from S2.2.
Runs on RAW text before any model sees it.
DONE WHEN: redteam corpus v1 (build 120 attacks today — take them from public injection datasets
plus 30 you write yourself) produces 0% landing in the primary namespace. Quarantine is fine.
COMMIT: `sec(s11.1): prompt injection detection`

### S11.2 -- Quarantine namespace
TIME: 40 min. Injected proposals land in `quarantine:<tenant>` — retrievable by admins, flagged,
never promoted without human review. Alert fires.
DONE WHEN: an injection attempt shows up in the audit log with kind=QUARANTINE and nothing in
primary.
COMMIT: `sec(s11.2): quarantine namespace`

### S11.3 -- PII detection and tokenization vault
TIME: 90 min
```bash
uv add presidio-analyzer presidio-anonymizer
```
Detect, then tokenize (`<PII:PERSON:tok_9a1f>`), store the mapping in the separately-encrypted
vault schema with its own DB role. Detokenize only at review-UI render time, for authorized roles.
DONE WHEN: invariant I7 test — grep every log line, span attribute, and outbound prompt in a full
seed run; zero raw PII. Add custom recognizers for MRN and case numbers.
COMMIT: `sec(s11.3): pii detection and reversible tokenization`

---

## DAY 12 — Security armor part 2 and policy

### S12.1 -- Source trust tiers and poisoning defense
TIME: 75 min. Enforce: `RETRIEVED_WEB` content can never auto-write a HIGH/CRITICAL predicate,
whatever the confidence. Per-source write rate limits. Corroboration quorum of 2 independent
sources for `requires_corroboration` predicates.
DONE WHEN: poisoning redteam set — an attacker-controlled retrieved document cannot change a
critical clinical fact without a human.
COMMIT: `sec(s12.1): source trust tiers and poisoning defense`

### S12.2 -- Policy engine
WHERE: `guardrails/policy_engine.py`
TIME: 90 min. Rego-compatible packs, versioned, hot-reloadable, `dry_run` mode. Rules return
`Obligation` objects that compose with the decision matrix — they never short-circuit it.
DONE WHEN: a policy change writes an audited `POLICY_CHANGE` event; dry-run reports projected
impact without enforcing.
COMMIT: `feat(s12.2): policy engine with obligations`

---

## DAY 13 — Observability

### S13.1 -- OpenTelemetry instrumentation
TIME: 75 min
```bash
uv add opentelemetry-sdk opentelemetry-exporter-otlp opentelemetry-instrumentation-fastapi
```
Span naming from ARCHITECTURE 2.5: `guardmem.l1.extract`, `guardmem.l3.score`,
`guardmem.decision`, `guardmem.store.write`. Attributes are **allow-listed** (never PII).
DONE WHEN: one trace_id appears in Langfuse, Phoenix, and the app logs, all consistent.
COMMIT: `feat(s13.1): otel tracing across the pipeline`

### S13.2 -- Prometheus metrics
TIME: 45 min. `guardmem_decisions_total{decision,namespace,tier}`,
`guardmem_confidence_bucket`, `guardmem_stage_latency_seconds`, `guardmem_tokens_total`.
DONE WHEN: `/metrics` scrapes clean and a Grafana dashboard shows decision mix live.
COMMIT: `feat(s13.2): prometheus metrics and grafana dashboard`

### S13.3 -- Telemetry aggregation endpoints
TIME: 60 min. The dashboard needs pre-aggregated funnel/mix/latency/cost, plus an SSE stream of
live decisions. Build the API now so week 3 is pure frontend.
DONE WHEN: `curl -N /v1/telemetry/stream` prints decisions as they happen.
COMMIT: `feat(s13.3): telemetry api and sse stream`

---

## DAY 14 — Compaction and garbage collection

### S14.1 -- Decay scoring
WHERE: `memory/compaction/decay.py`
TIME: 60 min. Implement the formula from MEMORY_ENGINE.md section 4 with per-namespace tau.
DONE WHEN: unit tests pin each term; a 180-day-old unused low-impact fact lands in COLD.
COMMIT: `feat(s14.1): decay scoring`

### S14.2 -- Tiering, tombstone sweep, rollup
TIME: 90 min. Move cold assertions out of the HNSW index; summarize into `EpisodicDigest` with
constituent assertion ids attached. Protected set: open review tasks, legal holds, in-flight audit
exports, CRITICAL impact.
DONE WHEN: 30-day simulated namespace shows stale-fact rate below 2% after GC, and no protected
assertion moved.
COMMIT: `feat(s14.2): compaction, rollup, protected set`

### S14.3 -- Context packer
TIME: 60 min. Greedy score/token knapsack over relevance x confidence x recency, with the
inclusion/exclusion list emitted into the trace.
DONE WHEN: `memory.search` with `token_budget=800` returns under budget and explains what it cut.
COMMIT: `feat(s14.3): token-budgeted context packer`

WEEK 2 EXIT GATE: the Phase 2 checklist in PHASES_AND_ROADMAP.md section 2, which owns it. In
short: latency SLAs met under 50 rps, a passing chaos test per ARCHITECTURE section 4 failure mode,
injection ASR 0% into primary, measured cost under $0.0009, one trace consistent across all three
observability backends. Write the measured numbers into DAILY_LOG as you take them.

---

# PART 4 — WEEK 3: DASHBOARD AND HITL QUEUE (Days 15-21)

Goal: an engineer can tune the system; a nurse can clear a queue. Neither reads a manual.

---

## DAY 15 — Frontend foundation

### S15.1 -- Next.js app and design tokens
TIME: 75 min
```bash
cd apps && pnpm create next-app@latest dashboard --ts --tailwind --app --eslint
cd dashboard && pnpm dlx shadcn@latest init
pnpm add @tanstack/react-query recharts d3 lucide-react
```
Put the tokens from DESIGN_SYSTEM.md 1.1 into `globals.css` and map them into the Tailwind theme.
Never write a raw hex in a component.
DONE WHEN: a token-only test page renders both palettes (control-panel dark, review-queue light),
and Lighthouse scores >= 90 on that page.
COMMIT: `feat(s15.1): dashboard scaffold and design tokens`

### S15.2 -- BFF route handlers and auth session
TIME: 60 min. All API access goes through `app/api/*` server handlers. No tenant token ever reaches
the browser (DESIGN_SYSTEM 4).
DONE WHEN: devtools network tab shows zero requests carrying an API key.
COMMIT: `feat(s15.2): bff layer and session auth`

---

## DAY 16 — Control panel part 1

### S16.1 -- Metric tiles + cross-filtering
TIME: 75 min. Six tiles: candidates, auto-write %, HITL %, reject %, cost, p95. Every tile is a
filter that applies to every other panel.
DONE WHEN: clicking "HITL 14.8%" filters funnel, mix chart, and trace stream in one action.

### S16.2 -- Extraction funnel with dropped-item sampling
TIME: 90 min. This is the differentiated view — make what got thrown away as visible as what got
stored. Clicking a stage samples 20 dropped items with their reasons.
DONE WHEN: funnel counts reconcile exactly with audit-log counts (write the reconciliation test).
COMMIT: `feat(s16.2): extraction funnel with drop sampling`

### S16.3 -- Decision mix chart
TIME: 45 min. Stacked hourly, four decision colors, and only those four saturated colors on screen.

---

## DAY 17 — Control panel part 2

### S17.1 -- Latency and routing panels
TIME: 60 min. p50/p95/p99 by stage; tier share, cost per 1k, error rate, breaker state, cache hit.

### S17.2 -- Live trace stream (SSE)
TIME: 60 min. Cap at 200 rows, pause on hover, never block the page on backpressure.

### S17.3 -- Trace detail page
WHERE: `app/(control)/traces/[traceId]`
TIME: 90 min. Waterfall + per-candidate "why" breakdown + K-sample clusters + source span + replay
button (DESIGN_SYSTEM 2.2).
DONE WHEN: from any trace you can answer "why was this flagged" in two clicks.
COMMIT: `feat(s17.3): trace detail with waterfall and rationale`

---

## DAY 18 — HITL backend

### S18.1 -- Priority queue with leases
WHERE: `hitl/queue.py`
TIME: 90 min
`priority = impact x staleness x sla_burn`. Claim with `SELECT ... FOR UPDATE SKIP LOCKED` plus a
Redis lease so two reviewers never open the same task.
DONE WHEN: integration test with two concurrent reviewers — zero double-assignment across 500
claims.
COMMIT: `feat(s18.1): hitl priority queue with leases`

### S18.2 -- SLA timers, escalation, sweeper
TIME: 60 min. Breach escalates or applies the policy default. **Never auto-approve on breach.**
DONE WHEN: a breached task moves to Overdue with escalation context, and the audit log records it.
COMMIT: `feat(s18.2): sla timers and escalation`

### S18.3 -- Decision recording
TIME: 45 min. `approve | edit | reject` with required reason codes; reviewer identity comes from
the token, never from the request body. Step-up re-auth for CRITICAL impact.
DONE WHEN: a `review.decide` call with a spoofed reviewer id in the body records the token's
identity instead.
COMMIT: `feat(s18.3): review decision recording with step-up auth`

---

## DAY 19 — Review queue UI (the one a nurse uses)

### S19.1 -- Task view layout
TIME: 90 min. Build exactly the layout in DESIGN_SYSTEM.md 3.2: proposed / currently-on-record /
where-this-came-from / plain-language flag reason / four action buttons.
Rule: **no jargon on this screen.** Not "semantic entropy 0.59" but "the model gave different
answers when asked repeatedly."

### S19.2 -- ProvenanceCard and DiffPane
TIME: 75 min. The highlight is generated server-side from `source_span` — never re-derived in the
browser. Supersession warning is mandatory on ONE-cardinality predicates.

### S19.3 -- Keyboard shortcuts and undo
TIME: 60 min. `J/K` navigate, `A` approve, `E` edit, `R` reject, `S` skip, `?` help. Ten-second
undo on every decision; optimistic UI with the network confirm inside the undo window.
DONE WHEN: you clear 10 seeded tasks without touching the mouse.
COMMIT: `feat(s19.3): keyboard-first review flow`

### S19.4 -- Edit flow with typed inputs
TIME: 60 min. Ontology-driven fields; coded values get autocomplete. A reviewer edit must be as
cheap as an approval — it is the highest-value training signal in the system.

---

## DAY 20 — Tuning loop

### S20.1 -- Threshold editor with projected impact
TIME: 90 min. C x R hex-density plot with draggable threshold lines. Dragging shows
"+2,140 auto-writes/day, -1,900 HITL tasks, est. write-precision 0.965". Apply requires confirm and
writes a versioned audited policy change.
DONE WHEN: Apply is disabled until a projection has been computed.
COMMIT: `feat(s20.1): threshold editor with impact projection`

### S20.2 -- threshold_tuner.py
WHERE: `scripts/threshold_tuner.py`
TIME: 90 min. Logistic regression on reviewer approve/reject labels; refit tau/rho and the risk
betas per namespace. **Gate promotion on Cohen's kappa >= 0.6** — if reviewers and model disagree
badly, the labels are not trustworthy and tuning must freeze.
DONE WHEN: running on synthetic labels moves thresholds sensibly and refuses to promote at low kappa.
COMMIT: `feat(s20.2): threshold auto-tuning from reviewer labels`

---

## DAY 21 — Polish and e2e

### S21.1 -- States: empty, loading, offline, lease-lost
TIME: 60 min. Skeletons preserve layout height. Offline buffers decisions and replays on reconnect.

### S21.2 -- Accessibility pass
TIME: 60 min. WCAG 2.2 AA everywhere, AAA on review body text. Decision state is never
color-alone — color + icon + label, always all three. Run axe, fix everything.

### S21.3 -- Playwright e2e
TIME: 90 min. Two critical paths: engineer finds why a candidate was flagged; reviewer clears five
tasks by keyboard.
DONE WHEN: e2e green in CI.
COMMIT: `test(s21.3): playwright e2e for both surfaces`

WEEK 3 EXIT GATE: the Phase 3 checklist in PHASES_AND_ROADMAP.md section 3, which owns it. In
short: median review time inside the PRD 6.2 target over 20 timed seeded tasks, reviewer decisions
feeding the tuner with kappa reported, "why was this flagged" answerable in two clicks, funnel
counts reconciling with the audit log, axe clean and e2e green.

---

# PART 5 — WEEK 4: EVALS, BENCHMARKS, DEPLOY (Days 22-28)

Goal: replace claims with numbers, and localhost with a URL.

---

## DAY 22 — Eval harness

### S22.1 -- Harness and datasets
TIME: 90 min. `evals/runners/run_suite.py --all` produces a versioned JSON + markdown report.
Datasets: LongMemEval subset, your 400-pair contradiction probe, the redteam corpus, three vertical
sets (clinical, legal, fintech, ~100 items each).
DONE WHEN: `make eval` runs end-to-end and writes `evals/reports/<date>.md`.
COMMIT: `feat(s22.1): eval harness and datasets`

### S22.2 -- RAG quality suite
TIME: 75 min. Faithfulness, answer relevance, context precision, context recall.
DONE WHEN: scores reproduce within +/-2% across three runs (pin seeds and model versions).

---

## DAY 23 — The metrics that matter

### S23.1 -- Memory integrity suite
TIME: 90 min. Write precision (human-audit a 300-item sample yourself — budget 2 hours),
contradiction escape rate, stale fact rate.
DONE WHEN: all three measured against the PRD 6.2 targets, gaps triaged into a list.

### S23.2 -- Drift@N
TIME: 90 min. 100-turn synthetic agent runs across three verticals, with and without GuardMem.
Plot accuracy on a fixed probe set every 10 turns.
DONE WHEN: you have the drift chart. **This is the single most important artifact of the project.**
If the curves overlap, that is a real finding — publish it and diagnose whether the failure is
retrieval (GC too gentle) or write-path (thresholds too loose).
COMMIT: `feat(s23.2): drift@n benchmark`

---

## DAY 24 — Baselines and security

### S24.1 -- Baseline comparison
TIME: 90 min. Raw RAG, mem0, Zep on identical datasets. Publish an honest table including where
GuardMem loses — added latency and setup cost are real, and pretending otherwise destroys trust
with exactly the engineers you want.

### S24.2 -- Security suite
TIME: 75 min. Injection/poisoning attack success rate; tenant isolation attempts through REST, MCP,
and SDK.
DONE WHEN: ASR into primary namespace 0%; all isolation attempts fail.

---

## DAY 25 — Load and cost benchmarks

### S25.1 -- Locust load tests
TIME: 90 min. Write path and read path at 10M vectors (generate synthetic embeddings).
DONE WHEN: the SLA table in PRD 6.1 is filled with **measured** numbers replacing the targets.

### S25.2 -- Cost benchmark
TIME: 60 min. Blended $/governed candidate vs a frontier-only, K=5 baseline.
DONE WHEN: the >= 55% token savings claim is either demonstrated or corrected to the true number.

---

## DAY 26 — Infrastructure as code

### S26.1 -- Terraform modules
TIME: 120 min. Cloud Run (gateway, mcp, dashboard), Cloud Run Jobs + Scheduler (worker),
Cloud SQL Postgres 16 with pgvector, Memorystore Redis, Secret Manager, Artifact Registry, GCS.
**Databases move here now, not earlier** — deploying a moving target wastes days.
DONE WHEN: `terraform apply` on a clean project produces a working environment from zero.
COMMIT: `feat(s26.1): terraform for gcp`

### S26.2 -- Migration to Cloud SQL
TIME: 60 min. Run alembic against Cloud SQL via the auth proxy; seed a demo tenant.
DONE WHEN: the deployed gateway serves `/healthz` and a real propose lands in Cloud SQL.

### S26.3 -- CI/CD pipeline
TIME: 90 min. build -> trivy + gitleaks + pip-audit -> cosign sign + SBOM -> deploy staging ->
smoke test -> manual gate -> prod.
DONE WHEN: one command releases; you have tested a rollback.
COMMIT: `ci(s26.3): release pipeline with signing and sbom`

---

## DAY 27 — Regression gates and runbooks

### S27.1 -- Nightly eval workflow
TIME: 60 min. Run the suites against a pinned baseline; fail the build if any metric regresses more
than 2 absolute points.
DONE WHEN: you deliberately break the scorer and CI catches it.

### S27.2 -- Runbooks and a game day
TIME: 90 min. Write and then actually execute: memory-poisoning incident, HITL queue backlog,
provider outage. A runbook you have not walked through is fiction.

---

## DAY 28 — Ship

### S28.1 -- README with the drift chart
TIME: 75 min. Lead with the chart, not the architecture. First screen answers: what breaks without
this, what GuardMem does, what the numbers are.

### S28.2 -- Three-minute demo video
TIME: 60 min. Script: (1) agent writes a fact, it lands with provenance; (2) a contradictory fact
supersedes it, visible in the timeline; (3) a critical fact gets flagged, a nurse approves it in
15 seconds; (4) an injection attempt gets quarantined; (5) the drift chart.

### S28.3 -- Cold-start verification
TIME: 45 min. On a clean machine: `git clone && docker compose up && make seed && make eval`.
DONE WHEN: a stranger reproduces your headline numbers without asking you a question.
This is the real definition of done.

### S28.4 -- Final ADRs and public eval report
TIME: 45 min. Review the five ADRs in docs/adr/ against what actually got built and add one for any
substantive design change made during the month, plus the eval report with a stated limitations
section.

WEEK 4 EXIT GATE: the Phase 4 checklist in PHASES_AND_ROADMAP.md section 4, which owns it. In
short: deployed and load-tested with measured SLAs replacing the PRD targets, an eval report with
baselines and limitations, the nightly regression gate proven by a deliberate regression, and a
cold-start run from a clean clone.

---

# PART 6 — APPENDICES

## A. Quick-reference: what to build when you are stuck

If you are lost mid-week, find your day and do the next unchecked box.

```
D1  workspace, CI, docker stack, settings, types, errors, schemas, protocols, fakes
D2  noise filter, k-sample extraction, span linker
D3  migrations, pgvector store, outbox, networkx graph, ontology, seed
D4  schema gate, incumbent retrieval, 3 conflict checks, resolution matrix, dedupe
D5  entropy, confidence, impact, decision matrix, audit chain, orchestrator, replay
D6  mcp server, 4 core tools, claude desktop, resources, prompts
D7  neo4j, coverage, contract tests, retro
D8  fastapi, auth+tenancy+rls, rate limit + idempotency, async worker path
D9  provider adapters, tier routing, circuit breakers, caching
D10 cost ledger, budget caps
D11 injection detection, quarantine, pii vault
D12 trust tiers + poisoning defense, policy engine
D13 otel, prometheus, telemetry api + sse
D14 decay, compaction + rollup, context packer
D15 next.js scaffold + tokens, bff
D16 metric tiles, funnel, decision mix
D17 latency/routing panels, live stream, trace detail
D18 hitl queue + leases, sla + escalation, decision recording
D19 review task view, provenance + diff, keyboard + undo, edit flow
D20 threshold editor, threshold tuner
D21 states, a11y, playwright
D22 eval harness + datasets, rag quality
D23 memory integrity, drift@n
D24 baselines, security suite
D25 load bench, cost bench
D26 terraform, cloud sql migration, ci/cd
D27 nightly eval gate, runbooks + game day
D28 readme, demo video, cold-start verification, adrs
```

## B. Environment variables (complete list)

| Variable | Introduced | Purpose |
|---|---|---|
| `GM_ENV` | S1.4 | dev / staging / prod |
| `GM_DATABASE_URL` | S1.4 | Postgres (assertions, audit, ledger, review tasks) |
| `GM_REDIS_URL` | S1.4 | queue, rate limits, leases, cache |
| `GM_NEO4J_URI/USER/PASSWORD` | S1.4 | graph store |
| `GM_GRAPH_BACKEND` | S7.1 | `neo4j` or `networkx` |
| `GM_ANTHROPIC_API_KEY` | S2.2 | primary provider |
| `GM_OPENAI_API_KEY` | S9.1 | fallback provider |
| `GM_MODEL_FAST/BALANCED/FRONTIER` | S1.4 | pinned model ids, never aliases |
| `GM_EMBED_MODEL` | S3.2 | embedding model |
| `GM_TAU_LO/TAU_MID/TAU_HI/RHO_LO/RHO_HI` | S1.4 | decision thresholds (MEMORY_ENGINE 3.4) |
| `GM_DEFAULT_K` | S1.4 | extraction samples |
| `GM_MAX_CONCURRENT_SCORES` | S1.4 | semaphore bound |
| `GM_LLM_TIMEOUT_S` / `GM_STORE_TIMEOUT_S` | S1.4 | mandatory timeouts (RULES 2.2) |
| `GM_BLOB_PATH` / `GM_BLOB_BUCKET` | S8.4 | raw payload storage |
| `GM_OTEL_ENDPOINT` | S13.1 | collector |
| `GM_LANGFUSE_PUBLIC_KEY/SECRET_KEY` | S13.1 | trace export |
| `GM_PII_VAULT_URL` | S11.3 | separate credentials, separate role |
| `GM_BUDGET_DAILY_USD` | S10.2 | hard cap |

## C. Definition of done, per layer

| Layer | Not done until |
|---|---|
| Schemas | round-trip property tests pass and `extra=forbid` rejects unknown fields |
| L1 | every candidate has a span or the pipeline rejects it |
| L2 | contradiction probe >= 90% and invariant I2 holds under 500 generated sequences |
| L3 | worked entropy example reproduces to 3 decimals; `decide()` is provably total and deterministic |
| Store | partial dual-write is never visible; DELETE is impossible for the app role |
| Audit | tampering with any row is detected at the exact break point |
| MCP | Claude Desktop round-trip works and tool schemas pass contract tests |
| Gateway | tenant isolation fails closed on all three surfaces |
| Guardrails | injection ASR into primary namespace is 0% |
| Dashboard | funnel counts reconcile with audit-log counts |
| HITL | zero double-assignment across 500 concurrent claims |
| Evals | numbers reproduce within 2% across runs on a clean clone |

## D. The seven invariants (tape these to the wall)

Copied verbatim from `RULES.md` section 5, which owns them. If they ever differ, RULES is right.

```
I1  every AUTO_WRITE assertion has a non-null source_span
I2  no two visible assertions share (subject, predicate) when cardinality == ONE
I3  supersession is acyclic
I4  decide() is deterministic and total over (C, R, policy)
I5  audit chain verifies: digest_n == sha256(payload_n || digest_{n-1})
I6  a tombstoned assertion never appears in retrieval results
I7  tokenized PII never appears in any span attribute or log line
```

Every one of these has a test. If a test for one of these ever goes red, stop feature work.

## E. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `vector` type does not exist | extension not created in the right database | `CREATE EXTENSION vector;` in `guardmem`, not `postgres` |
| Port 5432 in use | local Postgres running | stop it or remap to 5433 and update `.env` |
| Everything routes to HITL | thresholds too tight, or budget cap hit, or breaker open | check reason codes on the decision record — they name the cause |
| Nothing routes to HITL | ontology impact levels all default to LOW | set impact per predicate in the ontology YAML |
| Entropy always 0 | K=1, or all samples identical because temperature is 0 | samples 1..K-1 must use temperature 0.7 |
| Search returns superseded facts | missing `valid_to IS NULL` filter, or `visible` never flipped | check the outbox relay is running |
| MCP server not visible in Claude Desktop | config JSON error or non-absolute command path | check the desktop logs; use an absolute path to `uvx` |
| p95 latency spike on day 14 | NLI on the critical path | batch it, or move it off the strict path |
| Coverage stuck at ~78% | untested error branches | test the `except` paths — that is where the gap always is |
| Reviewer kappa collapsing | ambiguous ontology or an over-tuned threshold | freeze tuning, re-examine the last 50 disagreements by hand |

## F. Glossary

| Term | Meaning |
|---|---|
| Assertion | one stored fact: subject + predicate + object, with provenance and validity interval |
| Candidate | a proposed assertion that has not yet been decided |
| Bitemporal | two time axes: when the fact was true (valid) and when we recorded it (system) |
| Supersession | retiring an assertion by setting `valid_to`, pointing at its replacement |
| Tombstone | the marker that makes a superseded assertion invisible to retrieval but visible to audit |
| Semantic entropy | uncertainty measured over meaning clusters, not token strings |
| Blast radius | how much downstream state a write can corrupt if wrong |
| HITL | human in the loop — the review queue |
| Thread rot | slow degradation of memory quality as stale and contradictory facts accumulate |
| Quarantine | a namespace for content that failed a security guardrail; retrievable, never promoted |
| Obligation | a structured requirement returned by a guardrail, composed into the decision |

## G. What to cut if you fall behind

Cut in this order. Each cut costs something specific — know what before you make it.

1. **Neo4j** (S7.1) — stay on NetworkX/Postgres. Cost: no graph queries in the demo.
2. **Legal and fintech vertical eval sets** (S22.1) — keep clinical. Cost: a narrower claim.
3. **Baseline comparison** (S24.1) — Cost: reviewers ask "versus what?" and you have no answer. Painful.
4. **Threshold editor UI** (S20.1) — keep the CLI tuner. Cost: a weaker demo moment.
5. **Terraform** (S26.1) — deploy by hand with `gcloud run deploy`. Cost: not reproducible.

**Never cut:** the audit chain, the span requirement, the injection guardrail, Drift@N, or the
cold-start verification. Those five are the project.

---

*End of build notebook. Update DAILY_LOG.md every day. When a step here turns out to be wrong,
fix this document in the same commit — a notebook that drifts from the code is worse than none.*
