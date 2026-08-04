# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, email the
maintainers with a description, reproduction steps, and impact assessment. We
aim to acknowledge within 3 business days.

## Threat model — summary

GuardMem AI is a governance gateway that sits between agents and persistent
memory. The threats we design against, in rough priority order:

1. **Memory poisoning.** Injected instructions inside retrieved documents,
   user messages, or tool outputs that attempt to write attacker-chosen facts
   into persistent memory.
2. **Prompt injection into the extraction path.** Content that tries to
   coerce the extractor or adjudicator LLM into producing controlled outputs.
3. **Cross-tenant leakage.** Row-level-security bypass, retrieval that crosses
   namespace boundaries, or audit records exposed to the wrong tenant.
4. **PII exfiltration.** Raw PII persisted in logs, prompts, or audit blobs
   instead of tokenized vault references.
5. **Wide-blast-radius writes.** Overwrite of 1:1 predicates, entity deletes
   with many downstream edges, or writes to shared org namespaces without
   corroboration quorum.
6. **Provider-side risk.** Prompt or response caches that mix tenants; model
   provider outages that cause the system to fail open.

## Design commitments

- Fail closed to HITL or quarantine; degradation never widens the auto-write path.
- Raw input never reaches an LLM before injection detection and PII tokenization.
- Every durable write has a verbatim source span, model+version, and reviewer
  signature (where applicable) recorded on a hash-chained audit event.
- Cross-tenant isolation enforced at the DB layer (RLS) *and* the retrieval
  layer, not just the API.

## Scope

In scope: the code in this repository — core engine, SDKs, services, MCP server,
and dashboard.

Out of scope: user-installed model providers, third-party MCP clients, or
infrastructure the operator configures outside these components.
