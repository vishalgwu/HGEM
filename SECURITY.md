# Security Policy

> **Status: pre-implementation.** GuardMem AI has no deployed service and no
> released package. The controls below describe the design that
> `docs/RULES.md` §4 and `docs/PRD.md` §6.3 require, and are commitments for the
> build — not a description of shipped, audited behavior. Do not treat this file
> as evidence of a hardened system.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report privately through GitHub's advisory flow:
[Security → Report a vulnerability](https://github.com/vishalgwu/HGEM/security/advisories/new)

Please include: what you found, how to reproduce it, the impact you believe it
has, and any log or trace ids. If a reproduction involves a memory-poisoning or
prompt-injection payload, include the exact payload — the attack corpus is how
the regression test gets written.

Expect an acknowledgement within a few days. Because this is a pre-alpha project
run by one builder, there is no formal SLA yet; a real disclosure timeline goes
here when there is a released artifact to disclose against.

## Scope

In scope once code exists: the memory pipeline, the gateway, the MCP server, the
policy engine, the audit chain, and tenant isolation.

Out of scope: the design documents themselves, and anything requiring physical
access or a compromised developer machine.

## Threat model summary

`docs/RULES.md` §4 names **two primary threats**. Both are specific to governed
memory, and both are the reason this project exists rather than incidental risks
bolted on afterward.

### 1. Memory poisoning

An attacker gets an attacker-chosen fact into durable memory, where it survives
every session reset and silently corrupts downstream decisions. This is the
failure mode `docs/PRD.md` calls P4 — *durable* prompt injection.

Designed defenses:

- **Source trust tiers**, ordered `TRUSTED_SYSTEM > VERIFIED_USER >
  UNVERIFIED_USER > TOOL_OUTPUT > RETRIEVED_WEB`. Trust tier caps the maximum
  auto-writable impact level: retrieved web content can **never** auto-write a
  HIGH-impact predicate, regardless of confidence.
- **Corroboration quorum** — predicates marked `requires_corroboration` need two
  independent sources before an auto-write.
- **Per-source write rate limits.**
- **Bitemporal history**, so a poisoned write can be traced to patient zero and
  every downstream assertion that read it. Nothing is hard-deleted, which is
  what makes the forensic walk possible at all.

Response procedure: `docs/runbooks/incident-memory-poisoning.md`.

### 2. Indirect prompt injection

Instructions smuggled inside retrieved documents, tool outputs, or user messages
that try to steer extraction.

Designed defenses:

- Injection detection runs **pre-flight, on raw text, before any model sees it**.
- Untrusted content is delimited and spotlighted, with the system prompt stating
  that content inside the delimiters is data, never instructions.
- **Canary tokens** are inserted and checked on output. A canary appearing in the
  model's output is a confirmed injection, not a heuristic.
- Detections quarantine the whole proposal into a `quarantine:<tenant>`
  namespace — retrievable by admins, flagged, never promoted without review —
  and raise an alert.
- Target: **0% attack success rate into the primary namespace** on the redteam
  corpus. Quarantine is an acceptable outcome; a primary-namespace write is not.

## Other required controls

These are gates in `docs/RULES.md`, not aspirations — each is meant to be
enforced by a linter, a test, or a CI job rather than by convention.

| Control | Requirement |
|---|---|
| Tenant isolation | Defense in depth: Postgres RLS **and** namespace prefixing **and** an app-layer check. A test suite attempts cross-tenant reads through REST, MCP and SDK, and must fail all three. |
| PII | Detected and tokenized before storage. The vault is separately encrypted with its own DB role. Invariant I7: tokenized PII never appears in a span attribute or log line. |
| Secrets | Secret Manager / Vault only. Zero secrets in env files in prod. `gitleaks` + `detect-secrets` in pre-commit and CI. |
| Injection into queries | SQL and Cypher are parameterized. String-built queries fail `semgrep`. |
| Destructive writes | `DELETE` on `assertion` is revoked at the database role level. Retirement is supersession + tombstone. Hard deletion happens only via the GDPR crypto-shred path. |
| Fail closed | Every `except` in the decision path resolves to `HITL_REVIEW` or `REJECT`. `except: pass` around a guardrail fails CI. |
| Audit integrity | Append-only hash-chained log; each event embeds the prior digest. Tampering is detectable at the exact break point. |
| Step-up auth | Reviewer approval of a HIGH-impact assertion requires re-authentication. |
| Dependencies | `pip-audit` and `npm audit` in CI, Dependabot weekly, images scanned with `trivy`, releases signed with `cosign` and shipped with an SBOM. |

## Current dependency posture

`requirements.lock.txt` pins all 311 Python packages. As of 2026-09-10,
`pip-audit` reports **no known vulnerabilities** across that set. Re-run it
before any release:

```bash
pip-audit
```

## Compliance roadmap

SOC 2 Type I evidence collection starts from day one — the audit log is the
primary artifact. HIPAA BAA readiness and GDPR Art. 17 erasure via
crypto-shredding of vault tokens are designed for but not yet implemented. The
SOC 2 *audit engagement itself* is explicitly deferred post-alpha
(`docs/PHASES_AND_ROADMAP.md` §6).
