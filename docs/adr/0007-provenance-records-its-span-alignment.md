# ADR-0007: `Provenance` records its span alignment, and `verbatim` is the source

**Status:** Accepted
**Date:** 2026-09-12
**Amends:** `MEMORY_ENGINE.md` §0 (`Provenance`)

## Context

S2.3 adds the fuzzy half of §1.3's span linker — exact match first, then
`rapidfuzz.partial_ratio_alignment` at score ≥ 92. Building it surfaced a
question the exact-only version never had to answer: **when the model's quote
and the source text differ, which one is `Provenance.verbatim`?**

Measured against a real source, the divergence is not hypothetical:

| model's claimed verbatim | score | span the aligner returns |
|---|---|---|
| `allergic to penicilin` (typo) | 95.24 | `allergic to penicilli` |
| `allergic to penicillin.` | 95.65 | `allergic to penicillin ` |
| `allergic  to  penicillin` | 91.67 | *rejected, below 92* |

Two problems in that table. The claim and the span text are different strings,
and the span the aligner returns is not a clean quote — it truncates
`penicillin` mid-word and includes stray whitespace.

§3.2 then requires something neither answer supplies on its own: `S_src` is
"entailment of the candidate by its own verbatim span, × source-tier
multiplier; **fuzzy-match penalty applied if span alignment < 1.0**". S5.2 needs
the alignment. Nothing on `Provenance` carries it, and it cannot be recovered
later: if `verbatim` is the source slice the two are identical by construction
and the ratio is always 1.0, and if `verbatim` is the model's claim then
recomputing needs `source[span]`, which means a blob fetch inside the scorer.

## Decision

**`verbatim` is the source text, always.** The span is snapped to whole words
and stripped of surrounding whitespace, and `verbatim` is the resulting slice.
`Provenance.verbatim`'s own contract already required this — "This is what the
reviewer reads and what NLI compares against, so it is the text itself and never
a paraphrase" — and `DESIGN_SYSTEM.md` §3.2 renders the reviewer's highlight
from `source_span` rather than re-deriving it, so a `verbatim` that disagreed
with its span would show a reviewer one thing and highlight another. §2.2's NLI
would also be comparing a paraphrase against an incumbent.

**`Provenance` gains `alignment: float = 1.0`**, the normalised similarity
between what the model claimed and the text actually stored:

```python
class Provenance(_M):
    source_hash: str
    source_span: tuple[int, int]
    source_tier: SourceTier
    verbatim: str = Field(max_length=2000)   # the SOURCE text, never the claim
    alignment: float = Field(default=1.0, ge=0.0, le=1.0)   # NEW
    captured_at: datetime
```

It defaults to 1.0, which is what an exact match is and what every provenance
constructed before this ADR meant. Below 1.0 it says the model paraphrased its
own citation — a grounding signal in its own right, and the input §3.2's penalty
needs.

The threshold stays at **92** on rapidfuzz's 0–100 scale, as §1.3 specifies, and
is stored normalised to [0, 1] like every other score in the system.

## Alternatives rejected

- **Store the model's claim as `verbatim`.** Contradicts the field's own
  docstring, feeds a paraphrase to NLI, and desynchronises the reviewer's quote
  from the reviewer's highlight.
- **Recompute the alignment at S5.2.** Needs `source[span]`, so it couples the
  confidence scorer to blob storage to recover a number that was in hand at
  extraction time.
- **Use the aligner's span as returned.** It truncates words. `allergic to
  penicilli` is not a quote a reviewer can act on, and §1.1's "no unsourced
  write" is not honoured in spirit by a span pointing at half a word.
- **Add a minimum verbatim length to guard short fuzzy matches.** Measured and
  found unnecessary: at 92, a single wrong character in a three-character needle
  scores 67, and `hivez` against a source containing `hives` scores 80. The
  threshold is self-limiting on short strings, and an unspecified extra rule
  would have been a guess dressed as caution.

## Consequences

- `MEMORY_ENGINE.md` §0 and §1.3 are updated in the same commit; this ADR is the
  record of why, per `RULES.md` §8.
- Every `MemoryCandidate` now satisfies `source[source_span] == verbatim` by
  construction, which is a stronger and cheaper form of invariant I1 than
  anything that could be asserted before.
- S5.2 can implement §3.2's fuzzy-match penalty from a stored number.
- `alignment` is a candidate feature for `threshold_tuner.py` (§3.3 refits from
  reviewer labels): "the model paraphrased its citation" is plausibly
  predictive, and it is now recorded rather than discarded.
