# ADR-0002: Bitemporal tombstones over hard delete

**Status:** Accepted
**Date:** 2026-01-15

## Context

Regulated customers need to reconstruct what the agent believed at any past
timestamp. Hard-deleting a superseded fact loses that history and makes
"why did the agent do X on 2026-03-14?" unanswerable.

## Decision

No row is ever deleted. Assertions carry two time axes:

- `valid_time` — when the fact is true in the real world.
- `system_time` — when the system recorded that belief.

Supersession is a *new row* with a pointer to the tombstoned prior. `forget`
sets a tombstone and hides the row from retrieval; it never issues a `DELETE`.

## Consequences

- Storage grows monotonically. Compaction (`memory/compaction/*`) handles the
  cost side: decay, TTL tiers, cold-tier summarization.
- `replay_trace` and "as-of" queries are trivial single-table selects.
- Right-to-erasure (GDPR) is handled by rewriting rows to reference a
  vault-token, not by deleting audit history.
