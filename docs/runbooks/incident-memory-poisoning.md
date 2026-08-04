# Runbook: Memory poisoning incident

**Severity default:** SEV-1 for shared/org namespaces, SEV-2 for tenant-scoped.

## Detection

Any of the following:

- Injection detector fires and the pipeline routed a candidate to
  `QUARANTINE` — a security alert should already be paging.
- Reviewer flags a landed assertion as "content-controlled" during HITL.
- A downstream regression spike shows a specific fact suddenly widespread
  across tenants.

## Immediate containment (T + 0–15 min)

1. Freeze the namespace: `POST /v1/policy/quarantine` with the affected
   `namespace`. This flips the auto-write threshold to ∞ — every proposal
   goes to HITL.
2. Snapshot the audit chain segment: `scripts/replay_trace.py --namespace X
   --since Y --dump`. This is your forensic record — do this before anything
   mutates.
3. Rotate any provider keys the attacker may have exfiltrated context to.

## Investigation (T + 15 min – 2 hr)

1. Identify patient-zero write: earliest assertion in the namespace with the
   contaminant text or entity — the audit chain gives you the exact `trace_id`.
2. Walk the outbound edges: every subsequent assertion whose retrieval touched
   the poisoned node. `scripts/replay_trace.py --downstream <trace_id>`.
3. Tag every affected assertion with a tombstone reason `POISONING_SUSPECTED`.
   Do not delete — bitemporal history is what proves what happened.

## Remediation

1. Supersede each affected assertion with a corrected value (or leave it
   tombstoned if no ground truth exists).
2. Add the attack vector to `tests/security/` as a regression case — the CI
   injection corpus grows here.
3. If the injection bypassed the pre-flight detector, treat that as its own
   sev-1 bug: the detector is the load-bearing guardrail for this class.

## Comms

- Notify affected tenants within the incident window their contract requires.
- File a post-incident writeup in the audit-events blob with the trace-id
  range, actions taken, and the regression test that now covers it.
