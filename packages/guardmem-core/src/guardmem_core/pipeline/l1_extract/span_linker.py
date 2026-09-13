"""Locate a claim's supporting text in the source.  S2.3

`MEMORY_ENGINE.md` §1.3, and the single most load-bearing rule in Layer 1:

    Every candidate must resolve to a verbatim span. [...] **No span →
    REJECT(reason=UNSOURCED).** This single rule kills most confabulated facts
    before any scoring happens.

It works because it is not a judgement. A model that invents a fact must also
invent the sentence it came from, and an invented sentence is not in the source.
No confidence score is involved and none can be argued with.

**Exact first, then fuzzy at ≥ 92.** The exact half landed at S2.2, because
`extract` cannot build a `MemoryCandidate` without a `Provenance` and a
`Provenance` has no valid state without a span. This step adds the fallback the
spec calls for, and two things it does not:

1. **The aligner's raw span is not a quote.** Measured against a real source,
   the claim `allergic to penicilin` scores 95.24 and returns
   `allergic to penicilli` - a truncated word - while `allergic to penicillin.`
   returns a span with a trailing space. A reviewer's highlight is rendered from
   `source_span` (`DESIGN_SYSTEM.md` §3.2) and half a word is not something a
   human can act on, so the span is snapped to whole words and stripped of
   surrounding whitespace before it is returned.
2. **`alignment` is measured and returned**, because §3.2 applies a
   "fuzzy-match penalty [...] if span alignment < 1.0" and nothing else in the
   pipeline is in a position to compute it. ADR-0007 records why it is stored
   rather than recovered later.

**Why there is no minimum-length guard**, which was the obvious next worry: at
92 the threshold is self-limiting on short strings. Measured - one wrong
character in a three-character needle scores 67 (`PCQ` against a source
containing `PCP`), and `hivez` against `hives` scores 80. Adding a length rule
the spec does not call for would have been a guess dressed as caution.

What the fuzzy fallback still cannot do is notice that a claim *inverts* its
source. `not allergic to penicillin` scores 88.46 against a source that says the
opposite - under the bar, but not by much, and on a longer sentence it would
pass. That is not this module's job: §2.2's NLI is what compares meaning, and
the span rule only ever claims that the text is there.
"""

from __future__ import annotations

from typing import Final

from rapidfuzz import fuzz

from guardmem_core.schemas.base import GMModel

__all__ = ["SpanMatch", "link_span"]

# `MEMORY_ENGINE.md` §1.3, on rapidfuzz's 0-100 scale. Checkpoint B's ordered
# diagnosis names this number: if `S_src` alone has no discriminative power,
# "the span linker is matching too loosely; tighten the fuzzy threshold above
# 92". Change it there, with the AUROC that justified it, not here.
_FUZZY_THRESHOLD: Final = 92.0

# How far the snap may walk to find a word boundary. A span that starts mid-word
# is being repaired by a few characters; if there is no whitespace within this
# distance the text is not word-shaped and the raw edge is kept rather than
# swallowing an arbitrary amount of the document.
_MAX_SNAP: Final = 40


class SpanMatch(GMModel):
    """Where a claim was found, and how closely it was quoted.

    Attributes:
        span: Half-open character offsets `[start, end)` into the source,
            matching the `INT4RANGE` column in `ARCHITECTURE.md` §5 and the
            validator on `Provenance`.
        text: The source text at that span. This is what becomes
            `Provenance.verbatim` - the *source*, never the model's claim, per
            ADR-0007 - so a reviewer's quote and a reviewer's highlight cannot
            disagree.
        alignment: How close the claim was to `text`, normalised to [0, 1].
            Exactly 1.0 on an exact match. Below that it says the model
            paraphrased its own citation, which is the input §3.2's fuzzy-match
            penalty needs.
    """

    span: tuple[int, int]
    text: str
    alignment: float


def _snap(source: str, start: int, end: int) -> tuple[int, int]:
    """Trim a raw alignment to whole words.

    The aligner optimises a similarity score, not readability, so its edges land
    wherever the arithmetic put them. Widening to a word boundary is safe in the
    direction that matters: the result still contains the matched region, so it
    cannot turn a true citation into a false one - it can only quote more of the
    sentence the claim came from.

    Total, with no "matched only whitespace" case, and that is an argument
    rather than an oversight. Reaching one would need the aligner to return an
    all-whitespace window at score >= 92, and the score *is* the similarity
    between the needle and that window - a non-blank needle scores 0 against a
    blank window. `link_span` refuses a blank needle before calling this, so the
    two conditions cannot hold together. A guard here would have been
    unreachable code that reads as protection, and the invariant it would have
    defended is asserted over 500 generated examples in
    `tests/property/test_i1_sourced_writes.py` instead.

    Args:
        source: The document.
        start: Raw start offset.
        end: Raw end offset, exclusive.

    Returns:
        The snapped span.
    """
    start, end = max(start, 0), min(end, len(source))
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1

    limit = start
    while limit > 0 and start - limit < _MAX_SNAP and not source[limit - 1].isspace():
        limit -= 1
    start = limit if start - limit < _MAX_SNAP else start

    limit = end
    while limit < len(source) and limit - end < _MAX_SNAP and not source[limit].isspace():
        limit += 1
    end = limit if limit - end < _MAX_SNAP else end

    return (start, end)


def link_span(verbatim: str, source: str) -> SpanMatch | None:
    """Locate `verbatim` in `source` and return the span that supports it.

    Args:
        verbatim: The supporting substring the extractor claimed. The exact pass
            compares it literally - not normalised, not case-folded - and the
            fuzzy pass then allows what §1.3 allows and no more.
        source: The document the offsets are into. Must be the same string whose
            sha256 becomes `Provenance.source_hash`, or the span and the hash
            describe different documents.

    Returns:
        A `SpanMatch`, or `None` when the text is not there - which the caller
        must treat as `REJECT(reason=UNSOURCED)`. There is no third outcome, and
        that is the whole value of the rule.

        On an exact match the **first** occurrence wins. Any occurrence is a
        true citation, and preferring the first keeps the function
        deterministic, which replay depends on.

    An empty `verbatim` returns `None` rather than `(0, 0)`. `"" in source` is
    trivially true, so a naive search would hand back a zero-width span that
    satisfies a `NOT NULL` constraint while quoting nothing - which `Provenance`
    already refuses, and which `RULES.md` §1.1 treats as no span at all.
    Catching it here means the caller counts it as unsourced instead of raising.
    """
    # `verbatim.strip()`, not `verbatim`. A claim of `" "` is a substring of
    # almost any source, so the exact pass below would find it and return a span
    # quoting a single space - non-empty, so `Provenance` accepts it, and a
    # citation of nothing, which `RULES.md` §1.1 treats exactly as no span at
    # all. The fuzzy path already refused it in `_snap`; without this the two
    # halves of the same function disagreed about the same input. Found by the
    # I1 property test, not by a hand-written case.
    if not verbatim.strip() or not source:
        return None

    start = source.find(verbatim)
    if start >= 0:
        return SpanMatch(span=(start, start + len(verbatim)), text=verbatim, alignment=1.0)

    aligned = fuzz.partial_ratio_alignment(verbatim, source)
    if aligned is None or aligned.score < _FUZZY_THRESHOLD:
        return None

    snapped = _snap(source, aligned.dest_start, aligned.dest_end)
    text = source[snapped[0] : snapped[1]]
    # Measured against what is actually stored, not against the aligner's raw
    # window: `alignment` has to describe the distance between the claim and the
    # text a reviewer will see, and the snap moved that text.
    return SpanMatch(span=snapped, text=text, alignment=fuzz.ratio(verbatim, text) / 100.0)
