# MCP_INTEGRATION.md — Model Context Protocol Server

MCP is GuardMem's **primary** agent surface, not a wrapper bolted onto REST. The design goal: an
agent author adds one server to their config and gets governed memory without writing a single line
of memory-handling code.

**Transports:** `stdio` (Claude Desktop, Cursor, local dev) and `streamable-http` (server-side agents,
LangGraph/CrewAI deployments). Same tool surface, same authZ scopes, same audit trail.

---

## 1. Connection & Auth

```jsonc
// Claude Desktop / Cursor — claude_desktop_config.json
{
  "mcpServers": {
    "guardmem": {
      "command": "uvx",
      "args": ["guardmem-mcp@latest"],
      "env": {
        "GUARDMEM_API_KEY": "gm_live_…",
        "GUARDMEM_BASE_URL": "https://api.guardmem.ai",
        "GUARDMEM_DEFAULT_NAMESPACE": "patient:8812",
        "GUARDMEM_MODE": "async"          // async | strict
      }
    }
  }
}
```

```bash
# server-side agents
docker run -p 8080:8080 ghcr.io/guardmem/mcp-server:1.0 --transport streamable-http
# client connects to https://mcp.guardmem.ai/v1/mcp with OAuth 2.1 (PKCE) or a scoped API key
```

**Scopes → tools**

| Scope | Grants |
|---|---|
| `memory.read` | `memory.search`, `memory.get_entity`, `memory.timeline` |
| `memory.write` | `memory.propose`, `memory.commit`, `memory.forget` |
| `review.read` | `review.list_pending`, `review.get_task` |
| `review.decide` | `review.decide` (human-delegated only; requires a reviewer-bound token) |
| `audit.read` | `audit.trace`, `audit.lineage` |

A token holding `review.decide` without a bound human identity is rejected at handshake — agents do
not approve their own memories, and that is enforced in the auth layer rather than by convention.

### 1.1 Connecting *this repository* — S6.3

**The block above is the target and cannot be pasted today.** It names a published
package (`uvx guardmem-mcp@latest`), a hosted API (`https://api.guardmem.ai`) and a
`GUARDMEM_API_KEY` that a gateway resolves a tenant from. None of the three exists:
the gateway is S8.1 and its auth middleware is S8.2, so **the server you can run
today talks straight to Postgres and has no authentication at all.** That is why it
reads `GM_MCP_TENANT_ID` from configuration — a server with no way to resolve a
tenant and no instruction about which one to serve would have to guess, and a wrong
guess is a cross-tenant read, which is the isolation failure this product exists to
prevent. It refuses instead.

Everything below was run, not drafted. Where it quotes output, that is the output.

**Prerequisites, in order.** The server does not create or migrate anything.

```bash
make dev                              # the stack, and wait for health
make migrate                          # alembic upgrade head
make seed                             # the demo tenant and its 28 assertions
```

`make seed` prints the tenant id it used:

```
tenant      demo-clinic (cfc846ef-1d68-5958-a581-31608313de15)
```

That id is derived — `uuid5(NAMESPACE_URL, "guardmem/demo-clinic/tenant/demo-clinic")` —
so it is the same on every machine, and it is the value `GM_MCP_TENANT_ID` wants.
Skip `make seed` and the tools still answer; they answer about a tenant with no rows.

**The config block.** Claude Desktop reads
`%APPDATA%\Claude\claude_desktop_config.json` on Windows and
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS.

```jsonc
{
  "mcpServers": {
    "guardmem": {
      // The console script from `uv pip install -e services/mcp_server`.
      // An absolute path, because the client does not inherit your PATH.
      "command": "C:\\path\\to\\HGEM\\.venv\\Scripts\\guardmem-mcp.EXE",
      "args": [],
      "cwd": "C:\\path\\to\\HGEM",
      "env": {
        // pragma: allowlist secret - the dev stack's throwaway credentials,
        // the same pair already in .env.example and docker-compose.dev.yml.
        "GM_DATABASE_URL": "postgresql+asyncpg://guardmem:guardmem@localhost:5432/guardmem", // pragma: allowlist secret
        "GM_REDIS_URL": "redis://localhost:6379/0",
        "GM_NEO4J_URI": "bolt://localhost:7687",
        "GM_NEO4J_USER": "neo4j",
        "GM_NEO4J_PASSWORD": "guardmem123", // pragma: allowlist secret
        "GM_MODEL_FAST": "claude-haiku-4-5",
        "GM_MODEL_BALANCED": "claude-sonnet-5",
        "GM_MODEL_FRONTIER": "claude-opus-5",
        "GM_EMBED_MODEL": "text-embedding-3-large",
        "GM_LLM_PROVIDER": "ollama",
        "GM_OLLAMA_MODEL": "llama3.1:8b",
        "GM_MCP_TENANT_ID": "cfc846ef-1d68-5958-a581-31608313de15",
        "GM_MCP_DEFAULT_NAMESPACE": "patient:7781"
      }
    }
  }
}
```

**Four things about that block are load-bearing, and three of them are the ones
people get wrong.**

1. **The ports must match your stack.** The block above uses the compose defaults.
   If your `.env` shifted them — 5433/6380/7688 is a common second checkout — the
   client config must shift with it. The client never reads `.env.example`, and it
   may not read your `.env` either; see (3).
2. **`GM_LLM_PROVIDER=ollama` is what makes this runnable without a key.**
   `memory.search` and `memory.get_entity` need no model and work regardless;
   `memory.propose` and `memory.commit` refuse by name when no provider is
   configured, and the server logs `no model provider: …` and starts anyway. For
   Anthropic, set `GM_ANTHROPIC_API_KEY` and drop `GM_LLM_PROVIDER` — but read
   `providers/anthropic_client.py` on what the missing temperature parameter does
   to §3.1's entropy before comparing any score across providers.
3. **`env` is not a convenience — a spawned server inherits almost nothing.** The
   MCP SDK passes only `DEFAULT_INHERITED_ENV_VARS` plus this dict, so `GM_*`
   exported in your shell does **not** reach the process. `Settings` also resolves
   `.env` against the **working directory**, and the client chooses that directory:
   without `cwd`, it is wherever Claude Desktop was launched from, which on Windows
   can be `C:\Windows\System32`. Either set `cwd` to the repository so `.env` is
   found, or put every value in `env`. Doing both is fine and is what the block does.
4. **Restart Claude Desktop fully.** Closing the window leaves it in the tray;
   the config is read at process start.

**When it is wrong, it says so in one line.** `preflight()` runs before the
transport opens, so a misconfigured server declines to start rather than accepting a
pipe and dying mid-handshake. Run the command yourself from a directory with no
`.env` and this is the whole output:

```
ERROR mcp_server.server guardmem-mcp cannot start: 9 setting(s) are missing or
invalid: database_url, embed_model, model_balanced, model_fast, model_frontier,
neo4j_password, neo4j_uri, neo4j_user, redis_url. Settings are read from
GM_-prefixed environment variables, or from a .env file in the working directory -
which is C:\Users\you\AppData\Local\Temp - and there is no .env there. A server
launched by an MCP client inherits only a safe subset of the environment, so GM_*
exported in a shell does not reach it: put the values in that client's own `env`
block, or start the server in a directory that has a .env.
```

Exit code **2**, not 1 — distinct from a crash, so a supervisor does not restart a
misconfigured server forever. Claude Desktop reports every one of these identically
as "server disconnected" and buries the log, which is the reason that line exists.
Its log is `%APPDATA%\Claude\logs\mcp-server-guardmem.log`.

**What you should see.** The server advertises four tools —
`memory.search`, `memory.propose`, `memory.commit`, `memory.get_entity` — and
`initialize` returning at all is the real assertion: the lifespan opens the Postgres
pool and parses the ontology *before* the first response is written, so a bad
`GM_DATABASE_URL` fails the handshake rather than the first call.

**The gate.** Say to Claude Desktop:

> Remember that the patient's preferred pharmacy is CVS #4021.

then look for the row, its span, its score and its audit event:

```sql
SELECT a.id, a.predicate, a.object_json, a.visible, p.verbatim, p.char_start, p.char_end
FROM assertion a JOIN provenance p ON p.assertion_id = a.id
WHERE a.tenant_id = 'cfc846ef-1d68-5958-a581-31608313de15'
ORDER BY a.created_at DESC LIMIT 5;

SELECT kind, payload->'decision' AS decision, payload->'confidence'->'confidence' AS c
FROM audit_event
WHERE tenant_id = 'cfc846ef-1d68-5958-a581-31608313de15'
ORDER BY seq DESC LIMIT 10;
```

**`visible` will be `false`, and that is the design, not a failure.** ADR-0010's
applier commits the assertion, its provenance, its outbox event and its audit events
in one transaction; the relay applies the graph side and only then flips `visible`.
Nothing runs the relay in this configuration — `services/worker` is S8.4 — so the
row is durable, sourced, scored and audited, and deliberately not yet retrievable.
Drain it by hand with `OutboxRelay.run_once()` if you want `memory.search` to find
it.

**What it produced here**, on `llama3.1:8b`, 2026-09-16 — the sentence above, through
a spawned `guardmem-mcp` over real stdio pipes, `mode=strict`:

| | |
|---|---|
| `predicate` / `object_json` | `preferred_pharmacy` / `"CVS #4021"` |
| `verbatim` / `source_span` | `CVS #4021` / `[50, 59)`, `alignment 1.0` |
| `confidence` | `0.8375` (`semantic_entropy 0.0`, `grounding 0.95`) |
| `risk` | `0.2789` |
| decision | `auto_write`, reasons `C_AT_OR_ABOVE_TAU_HI`, `R_BELOW_RHO_LO` |
| audit | `DECISION` + `WRITE`, identical `created_at` — one transaction, ADR-0010 |
| `visible` | `false`, pending the relay |

`content[50:59]` of the submitted string is exactly `CVS #4021`, so the span is
checkable rather than decorative — which is the point of `PRD.md`'s "no span, no
write". Four Ollama calls, about two minutes wall clock: one noise filter and
K=3 extraction. Entropy is `0.0` because all three samples agreed.

**Then drain the outbox and the read path closes the loop.** One
`OutboxRelay.run_once()` reported `claimed=1 dispatched=1`, flipped `visible` to
true, and `memory.search` — over the same spawned server — returned it:

```jsonc
{ "assertion_id": "eca881e5-…", "predicate": "preferred_pharmacy",
  "object": "CVS #4021", "confidence": 0.8375, "valid_to": null,
  "provenance": [ { "span": [50, 59], "verbatim": "CVS #4021",
                    "tier": "verified_user", "source_hash": "sha256:4e6bdd8b…" } ] }
```

That is `PHASES_AND_ROADMAP.md`'s first Phase-1 exit-gate line — propose → decide →
write → `memory.search` returns it with provenance — over the real transport. The
box stays unticked because it says *from Claude Desktop* and this was driven by a
programmatic MCP client: same spawned binary, same stdio pipes, same `env` handling,
same tool surface, but not the desktop app's own UI. That last click is yours.

**Two things in that result are worth not misreading.**

- **The query has to be narrow, because retrieval is not semantic yet.** Searching
  `"pharmacy"` does *not* return this row. `HashEmbedder` hashes text — identical
  text retrieves identically and nothing else does — so ranking is near-noise until
  a real embedder lands. Filter by `predicates` or query the exact verbatim. Nothing
  measured against `HashEmbedder` is a retrieval-quality number.
- **The seeded tenant already had a `preferred_pharmacy`,** and this did not
  supersede it. They are on different subjects — `3c723e01…` from the seed,
  `8dadc187…` here — because "the patient" in a one-line proposal does not resolve
  to the seeded entity. ADR-0008 is why that is a new binding rather than a fuzzy
  match to the nearest candidate. Two believed rows for one predicate is therefore
  correct, not a cardinality violation.

**The payload keys are `assertions` and `excluded`**, not `results`.

**A `hitl_review` is also a pass.** `MEMORY_ENGINE.md` §3.4 sends the ambiguous
middle to a human, and an 8B local model is exactly the kind of extractor that lands
there on a less clear-cut sentence. What the gate asks for is a governed row with a
span, a score and an audit event — not a particular decision. A `REJECT` is a real
outcome too and still audits. What would be a failure is a row with no provenance,
or a decision with no audit event.

**One note for anyone writing a client rather than using Claude Desktop.** The
Python SDK exposes the payload as `result.structured_content`; `structuredContent`
is the wire name and reading it off the model object silently returns `None`,
which looks exactly like a server that answered with nothing. The tools always
populate it — `memory.search` returns its results there and only a summary line in
`content[0].text`.

---

## 2. Tools

### 2.1 `memory.search`

```json
{
  "name": "memory.search",
  "description": "Search governed long-term memory. Returns only currently-believed, non-tombstoned assertions with provenance. Use this before answering anything that depends on facts about this subject.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query":      {"type": "string", "description": "Natural-language query."},
      "namespace":  {"type": "string", "description": "Defaults to server-configured namespace."},
      "subject":    {"type": "string", "description": "Optional entity filter."},
      "predicates": {"type": "array", "items": {"type": "string"}},
      "as_of":      {"type": "string", "format": "date-time",
                     "description": "Point-in-time query: what was believed at this instant."},
      "min_confidence": {"type": "number", "default": 0.6, "minimum": 0, "maximum": 1},
      "limit":      {"type": "integer", "default": 10, "maximum": 50},
      "token_budget": {"type": "integer", "default": 1500,
                       "description": "Context packer trims to fit."}
    },
    "required": ["query"]
  }
}
```

Result (`structuredContent`):
```json
{
  "assertions": [{
    "assertion_id": "a_7f21…",
    "subject": "patient:8812", "predicate": "allergy", "object": "penicillin",
    "confidence": 0.94, "valid_from": "2026-03-12T00:00:00Z", "valid_to": null,
    "corroboration": 2, "reviewed_by": "rn:sarah.r", "reviewed_at": "2026-03-12T14:31:02Z",
    "provenance": {"source_hash": "sha256:9c1…", "span": [212, 271], "tier": "verified_user",
                   "verbatim": "…penicillin — it gives me hives…"}
  }],
  "excluded": [{"assertion_id": "a_11c…", "reason": "superseded_by a_7f21", "at": "2026-03-12"}],
  "tokens_used": 412,
  "trace_id": "tr_9f2a3c"
}
```

The `excluded` array is deliberate: the agent should be able to tell the difference between "we have
no record" and "we retired that record," and so should anyone reading the transcript later.

### 2.2 `memory.propose`

```json
{
  "name": "memory.propose",
  "description": "Submit candidate facts for governance. Facts are NOT immediately stored — they are extracted, validated, scored, and either auto-written, queued for human review, or rejected. Returns a trace_id and per-candidate decisions. Prefer passing raw conversation text over pre-structured facts: the extractor needs source spans for provenance.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "content":   {"type": "string", "description": "Raw turn(s), tool output, or document text."},
      "namespace": {"type": "string"},
      "source_tier": {"enum": ["trusted_system","verified_user","unverified_user","tool_output","retrieved_web"],
                      "default": "unverified_user"},
      "risk_hint": {"enum": ["low","default","high"], "default": "default"},
      "mode":      {"enum": ["async","strict"], "default": "async",
                    "description": "strict blocks until decided (~450ms p95); async returns immediately."},
      "hints": {"type": "object", "properties": {
        "subject": {"type": "string",
                    "description": "An EntityId, not a name (ADR-0008). Entity resolution binds and never matches, so a surface form here resolves nothing. Omit it when the namespace is subject-bound."},
        "predicates_of_interest": {"type": "array", "items": {"type": "string"}}
      }},
      "idempotency_key": {"type": "string"}
    },
    "required": ["content"]
  }
}
```

Result:
```json
{
  "trace_id": "tr_9f2a3c",
  "status": "decided",                 // "accepted" when mode=async
  "candidates": [
    {"candidate_id": "c_1", "predicate": "allergy", "object": "penicillin",
     "decision": "hitl_review", "confidence": 0.91, "risk": 0.82,
     "reason_codes": ["R_ABOVE_RHO_HI", "POL_REQUIRE_CORROBORATION"],
     "review_task_id": "rt_88a1", "eta_minutes": 26},
    {"candidate_id": "c_2", "predicate": "preferred_pharmacy", "object": "CVS #4021",
     "decision": "auto_write", "assertion_id": "a_9d33", "confidence": 0.94, "risk": 0.21}
  ],
  "dropped_noise": 3,
  "quarantined": 0
}
```

**Agent-facing guidance embedded in the tool description:** an agent that receives `hitl_review`
should proceed *without* treating the fact as established, and may tell the user it's pending
confirmation. This is the behavior that makes governance visible rather than mysterious.

**Two fields the server adds, and why (S6.2's write half).**

- `applied` (boolean). True when any candidate in this proposal produced a row.
  ADR-0010's applier writes the assertion, its citation, its outbox event and
  its audit events in **one transaction**, so `RULES.md` non-negotiable #4 holds
  by construction. A candidate that wrote nothing carries `not_applied` instead
  of `assertion_id` - `decision_reject`, `decision_hitl_review`,
  `decision_escalate`, or `merge_not_implemented` - so a caller reads what
  happened rather than inferring it from a missing field.

  The row is **not yet visible**. The outbox event commits with it and the relay
  applies the graph side and sets `visible`, which is §2.4 unchanged: a reader
  sees nothing until both sides land.
- `failed` (array of `{candidate_id, code}`). `run()` returns a failed candidate
  *beside* the decisions rather than discarding the batch, and §2.2 has no field
  for one. Omitting them would mean a fact the caller submitted vanished from
  the answer. The stable `code` is carried and never the message, per
  `RULES.md` §1.5.

**`memory.propose` needs a model provider and `memory.search` does not.** A
server started with no credential logs a warning, serves the two read tools, and
refuses the two write tools by name. Set `GM_LLM_PROVIDER` - `ollama` needs no
key.

### 2.3 `memory.commit`
For agents that already have structured, high-trust facts (e.g. a signed EHR payload). Still passes
the full pipeline — `commit` is not a bypass, it just skips L1 extraction and requires the caller to
supply provenance explicitly.

```json
{"name": "memory.commit",
 "inputSchema": {"type":"object","properties":{
   "assertions": {"type":"array","items":{"type":"object","required":["subject","predicate","object","provenance"]}},
   "namespace": {"type":"string"},
   "idempotency_key": {"type":"string"}},
  "required": ["assertions"]}}
```
Missing or unverifiable provenance → `GM_VALIDATION`. There is no unsourced write path in this API.

### 2.4 `memory.get_entity`
Returns the graph view: an entity, its live assertions grouped by predicate, and 1-hop neighbors with
edge confidences. Optional `as_of` for point-in-time reconstruction.

### 2.5 `memory.timeline`
Belief history for a `(subject, predicate)` pair: every assertion, when it was believed, what
superseded it, and who decided. This is the tool a support engineer reaches for when a user says
"the assistant used to know my address."

### 2.6 `memory.forget`

```json
{"name": "memory.forget",
 "description": "Retire assertions. Retirement is a supersession with a tombstone — the fact stops being returned but remains auditable. True erasure (GDPR) requires the `erase` mode and a human-approved request.",
 "inputSchema": {"type":"object","properties":{
   "assertion_ids": {"type":"array","items":{"type":"string"}},
   "subject": {"type":"string"}, "predicate": {"type":"string"},
   "reason": {"type":"string"},
   "mode": {"enum":["retire","erase"], "default":"retire"}},
  "required": ["reason"]}}
```
`mode=erase` always routes to HITL with step-up auth, regardless of confidence. An agent cannot
unilaterally destroy audit-bearing state.

### 2.7 `review.list_pending` / `review.get_task` / `review.decide`
Lets a human-supervised client (Claude Desktop with a reviewer-bound token) surface and clear the
queue in-conversation. `review.decide` takes `{task_id, action: approve|edit|reject, edited_object?,
reason_code, reviewer_note?}` and records the reviewer identity from the token, never from the
arguments.

### 2.8 `audit.trace` / `audit.lineage`
`audit.trace(trace_id)` returns the full decision record (spans, scores, models, prompt versions).
`audit.lineage(assertion_id)` walks the supersession chain back to first origin and verifies the
hash chain, returning `{verified: true, broken_at: null}`.

### 2.9 `policy.evaluate` (dry-run)
Evaluates a hypothetical candidate against current policy without writing anything. Useful for agent
authors debugging why their writes keep landing in review.

---

## 3. Resources

Resources are read-only context an agent (or a human in Claude Desktop) can attach directly.

| URI template | Returns | MIME |
|---|---|---|
| `guardmem://memory/{namespace}` | current believed-state snapshot, token-budgeted | `text/markdown` |
| `guardmem://memory/{namespace}/{entity_id}` | entity card: assertions + neighbors + confidences | `application/json` |
| `guardmem://timeline/{assertion_id}` | belief history | `application/json` |
| `guardmem://audit/{trace_id}` | full decision record | `application/json` |
| `guardmem://policy/{pack_name}` | active policy pack + version | `text/yaml` |
| `guardmem://ontology/{namespace}` | predicate schema (so agents extract the right shapes) | `text/yaml` |
| `guardmem://queue/pending` | reviewer's pending tasks | `application/json` |

Resources support `subscribe` — a client gets `notifications/resources/updated` when a namespace
snapshot changes, which is how a desktop client keeps a live memory panel accurate without polling.

**Templates** are advertised via `resources/templates/list` so clients can construct URIs for
namespaces they discover at runtime.

---

## 4. Prompts

| Prompt | Args | Purpose |
|---|---|---|
| `guardmem/extract_memories` | `content`, `ontology_ref`, `k` | The canonical extraction prompt, version-pinned. Exposed so external agents extract in exactly the shape the pipeline validates — reduces schema-gate rejections dramatically. |
| `guardmem/adjudicate_conflict` | `candidate`, `incumbent`, `ontology_ref` | Structured conflict reasoning: entailment, contradiction, temporal ordering, recommended resolution. |
| `guardmem/review_brief` | `task_id` | Renders a flagged candidate as a plain-language brief for a human reviewer — the same copy the dashboard uses, so a reviewer working in Claude Desktop sees an identical framing. |
| `guardmem/memory_hygiene_report` | `namespace`, `window` | Narrated summary of drift, stale facts, and contradiction pressure for a namespace. |

---

## 5. Framework Adapters

```python
# LangGraph — governed memory as a node
from guardmem.middleware.langgraph import GuardMemNode

graph.add_node("remember", GuardMemNode(
    namespace=lambda s: f"patient:{s['patient_id']}",
    source_tier="verified_user",
    mode="async",                 # never block the agent loop
    on_hitl=lambda d: state_update(pending_facts=d.candidates),
))
```

```python
# CrewAI — memory tool for every agent in the crew
from guardmem.middleware.crewai import guardmem_tools
crew = Crew(agents=[intake, triage], tools=guardmem_tools(namespace="patient:8812"))
```

```python
# LlamaIndex — GuardMem as a governed retriever in front of an existing index
from guardmem.middleware.llamaindex import GovernedRetriever
retriever = GovernedRetriever(base=index.as_retriever(), min_confidence=0.7)
```

---

## 6. Error Contract

| GuardMem code | MCP error | Agent should |
|---|---|---|
| `GM_VALIDATION` | `-32602` invalid params | fix the payload; don't retry unchanged |
| `GM_POLICY` | `-32001` | surface to the user; the write is not permitted |
| `GM_INJECTION` | `-32002` | stop; the content is quarantined and a human is alerted |
| `GM_BUDGET` | `-32003` | back off; writes are degraded to review-only |
| `GM_PROVIDER` / `GM_STORE` | `-32603` internal | retry with jittered backoff (advertised `retry_after`) |
| `GM_CONFLICT` | `-32004` | re-read with `memory.search`, then re-propose |

Every error carries `trace_id` so a failure in an agent log is one lookup away from the full decision
record.

---

## 7. Server Behavior Guarantees

1. **Idempotent writes.** `idempotency_key` is honored for 24 h; a repeat returns the original result
   rather than re-running the pipeline.
2. **No tool bypasses governance.** There is no MCP path to the store that skips scoring — including
   `commit`.
3. **Tool descriptions are behavior.** They are versioned, eval-tested (does an agent call
   `memory.search` before answering? does it correctly not assert `hitl_review` facts?), and treated
   as prompt engineering, because that's what they are.
4. **Read consistency.** `memory.search` never returns an assertion whose dual-write hasn't landed
   (`visible=true` gate).
5. **Sampling not required.** GuardMem's MCP server never calls back into the client's model for
   its own decisions — all model use is server-side and billed to the tenant, so agent authors don't
   get surprise token charges in their own loop.
