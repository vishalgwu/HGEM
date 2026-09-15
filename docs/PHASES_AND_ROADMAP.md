# PHASES_AND_ROADMAP.md — 4-Week Execution Plan

**Assumption:** one focused builder (plus Claude Code), ~6 productive hours/day, 28 days to a
demoable alpha with real evals. Scope is deliberately ruthless — the plan below is what gets built,
and §6 is the list of things that are explicitly *not* getting built.

**The single ordering principle:** the decision engine ships before the gateway, the gateway before
the UI, and the evals before the demo. Anything that can't be measured by day 28 doesn't go in.

**What this document owns:** phase goals, the exit gate for each phase, deferred scope, and the
weekly risk review. **What it does not own:** the step-by-step work. Every step, command, file path
and per-step "done when" lives in `BUILD_NOTEBOOK.md`, and the one-line-per-day index is its
**Appendix A**. Those used to be restated here; they are not, so a plan change has exactly one
place to be made. (A 2026-09-14 correction rewrote this line to say the notebook "has no
appendices" and repointed it at the Day sections. **That correction was wrong and is reverted
2026-09-15.** `BUILD_NOTEBOOK.md` PART 6 carries appendices A-G, present since the scaffold commit,
and Appendix A is exactly the one-line-per-day index the original pointer meant.)

---

## 1. Phase 1 — Core Engine & MCP (Days 1–7)

> **Goal:** `guardmem_core.pipeline.run()` takes raw text and returns an audited decision, and an
> MCP client can drive it end-to-end from Claude Desktop.

**Steps:** `BUILD_NOTEBOOK.md` Days 1–7 (S1.1 – S7.4). Ends at **Checkpoint B**, the discrimination
gate in the notebook's Day 5 section — the single most important gate in the project.

Ships: the Pydantic schema layer and error hierarchy; L1 extraction with span linking; the
bitemporal store with outbox-coordinated dual writes; L2 validation and conflict detection; L3
scoring, the decision matrix, and the hash-chained audit log; the MCP server and its four core
tools.

**Exit gate**
- [ ] End-to-end from Claude Desktop: propose → decide → write → `memory.search` returns it with provenance
- [ ] A contradictory second fact supersedes the first, with tombstone and audit event
- [ ] `replay_trace.py` reproduces any decision deterministically
- [ ] Zero unsourced writes possible (property test + DB constraint both enforce it)
- [ ] Checkpoint B signed off: AUROC of `C` against 200 human-labelled candidates ≥ 0.80 (0.75–0.80 proceeds as MARGINAL and is recorded)
- [ ] `guardmem-core` coverage ≥ 90% (RULES §5 release gate; 85% is the Week-1 interim floor)
- [ ] `make lint typecheck test` green

**Risk:** NLI quality on typed/short objects. Mitigation: hybrid — cross-encoder for prose,
deterministic comparators for coded/numeric values, LLM judge only in the ambiguous band.

---

## 2. Phase 2 — Gateway, Routing & Guardrails (Days 8–14)

> **Goal:** production ingress with model routing, fallback, security armor, and budget control.

**Steps:** `BUILD_NOTEBOOK.md` Days 8–14 (S8.1 – S14.3).

Ships: the FastAPI gateway with auth, tenancy and RLS context; the async worker path; the
FAST/BALANCED/FRONTIER router with circuit breakers and caching; the per-tenant cost ledger;
injection detection, the PII vault and poisoning defense; the policy engine; OpenTelemetry,
Prometheus and the telemetry API; compaction and the context packer.

**Exit gate**
- [ ] Latency SLAs in `PRD.md` §6.1 met under 50 rps synthetic load
- [ ] Every failure mode in `ARCHITECTURE.md` §4 verified by a chaos test
- [ ] Injection attack success rate into the primary namespace: 0%
- [ ] Blended cost per governed candidate measured and under the $0.0009 envelope
- [ ] One trace visible in Langfuse, Phoenix and Prometheus with a consistent `trace_id`

---

## 3. Phase 3 — Dashboard & HITL Queue (Days 15–21)

> **Goal:** an engineer can tune the system and a nurse can clear a queue, both without reading docs.

**Steps:** `BUILD_NOTEBOOK.md` Days 15–21 (S15.1 – S21.3).

Ships: the Next.js shell and design tokens behind a BFF; the control panel — metric tiles, the
extraction funnel with drop sampling, decision mix, latency and routing panels, the live trace
stream and trace detail; the HITL queue backend with leases, SLA timers and escalation; the review
task UI, keyboard flow and typed edit flow; the threshold editor and `threshold_tuner.py`.

**Exit gate**
- [ ] Median review time within the `PRD.md` §6.2 target (≤ 25 s) over a timed run of 20 seeded tasks
- [ ] Reviewer decisions visibly close the loop — κ reported, tuner consuming labels, promotion blocked below κ 0.6
- [ ] The control panel answers "why was this flagged?" in ≤ 2 clicks from any trace
- [ ] Funnel counts reconcile exactly with audit-log counts
- [ ] axe clean (WCAG 2.2 AA), Playwright e2e green in CI

---

## 4. Phase 4 — Evals, Benchmarks & Deployment (Days 22–28)

> **Goal:** numbers instead of claims, and a URL instead of a localhost demo.

**Steps:** `BUILD_NOTEBOOK.md` Days 22–28 (S22.1 – S28.4).

Ships: the eval harness and datasets; RAG quality, memory integrity, security and Drift@N suites;
the baseline comparison against raw-RAG, mem0 and Zep; load and cost benchmarks; Terraform and the
signed release pipeline; the nightly regression gate; runbooks walked through against staging; the
README, demo video and public eval report.

**Exit gate**
- [ ] Deployed, reachable, load-tested, with published **measured** SLAs replacing the targets in `PRD.md` §6.1
- [ ] Eval report with baselines and a stated limitations section
- [ ] Nightly regression gate live and proven — a deliberate scoring regression blocks the merge
- [ ] Demo runs cold from a clean clone: `git clone && docker compose up && make seed && make eval`

---

## 5. Milestone Chart

```
Week 1 ████████ Core Engine + MCP        → "it decides, and I can prove why"
Week 2 ████████ Gateway + Guardrails     → "it's fast, safe, and cheap"
Week 3 ████████ Dashboard + HITL         → "a human can actually use it"
Week 4 ████████ Evals + Deploy           → "here are the numbers, here's the URL"

Demo-ready checkpoints:
  Day  5  ▸ Checkpoint B: the scoring separates good writes from bad  (viability proof)
  Day  7  ▸ Claude Desktop writes a governed fact                     (technical proof)
  Day 14  ▸ injection attempt quarantined, live on a dashboard        (security proof)
  Day 21  ▸ nurse clears a real queue inside the review-time target   (product proof)
  Day 28  ▸ drift curve: with vs without GuardMem over 100 turns      (value proof)
```

---

## 6. Explicitly Deferred (post-alpha)

Multi-agent shared-memory arbitration · fine-tuned in-house extraction model · air-gapped installer ·
mobile reviewer app · automatic ontology induction from corpora · federated/cross-tenant learning ·
SOC 2 audit engagement (evidence collection starts now; the audit doesn't) · marketplace of
community policy packs · streaming/incremental extraction mid-turn.

This list is the roadmap-level view of `PRD.md` §7 "Out (v1)"; the PRD owns product scope, and any
change belongs there first.

Each of these is a real feature. None of them changes whether the core claim — *governed memory
measurably reduces drift* — is true, which is the only thing week 4 needs to establish.

**If you fall behind,** the cut order is `BUILD_NOTEBOOK.md` **Appendix G**, which owns it the same
way the notebook owns every other piece of step-level execution. It names five cuts in order and
five things that are never cut. It is not restated here: a cut order in two documents is a cut
order that will be argued about at the exact moment there is no time to argue.

(This paragraph said until 2026-09-15 that the cut order "is not yet written" and that Appendix G
"does not exist". Both claims were false — Appendix G has been in the notebook since the scaffold
commit — and the effect was that the one document supposed to say what to sacrifice appeared to say
nothing. Corrected after reading the notebook rather than the pointer.)

---

## 7. Weekly Risk Review

| Week | Top risk | Early warning | Response |
|---|---|---|---|
| 1 | Scoring math looks principled but doesn't separate good from bad writes | AUROC of `C` against hand-labelled candidates < 0.75 on a 200-item dev set | Drop to a simpler ensemble; entropy alone is a strong baseline — ship that and iterate. This is Checkpoint B, and a failure here is the most valuable information in the month |
| 2 | Latency budget blown by NLI + K-sampling | p95 eval > 2.5 s | Batch NLI, quantize the cross-encoder, cut K on LOW risk, move more work off the strict path |
| 3 | HITL volume too high to be usable | > 25% of candidates routed to review on the demo tenant | Tune τ/ρ per namespace with the tuner; tighten the noise filter; raise the corroboration bar only where impact demands it |
| 4 | Evals show no drift improvement vs baseline | Drift@100 curves overlap | This is the falsification point — publish it honestly, and dig into whether the failure is retrieval (GC not aggressive enough) or write-path (thresholds too loose) |
