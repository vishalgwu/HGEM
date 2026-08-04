# ADR-0004: Semantic entropy as confidence primitive

**Status:** Accepted
**Date:** 2026-01-15

## Context

Token-level log-probs are a poor proxy for factual confidence: paraphrases of
the same fact score identically to genuinely uncertain outputs. We need a
signal that rewards *semantic* agreement across samples, not lexical.

## Decision

The confidence composite `C` uses semantic entropy over K structured-output
samples of the extractor as its primary term. Semantic entropy clusters
outputs by entailment (both directions) and measures Shannon entropy over
clusters, not tokens.

`C` also folds in retrieval agreement, provenance trust tier, and adjudicator
margin — but semantic entropy is the load-bearing signal.

## Consequences

- Every extraction is K samples (default K=5 BALANCED, K=1 FAST). Budget
  routing matters — this ADR forces the LLM router (ADR pending) to be real.
- The confidence score is provider-agnostic; we don't depend on any single
  model exposing log-probs.
- Threshold tuning (`scripts/threshold_tuner.py`) fits τ (auto-write) and ρ
  (reject) from labelled reviewer decisions, not from vibes.
