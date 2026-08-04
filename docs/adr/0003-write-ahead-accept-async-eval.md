# ADR-0003: Write-ahead accept, async eval

**Status:** Accepted
**Date:** 2026-01-15

## Context

Full evaluation of a candidate (K-sample extraction, entropy, NLI conflict
check, impact scoring, policy engine) costs 500ms–2s. Blocking the agent on
that per turn destroys the UX.

## Decision

`propose` returns in <80ms with a `trace_id`, having only run the security
armor and enqueued the candidate. The worker runs L1–L3 asynchronously and
publishes the decision.

Agents that need synchronous certainty opt into `mode=strict`, which runs the
pipeline inline with K=1 on the FAST tier.

## Consequences

- The gateway is I/O-bound, not compute-bound. Cloud Run scales trivially.
- We need a durable queue (Redis stream) and a way for the agent to observe
  the decision — the audit log endpoint doubles as the callback surface.
- Strict mode is available but discouraged; docs will steer people to
  async-with-callback.
