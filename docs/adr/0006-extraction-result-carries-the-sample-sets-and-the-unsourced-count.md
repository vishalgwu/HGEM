# ADR-0006: `ExtractionResult` carries the sample sets and the unsourced count

**Status:** Accepted
**Date:** 2026-09-12
**Amends:** `MEMORY_ENGINE.md` §0 (`ExtractionResult`)

## Context

`MEMORY_ENGINE.md` §0 declares `ExtractionResult` with six fields: `candidates`,
`k_samples`, `dropped_noise`, `tokens_in`, `tokens_out`, `cache_hit`. Building
S2.2 against it surfaced two things the object is required to carry and cannot.

**1. The K samples themselves have nowhere to live.** §3.1 computes semantic
entropy by clustering, "for each pair `(s_i, s_j)` in the K samples for a given
`(subject, predicate)`", and then drops "a candidate that appears in **zero**
clusters containing sample 0's meaning" as a minority hallucination. Both
operations need the per-sample extractions and their draw order. `k_samples: int`
records only *how many* were drawn. S5.1 is the step that clusters them, and
there is no route from Layer 1 to Layer 3 that carries what it clusters — the
`ExtractionResult` is the only object that crosses that boundary. Re-extracting
at scoring time would cost K more model calls and would not reproduce the same
samples, which makes replay dishonest.

**2. The unsourced drop count is unobservable.** §1.3 is the anti-confabulation
rule — "No span → `REJECT(reason=UNSOURCED)`. This single rule kills most
confabulated facts before any scoring happens" — and `RULES.md` §1.1 makes an
unsourced write a P0 bug. A `MemoryCandidate` cannot be constructed without
`Provenance`, so an unsourced fact is necessarily dropped inside `extract()`.
Nothing then records that it happened.

That is the same failure §1.1 forbids one layer up, in the same words: noise
drops are counted into `dropped_noise` because "you must be able to see what the
filter is eating". Extraction eats facts too, for a stricter reason, and the
funnel `DESIGN_SYSTEM.md` §2.1 draws has an `ingested → denoised → extracted`
step whose height is exactly this number. A rule whose activation count cannot be
seen cannot be tuned, and — worse — a span linker that silently stopped matching
would look like a quiet drop in recall rather than a bug.

## Decision

Add two fields to `ExtractionResult`, and the model one of them needs:

```python
class ExtractedFact(_M):
    subject: str
    predicate: str
    object: str | float | bool | dict
    verbatim: str = Field(max_length=2000)

class ExtractionResult(_M):
    candidates: list[MemoryCandidate]
    samples: list[list[ExtractedFact]]   # NEW - draw order, sample 0 canonical
    k_samples: int
    dropped_noise: int
    dropped_unsourced: int               # NEW
    tokens_in: int
    tokens_out: int
    cache_hit: bool
```

`samples` is the raw per-sample output, **not** grouped by `(subject,
predicate)`. The grouping is S5.1's, and doing it here would freeze one reading
of §3.1 into Layer 1 before the clustering code exists.

`ExtractedFact` lives in `schemas/candidate.py` rather than in the extractor,
because `ExtractionResult` carries it and `guardmem_core.schemas` may not import
from `guardmem_core.pipeline`. It is deliberately narrower than
`MemoryCandidate`: it holds only what a model is allowed to assert. Everything
else on a candidate — tenant, namespace, trace, provenance, the pinned model id,
the prompt version — is known by the caller, and a model must never be in a
position to supply it.

## Alternatives rejected

- **Return the samples separately from `extract()`.** Two return values, and the
  pairing between a candidate and the samples it came from becomes a convention
  rather than a type.
- **Recompute entropy inputs at S5.1.** K more calls per candidate, different
  samples each run, and `replay_trace.py` stops reproducing decisions.
- **Leave the unsourced count to logging.** Structured logging arrives at S13.1,
  telemetry is explicitly sampled and lossy (`ARCHITECTURE.md` §2.5), and the
  funnel reconciliation in the Phase 3 exit gate compares counts, not log lines.
- **Derive the unsourced count as `len(samples[0]) - len(candidates)`.** True
  today and quietly false the moment anything else drops a fact — a schema-gate
  rejection at S4.1, or a dedupe merge. A count should be counted.

## Consequences

- `MEMORY_ENGINE.md` §0 is updated in the same commit; this ADR is the record of
  why, per `RULES.md` §8.
- `ExtractionResult` grows, and it is a transient pipeline object rather than a
  persisted one, so nothing about the data model changes. In particular
  `samples` never reaches the audit payload or a store row.
- S5.1 can be written against a real input rather than against a placeholder.
- The funnel gets a truthful `extracted` stage at S16.2 instead of a derived one.
