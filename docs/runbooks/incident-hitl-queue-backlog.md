# Runbook: HITL queue backlog

**Severity default:** SEV-3, escalates to SEV-2 if any task breaches its SLA.

## Detection

- `hitl_queue_depth` metric above the tenant's warn threshold for >10 min.
- Any task with `sla_deadline_ts < now` — the SLA sweeper should already have
  paged.

## Immediate actions (T + 0–15 min)

1. Check the router: `assignment.py` distributes by reviewer skill +
   namespace. If a single reviewer's queue is spiking while others are idle,
   a routing rule is misconfigured — patch the skill map.
2. Auto-escalate breached tasks: `POST /v1/review/escalate?sla=breached`.
   Escalation reroutes to the on-call reviewer pool.
3. If the queue depth is growing faster than reviewer throughput, temporarily
   raise the auto-write threshold τ within the tenant's policy ceiling.
   Log this as a policy override in the audit chain.

## Investigation

- Is confidence scoring calibrated? A sudden queue spike often means the
  decision matrix is treating routine writes as ambiguous — check
  `threshold_tuner.py` output vs. current τ/ρ.
- Is one provider degraded? If the FRONTIER tier is slow, the extractor may
  be falling back to a noisier model and generating more borderline
  confidences. Check the LLM router circuit-breaker state.

## Follow-up

- If threshold drift caused the backlog, refit τ/ρ against the last N
  labelled decisions and open a PR against the tenant's policy pack.
- If reviewer capacity is genuinely under-provisioned, that's a business
  conversation, not an ops action — file an issue with the queue-depth
  trend attached.
