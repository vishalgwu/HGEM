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
