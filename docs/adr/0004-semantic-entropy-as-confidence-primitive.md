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

`C` also folds in source grounding (`S_src`), schema fit (`S_sch`), corroboration
(`S_cor`), and consistency with existing memory (`S_con`) — but semantic entropy
carries the largest weight (`w_H = 0.35`) and is the load-bearing signal. The
exact composite and its v1 weights are in `MEMORY_ENGINE.md` §3.2.

## Consequences

- Every extraction is K samples, with K set by risk hint: 1 (FAST) on LOW,
  3 (FAST) by default, 5 (BALANCED) on HIGH — see `MEMORY_ENGINE.md` §1.2.
  Budget routing matters, so this ADR forces the LLM router
  (`ARCHITECTURE.md` §2.8) to be real.
- The confidence score is provider-agnostic; we don't depend on any single
  model exposing log-probs.
- Threshold tuning (`scripts/threshold_tuner.py`) fits the confidence thresholds
  τ (`τ_lo`, `τ_mid`, `τ_hi`) and the impact-risk thresholds ρ (`ρ_lo`, `ρ_hi`)
  from labelled reviewer decisions, not from vibes. τ gates on confidence and ρ
  on blast radius; neither is "the reject threshold" on its own, because the
  decision comes from the (C, R) cell, not from either axis alone.
