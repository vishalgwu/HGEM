# ADR-0010: The applier owns one Postgres transaction, and `VectorStore` does not change

**Status:** Accepted
**Date:** 2026-09-16
**Amends:** nothing. `ARCHITECTURE.md` §2.4 and `RULES.md` non-negotiable #4 are
implemented rather than altered.

## Context

`pipeline.run()` reaches a decision and applies none. That is not an omission —
`orchestrator.py` has said since S5.6 that finishing it "would mean either
widening the protocol or teaching the store about audit. Both are ADR-sized."
This is that ADR.

The gap is narrow and precisely shaped, and four facts define it.

**1. `RULES.md` non-negotiable #4 is absolute.** The audit event commits "in the
same transaction as the state change", or not at all. A chain that can be
missing its most recent link is not a chain; the whole product claim is that a
write can be explained afterwards.

**2. The two halves cannot currently share a transaction.** They were built to
opposite conventions, each correct in isolation:

```python
async def upsert(self, assertions: Sequence[StoredAssertion]) -> None: ...   # no connection
async def append(connection: Conn, *, tenant_id, trace_id, kind, ...): ...   # needs one
```

`VectorStore.upsert` owns its transaction internally, because S3.3 makes the
assertion row and its outbox event commit atomically and a protocol that handed
out a connection would be a protocol about Postgres. `audit_store.append` takes
a connection precisely so the audit row commits with the state change — S5.5
built it that way on purpose. Neither is wrong. They simply cannot be composed
without something deciding who owns the transaction.

**3. The audit chain exists and nothing calls it.** `observability/audit.py` and
`audit_store.append` have been built and tested since S5.5, and no write path
appends an event (open item #18). The chain is real, verified, tamper-evident —
and empty.

**4. A decision is not one effect.** §2.3's resolution hints and §3.4's four
decisions imply different state changes, and an applier that only knew how to
`INSERT` would be wrong for most of them:

| Decision / hint | State change | Audit kind |
|---|---|---|
| `AUTO_WRITE`, hint `coexist` | insert the assertion | `WRITE` |
| `AUTO_WRITE`, hint `supersede` | insert, then retire the incumbent | `WRITE` + `SUPERSEDE` |
| `AUTO_WRITE`, hint `merge` | raise the incumbent's `corroboration_count`; **no new row** | `WRITE` |
| `HITL_REVIEW` | a review task (S18.1, unbuilt) | `REVIEW` |
| `REJECT`, `ESCALATE` | none | `DECISION` only |

## Decision

**The applier is a Postgres-specific composition that owns one transaction per
candidate, and the `VectorStore` protocol does not change.**

`guardmem_core/memory/applier.py`. It opens one `tenant_transaction` and, inside
it, writes every effect a single decision implies: the assertion row, its
provenance, the outbox event, the supersession or the merge, and the audit
events. One commit. `RULES.md` #4 then holds by construction rather than by
convention.

**`PgVectorStore` grows connection-taking variants of the writes it already
does; the `VectorStore` Protocol does not.** This is the crux, and it is the
difference between a guarantee and a guarantee-on-some-backends. Widening the
*protocol* would mean a Qdrant store either fabricates a Postgres transaction or
silently drops the atomicity — so #4 would hold where nobody checked and fail
where nobody looked. Widening the *concrete class* keeps the seam
`ARCHITECTURE.md` §2.4 rests on ("backend swaps are config, not code") while
letting one backend offer a stronger promise than the protocol can express. A
Qdrant deployment gets a different applier, and the absence of the guarantee is
then a visible fact about that deployment rather than a silent one.

Naming `PgVectorStore` is allowed here for the same reason `mcp_server` may: the
applier is infrastructure, not pipeline. The import-linter contract forbids
`guardmem_core.pipeline` from importing a concrete store, and this is
`guardmem_core.memory`, which is where the concrete stores already live.

**One transaction per candidate, not per proposal.** A batch-wide transaction
would discard nineteen good writes because the twentieth hit a constraint —
which is the argument `_score_one` already makes about scoring and which applies
with more force here, because the discarded nineteen were *decided*. Candidates
are independent by construction: each has its own subject, its own conflict
report and its own audit entry.

**`run()` still writes nothing, and the applier is called after it.** Keeping
the two apart preserves a property worth having: the decision layer is pure of
side effects and its test asserts the store stays empty. The cost is a window
between deciding and applying, and the failure in that window is *nothing
happened* — no row, no audit event, no half-state. The proposal can be re-run
and `replay_trace.py` reproduces the same decision, because `decide()` reads
nothing but its arguments (invariant I4).

**Every decision is audited, including the ones that write nothing.** A
`REJECT` that leaves no trace cannot be reviewed, tuned against, or explained to
the person whose fact was dropped — and `threshold_tuner.py` (S20.2) refits from
exactly those outcomes. `DECISION` is appended for all four; `WRITE`,
`SUPERSEDE` and `REVIEW` join it where the state actually changed.

**Idempotency is by derived id, not by a flag.** Every id on the write path is
already a `uuid5` of stable inputs — provenance from `(assertion_id,
source_hash, span)`, the outbox row from `"outbox/" + assertion_id` — so a
replayed apply is `ON CONFLICT DO NOTHING` and a no-op. §2.2's
`idempotency_key`, which is published and currently unused, becomes the caller's
way of saying "this is the same proposal", and that is a distinct question from
"this is the same assertion".

### What this does not decide

- **`memory.commit`'s confidence.** §2.3 skips L1 extraction, so §3.1's entropy
  has no sampling distribution and `w_H = 0.35` of `C` is undefined for a fact
  no model drew. The daily log of 2026-09-16 said these two would be decided
  together because both "are about the transaction boundary". That was wrong:
  this one is about atomicity and that one is about scoring, and bundling them
  would hide a scoring judgement inside a storage ADR. **ADR-0011.**
- **The HITL row itself.** `REVIEW` is in the vocabulary and S18.1 builds the
  queue. Until then a `HITL_REVIEW` decision is audited and produces no task,
  and `memory.propose` reports no `review_task_id` — §2.2 already marks that
  field as arriving with the queue.
- **Async application.** ADR-0003 puts evaluation behind a queue (S8.4). Where
  the applier runs is that step's decision; what it does atomically is this one.

## Alternatives rejected

- **Widen `VectorStore.upsert` to take an optional connection.** The cheapest
  edit and the worst outcome: `RULES.md` #4 would then hold on Postgres and be
  quietly unenforceable on any other backend, with the same call site and the
  same green tests. A guarantee that is silently backend-dependent is worse than
  one that is explicitly unavailable.
- **Teach the store about audit** — `upsert(assertions, events)`. Inverts the
  layering (a store would import observability) and makes every future backend
  responsible for implementing a hash chain correctly, which is the one thing
  in this system least suited to being reimplemented per adapter.
- **Write the audit event through the outbox.** Attractive, because the outbox
  is already transactional with the assertion. But then the event lands when the
  relay dispatches, not when the state changes, and a relay that never runs
  leaves a durable assertion with no audit row. That is precisely the state #4
  forbids.
- **Two transactions, audit second, compensate on failure.** A compensating
  delete is impossible here by construction: `0001_initial` revokes `DELETE` on
  `assertion` and `UPDATE`/`DELETE` on `audit_event` at the role level. The
  schema will not permit the cleanup this design needs, which is the schema
  being right.
- **Put the applier in `pipeline/`.** Forbidden by the import-linter contract,
  and rightly: the pipeline talks to protocols so a backend swap stays
  configuration, and an applier that names `PgVectorStore` is not a pipeline
  concern.

## Consequences

- **The audit chain gets its first caller**, closing open item #18. Everything
  S5.5 built — `canonical_json`, the digest chain, `verify_chain`'s two checks
  per link, the advisory lock that stops two appends forking the chain — starts
  being exercised by real writes rather than by tests.
- **`PgVectorStore` gains connection-taking write methods**, and the existing
  transaction-owning ones are expected to become thin wrappers over them so the
  two paths cannot drift. A second copy of the row mapping would be the obvious
  way to get this wrong.
- **S6.3 becomes reachable.** Its DONE WHEN — "you can see the row in Postgres
  with a source span, a confidence score, and an audit event" — is exactly this
  ADR's output, and it is the Day-7 gate hit early.
- **`memory.propose` will return `applied: true` and an `assertion_id`.** Both
  were added by S6.2's write half specifically as the honest placeholder for
  this; `MCP_INTEGRATION.md` §2.2 records that they flip together.
- **A `merge` writes no assertion**, which will look like a missing row to
  anyone reading the count. §2.4 is explicit that a merge raises the incumbent's
  `corroboration_count` and appends provenance, and `dedupe.merge` already does
  it — the applier calls it rather than inserting.
- **The graph side still lands asynchronously.** The applier commits the
  assertion and the outbox event; the relay applies the graph write and flips
  `visible`. Nothing here changes that, and a reader still sees nothing until
  both sides are done — which is the property that makes a partial write
  unretrievable rather than briefly wrong.
