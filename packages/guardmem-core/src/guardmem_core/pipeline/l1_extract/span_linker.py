"""Locate a claim's supporting text in the source.  S2.2 / S2.3

`MEMORY_ENGINE.md` §1.3, and the single most load-bearing rule in Layer 1:

    Every candidate must resolve to a verbatim span. [...] **No span →
    REJECT(reason=UNSOURCED).** This single rule kills most confabulated facts
    before any scoring happens.

It works because it is not a judgement. A model that invents a fact must also
invent the sentence it came from, and an invented sentence is not in the source.
No confidence score is involved and none can be argued with.

**This module lands at S2.2 holding only the exact-match half.** The notebook
puts it at S2.3, but `extract()` cannot construct a `MemoryCandidate` without a
`Provenance`, and `Provenance` has no valid state without a span - so the
function has to exist a step before the one that names it. What S2.3 adds is the
fuzzy fallback (rapidfuzz `partial_ratio_alignment`, score ≥ 92) and the
property test that invariant I1 rests on. The signature does not change.

Until then this under-matches, which is the safe direction: a fact whose
verbatim is a near-miss is rejected as unsourced rather than attached to an
approximate span. The cost is recall, it is counted in
`ExtractionResult.dropped_unsourced`, and S2.3 is where it gets paid back.
"""

from __future__ import annotations

__all__ = ["link_span"]


def link_span(verbatim: str, source: str) -> tuple[int, int] | None:
    """Locate `verbatim` in `source` and return its half-open character span.

    Args:
        verbatim: The supporting substring the extractor claimed. Compared
            literally - not normalised, not case-folded, not whitespace-
            collapsed. That strictness is deliberate: the span is what the
            review UI highlights and what NLI compares against
            (`DESIGN_SYSTEM.md` §3.2 renders the highlight from `source_span`
            and never re-derives it client-side), so an offset that points at
            *nearly* the quoted text would silently mislead a human reviewer.
        source: The document the offsets are into. Must be the same string
            whose sha256 becomes `Provenance.source_hash`, or the span and the
            hash describe different documents.

    Returns:
        `(start, end)` as half-open offsets, matching the `INT4RANGE` column in
        `ARCHITECTURE.md` §5 and the validator on `Provenance`. `None` when the
        text is not present, which the caller must treat as
        `REJECT(reason=UNSOURCED)` - there is no third outcome.

        The **first** occurrence is returned when the text appears more than
        once. Any occurrence is a true citation, and preferring the first keeps
        the function deterministic, which replay depends on.

    An empty `verbatim` returns `None` rather than `(0, 0)`. `"" in source` is
    trivially true, so a naive search would hand back a zero-width span that
    satisfies a `NOT NULL` constraint while quoting nothing - which `Provenance`
    already refuses, and which `RULES.md` §1.1 treats the same as no span at all.
    Catching it here means the caller counts it as unsourced instead of raising.
    """
    if not verbatim:
        return None
    start = source.find(verbatim)
    if start < 0:
        return None
    return (start, start + len(verbatim))
