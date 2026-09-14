"""The resolution matrix, and the merge it calls for.  BUILD_NOTEBOOK.md S4.4

`MEMORY_ENGINE.md` §2.3 is a six-row table over one `(incumbent, candidate)`
pair, and §2.4 is the one sentence that says what a DUPLICATE actually does:
"Merging increments `corroboration_count`, appends the new `Provenance`, and
recomputes confidence with the corroboration term." Both live here. S4.3's
`conflict.py` gathers the evidence - cosine from retrieval, three numbers from
the judge - and this reads it.

**Why the split.** `conflict.py` decides *whether to ask a model*; this decides
*what the answer means*. §2.2's (b) and (c) are arithmetic that settles the
question before any judge is paid for, so they stay on the near side of that
call and never reach `classify`. Everything downstream of the judge is a table
lookup with no I/O, no clock and no ordering - which is what makes it testable
the way `decide()` will be at S5.4.

**Two places this departs from the table as printed, both recorded because they
are deliberate.**

1. **Contradiction is read before the similarity rows, not after.** The table
   lists DUPLICATE first, with `—` in the `contra` column, so a literal
   top-to-bottom match would merge a pair the judge scored at 0.9
   contradiction - and a merge is not a neutral act. It bumps
   `corroboration_count`, which feeds §3.2's `S_cor`, which raises `C`. So the
   printed order lets an incoherent or manipulated judgement *raise confidence
   in a fact by feeding it its own negation*. Reading contradiction first costs
   nothing when the numbers are coherent (mutual entailment and mutual
   exclusion do not co-occur in a sane judge) and closes that path when they are
   not. `ARCHITECTURE.md` §0: degradation never widens the auto-write path.

2. **The table is not total, and the gap resolves to `coexist`.** A pair at
   cosine 0.88 with both entailments at 0.9 and no contradiction matches no row:
   row 1 needs 0.95, row 2 needs `fwd < 0.85`, row 5 needs `cosine < 0.80`, row
   6 needs contradiction above 0.3. `classify` is total by construction and
   answers `coexist` there - the same answer row 5 gives, and the safe one,
   because two facts that do not contradict can both be true. The cost is a
   near-duplicate row rather than a merge; the alternative reading would retire
   or absorb a fact on similarity alone.

3. **Equality satisfies the DUPLICATE row without the thresholds.** §2.3
   identifies a duplicate by cosine and entailment, both of which are proxies
   for "these are the same claim". When the incumbent already holds the exact
   `object` over an intersecting interval, the proxies have nothing left to
   establish - and leaving them to decide means a restatement the embedder
   scores at 0.94 gets written as a second live row saying what the first one
   says. Invariant I2 is what surfaced this; `tests/property/` drives it.
   Note where the check sits: *below* the contradiction rows, because an equal
   object does not mean the claims agree. Polarity lives in the verbatim, not
   in the object.

**What is still missing, and it is one number.** Row 3 resolves a CONTRADICTION
by "supersede if candidate newer *and* `C` >= tau_hi, else escalate". `C` is
Layer 3's composite and does not exist when Layer 2 runs, so the conjunct cannot
be established and the row's own `else` applies. S4.3 recorded this as a
deviation that "S4.4 owns closing"; it closes as a *total function* rather than
a TODO - the rule is implemented in full, and `escalate` is what the rule
itself returns when confidence is unknown. S5.4's `decide()` is where a
confident, newer candidate may be upgraded, with the number in hand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.verdict import ConflictKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.pipeline.l2_validate.nli import Judgement
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.schemas.receipt import Provenance

__all__ = ["Resolution", "classify", "merge", "most_severe"]

# §2.3's five thresholds, and all five live here. `conflict.py` held the last
# two until this step; the table is one rule, and a threshold with two homes is
# one that can disagree with itself. Private, because the way to check one is to
# drive `classify` across it - a test that imported the constant and compared it
# with itself would pass on a wrong value.
_DUPLICATE_COSINE: Final = 0.95
_REFINEMENT_COSINE: Final = 0.80
_ENTAILED: Final = 0.85
_CONTRADICTION_FLOOR: Final = 0.65
_AMBIGUOUS_FLOOR: Final = 0.30


class Resolution(GMModel):
    """One row of §2.3's table: what this pair is, and what to do about it.

    Attributes:
        kind: The classification, which lands on `ConflictReport.kind`.
        resolution_hint: §2.3's action. Named to match the field it fills, and a
            `Literal` for the same reason that field is one - it is what the
            spec of record declares.

    A model rather than a tuple because the pair travels together and is
    compared by value: `most_severe` ranks resolutions by their position in
    `_PRECEDENCE`, which only works if two equal resolutions are equal.
    """

    kind: ConflictKind
    resolution_hint: Literal["merge", "supersede", "coexist", "escalate"]


# The five outcomes `classify` can return, as values rather than constructed at
# each return - see `_PRECEDENCE` below, which is the reason they are named.
DUPLICATE: Final = Resolution(kind=ConflictKind.DUPLICATE, resolution_hint="merge")
REFINEMENT: Final = Resolution(kind=ConflictKind.REFINEMENT, resolution_hint="supersede")
CONTRADICTION: Final = Resolution(kind=ConflictKind.CONTRADICTION, resolution_hint="escalate")
AMBIGUOUS: Final = Resolution(kind=ConflictKind.NONE, resolution_hint="escalate")
COEXIST: Final = Resolution(kind=ConflictKind.NONE, resolution_hint="coexist")

# Cardinality and temporal overlap are settled before the judge is asked, so
# they are `conflict.py`'s to return - but they are rows of this table and rank
# against the rest here, because `most_severe` is where a set of incumbents is
# reduced to one answer.
CARDINALITY: Final = Resolution(kind=ConflictKind.CARDINALITY, resolution_hint="supersede")
TEMPORAL_OVERLAP: Final = Resolution(
    kind=ConflictKind.TEMPORAL_OVERLAP, resolution_hint="supersede"
)

# **This ordering is not in §2.3.** The table is written for a single pair and
# retrieval returns up to ten, so something has to say which incumbent's answer
# wins - and the table's own row order is a *matching* order, not a severity
# one. DUPLICATE is its first row and must not outrank a contradiction found
# against a different incumbent.
#
# The rule stated once, here, so a reviewer can disagree with it in one place:
# anything that stops the pipeline outranks anything that changes memory, and
# among the changes, the one that writes least wins. Escalations first, then the
# deterministic supersessions, then merge (no new row), then supersede (retires
# one), then coexist.
_PRECEDENCE: Final = (
    CONTRADICTION,
    AMBIGUOUS,
    CARDINALITY,
    TEMPORAL_OVERLAP,
    DUPLICATE,
    REFINEMENT,
    COEXIST,
)


def classify(cosine: float, judgement: Judgement, *, restates: bool = False) -> Resolution:
    """Read §2.3's table for one judged pair.

    Args:
        cosine: Similarity to the incumbent, as retrieval reported it. Not
            recomputed: the stored vector came from whatever embedder was
            configured at write time, and a fresh distance would be a different
            measurement wearing the same name.
        judgement: The three numbers §2.2(a) asks for.
        restates: Whether the incumbent already holds this exact value over an
            intersecting interval - `conflict._restates`. Equality is stronger
            evidence of sameness than either proxy this row otherwise uses, so
            it satisfies the DUPLICATE row on its own; it is read *below* the
            contradiction rows, never above them, because an equal `object`
            does not mean the claims agree. The object carries no polarity.
            Defaults false so a caller that has not compared cannot assert
            sameness by omission.

    Returns:
        The matching row. Total over every input: the fall-through is `COEXIST`,
        for the reason in the module docstring.

    Pure, and deliberately so - no I/O, no clock, no ordering. That is what lets
    the 60-pair probe at S4.3 and the I2 property suite here drive it directly
    rather than through a store.
    """
    if judgement.contradiction >= _CONTRADICTION_FLOOR:
        return CONTRADICTION
    if judgement.contradiction >= _AMBIGUOUS_FLOOR:
        # §2.3's last row: "the NLI is unsure, so a human or frontier model
        # decides". `ConflictKind` has no AMBIGUOUS member and the spec does not
        # ask for one - what is ambiguous is the resolution, not the finding.
        return AMBIGUOUS
    if restates:
        return DUPLICATE
    if (
        cosine >= _DUPLICATE_COSINE
        and judgement.entail_fwd >= _ENTAILED
        and judgement.entail_rev >= _ENTAILED
    ):
        return DUPLICATE
    if (
        cosine >= _REFINEMENT_COSINE
        and judgement.entail_rev >= _ENTAILED
        and judgement.entail_fwd < _ENTAILED
    ):
        # The asymmetry *is* the refinement: the candidate entails the incumbent
        # and the incumbent does not entail the candidate, so the candidate is
        # the narrower claim. "Allergic to penicillin and amoxicillin" against
        # "allergic to penicillin". This is the one row that needs both
        # directions, and the reason `Judgement` carries two.
        return REFINEMENT
    return COEXIST


def most_severe(resolutions: Sequence[Resolution]) -> int:
    """Which of these resolutions decides the candidate's fate.

    Args:
        resolutions: One per incumbent, in retrieval order - nearest first.

    Returns:
        The index of the winning resolution. Ties go to the earliest, which is
        the nearest incumbent, because `IncumbentSet.nearest` is cosine-ordered.

    Raises:
        ValueError: `resolutions` is empty. A candidate with no incumbents never
            reaches here - `detect` answers that case before retrieval results
            are read - so an empty sequence is a caller bug rather than a state
            to encode as `None`.

    A candidate that contradicts the third incumbent and duplicates the first is
    a contradiction. Reporting the first one's comfortable numbers would hide
    it, which is why this ranks rather than taking the nearest.
    """
    if not resolutions:
        raise ValueError(
            "most_severe needs at least one resolution; a candidate with no "
            "incumbents is answered before the table is consulted"
        )
    return min(range(len(resolutions)), key=lambda i: _PRECEDENCE.index(resolutions[i]))


def merge(incumbent: StoredAssertion, candidate: MemoryCandidate) -> StoredAssertion:
    """Fold a duplicate into the fact it duplicates.  §2.4

    Args:
        incumbent: The live assertion the candidate duplicates.
        candidate: The proposal, already classified `DUPLICATE`.

    Returns:
        The same assertion - same `assertion_id` - with the candidate's citation
        appended and `corroboration_count` raised if the citation is a genuinely
        new source. S4.4: "it does not create a row."

    **`corroboration_count` is not `len(provenance)`, and this is the function
    where that distinction is either kept or quietly lost.** `StoredAssertion`
    declares it as the number of *independent* sources: two spans from the same
    document are two provenance records and one source. §3.2's `S_cor` is a
    function of independent sources, so counting citations instead would inflate
    confidence exactly where a poisoning attempt wants it inflated - quote one
    planted document three times and the fact scores as corroborated. Sameness
    is `source_hash`, which is a digest of the document, not of the span.

    **Idempotent, because the outbox relay replays.** S3.3 delivers at least
    once, so this can be called twice with the same candidate; a citation
    already present returns the incumbent untouched rather than appending a
    duplicate and growing the list on every retry. `upsert` is idempotent by id
    for the same reason.

    Confidence is *not* recomputed here, though §2.4's sentence ends by asking
    for it. The corroboration term is `S_cor` inside §3.2's composite, and that
    composite is S5.2 - recomputing `C` would mean reimplementing it a layer
    early and from one of its five inputs. What this does is make the input
    correct; S5.2 is where the number moves.
    """
    citation = candidate.provenance
    if any(_is_same_citation(existing, citation) for existing in incumbent.provenance):
        return incumbent
    independent = all(
        existing.source_hash != citation.source_hash for existing in incumbent.provenance
    )
    return incumbent.model_copy(
        update={
            "provenance": [*incumbent.provenance, citation],
            "corroboration_count": incumbent.corroboration_count + int(independent),
        }
    )


def _is_same_citation(left: Provenance, right: Provenance) -> bool:
    """Are these two citations the same span of the same document?

    Compared on `(source_hash, source_span)` rather than by equality of the
    whole model: `alignment` and `captured_at` can differ between two extractions
    of the identical span, and a replay that re-ran the extractor would then
    append a second copy of a citation already held.
    """
    return left.source_hash == right.source_hash and left.source_span == right.source_span
