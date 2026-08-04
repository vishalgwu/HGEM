# Runbook: Model provider outage

**Severity default:** SEV-2, escalates to SEV-1 if the fallback path is also
degraded.

## Detection

- Circuit breaker opens on a provider (`llm/fallback.py` metric).
- Elevated 5xx from `llm.providers.<name>` in traces.
- Provider status page.

## Immediate actions (T + 0–15 min)

1. Confirm the failure is provider-side, not our egress or auth
   configuration — check a raw curl against the provider's health endpoint
   from a worker pod.
2. The router (`llm/router.py`) should already have shifted to the fallback
   tier; verify that traffic actually moved by checking the per-provider
   request counter.
3. **Do not widen the auto-write path to compensate for lower fallback
   quality.** Under degraded quality, the correct behavior is more HITL, not
   more auto-writes.

## Investigation

- If the fallback tier is also spiking latency, the router's hedged-request
  policy is likely amplifying load — temporarily disable hedging via the
  runtime flag.
- If a specific tenant's budget cap hit at the same time, tokens/$ ledger
  and provider outage together can look like a single event — check
  `budget.py` metrics separately.

## Follow-up

- Add the outage window to the tenant status page.
- If the outage exposed a fallback that didn't behave as designed (wrong
  provider selected, cache poisoned across providers), file that as its
  own bug — the outage was the trigger, not the root cause.
