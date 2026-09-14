"""The three conflict checks.  BUILD_NOTEBOOK.md S4.3

`MEMORY_ENGINE.md` §2.2's second half: (a) NLI contradiction, (b) cardinality,
(c) temporal overlap, over the incumbents `incumbents.py` retrieved.

**They run in the order (b), (c), (a), and that is the step's own ordering.**
S4.3's snippet checks cardinality first and returns "regardless of NLI", which
§2.2(b) states in as many words - so a certain answer is never paid for with a
BALANCED call. Temporal overlap joins it in front for the same reason: it is
arithmetic on two intervals. Only what neither settles reaches the judge.

The tension worth recording: §2.3's resolution table lists CONTRADICTION *above*
CARDINALITY, which read as an ordered match would make a contradictory
cardinality violation a CONTRADICTION. Both resolve by supersession and
CARDINALITY's is "always audited", so nothing is lost by answering with the
cheaper, certain one - and §2.2(b)'s "regardless of NLI" is the more specific
instruction.

**S4.4 owns the rest of §2.3's table.** DUPLICATE, REFINEMENT and the merge that
follows read `cosine` and the entailment asymmetry; this carries both onto the
report and stops there, because "Implement the table in MEMORY_ENGINE.md 2.3" is
S4.4's sentence, not this step's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from guardmem_core.pipeline.l2_validate.nli import Judgement, NLIJudge
from guardmem_core.schemas.entity import Cardinality
from guardmem_core.schemas.verdict import ConflictKind, ConflictReport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.memory.vector.base import ScoredAssertion
    from guardmem_core.pipeline.l2_validate.incumbents import IncumbentSet
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.schemas.ontology import PredicateSpec

__all__ = ["detect"]

# §2.2(a)'s thresholds, from §2.3's table. Named because two of them are the
# boundary of the "ambiguous" band - contradiction between 0.3 and 0.65 escalates
# rather than deciding, which is the band a human exists for.
_CONTRADICTION: Final = 0.65
_AMBIGUOUS_FLOOR: Final = 0.3


def _live_incumbent(
    incumbents: Sequence[ScoredAssertion], obj: ObjectValue
) -> ScoredAssertion | None:
    """The nearest live incumbent whose object differs from `obj`.

    Args:
        incumbents: What retrieval returned, nearest first.
        obj: The candidate's value.

    Returns:
        The first differing one, or `None` when every incumbent already says
        what the candidate says.

    "Differing" is what makes this a conflict rather than a duplicate: §2.2(b)
    fires when a `ONE` predicate "already has a live value" *with a different
    object*. An incumbent that agrees is S4.4's merge, not a cardinality
    violation, and returning it here would retire a fact in favour of itself.

    Retrieval already filtered to live rows, so no `valid_to` check is repeated
    here - `search` with no `as_of` returns `valid_to IS NULL AND visible`.
    """
    return next((hit for hit in incumbents if hit.assertion.object != obj), None)


def _overlaps(candidate: MemoryCandidate, incumbent: StoredAssertion) -> bool:
    """Do the two validity intervals intersect?

    Args:
        candidate: The proposed fact.
        incumbent: What is already believed.

    Returns:
        True when `[valid_from, valid_to)` intersect, half-open at both ends.

    An open `valid_to` means "still true", so it extends to infinity. A missing
    `valid_from` on the candidate means the extractor did not say when the fact
    became true - **which is every candidate today**, because
    `extract_memories/v1.md` does not ask for it and `ExtractedFact` therefore
    has no such field. `None` is read as "unbounded in the past", so an
    undated candidate overlaps any live incumbent, which is the conservative
    reading: it raises the conflict rather than silently missing it.
    """
    start = candidate.valid_from
    end = candidate.valid_to
    if end is not None and end <= incumbent.valid_from:
        return False
    return not (
        incumbent.valid_to is not None and start is not None and incumbent.valid_to <= start
    )


async def detect(
    candidate: MemoryCandidate,
    incumbents: IncumbentSet,
    spec: PredicateSpec,
    nli: NLIJudge,
) -> ConflictReport:
    """Compare a candidate against its incumbents.  §2.2

    Args:
        candidate: The proposed fact, admitted by the schema gate.
        incumbents: What retrieval found, nearest first.
        spec: The predicate's declaration, from the tenant ontology. This is
            where `cardinality` comes from, so the gate's ontology lookup is
            passed along rather than repeated.
        nli: The judge. Called at most once, and not at all when (b) or (c)
            settles the question.

    Returns:
        A `ConflictReport`. `NONE` with a `coexist` hint when nothing conflicts,
        which is the ordinary answer.

    Raises:
        InjectionDetected: the judge's reply echoed the canary.
        ValidationRejected: the judge's reply did not validate.
        ProviderUnavailable: the judge is unreachable. Retryable.

    **`resolution_hint` is a hint, and for CONTRADICTION it is deliberately the
    conservative one.** §2.3 resolves a contradiction by "supersede if candidate
    newer *and* C >= tau_hi, else escalate" - and `C` is Layer 3's confidence
    composite, which does not exist when this runs. So this emits `escalate`,
    and S5.4's decision matrix is where a confident, newer candidate may be
    upgraded. `ARCHITECTURE.md` §0: degradation never widens the auto-write
    path, and guessing `supersede` here would be exactly that.

    S4.4 owns the rest of §2.3's table - DUPLICATE, REFINEMENT and the merge
    that follows - which is why `cosine` is carried onto the report rather than
    compared here.
    """
    if not incumbents.nearest:
        return _no_conflict()
    if spec.cardinality is Cardinality.ONE and (
        hit := _live_incumbent(incumbents.nearest, candidate.object)
    ):
        return _conflict(ConflictKind.CARDINALITY, hit, "supersede")
    if spec.cardinality is Cardinality.ONE_PER_TIME and (
        overlapping := _first_overlap(candidate, incumbents.nearest)
    ):
        return _conflict(ConflictKind.TEMPORAL_OVERLAP, overlapping, "supersede")
    return _adjudicate(candidate, incumbents, await _judge(candidate, incumbents, nli))


async def _judge(
    candidate: MemoryCandidate, incumbents: IncumbentSet, nli: NLIJudge
) -> list[Judgement]:
    """Ask the judge about every incumbent, in one call."""
    return await nli.compare(
        candidate.provenance.verbatim,
        [hit.assertion.provenance[0].verbatim for hit in incumbents.nearest],
        trace_id=candidate.trace_id,
    )


def _first_overlap(
    candidate: MemoryCandidate, incumbents: Sequence[ScoredAssertion]
) -> ScoredAssertion | None:
    """The nearest live incumbent with a different object over an intersecting interval."""
    return next(
        (
            hit
            for hit in incumbents
            if hit.assertion.object != candidate.object and _overlaps(candidate, hit.assertion)
        ),
        None,
    )


def _adjudicate(
    candidate: MemoryCandidate, incumbents: IncumbentSet, judgements: list[Judgement]
) -> ConflictReport:
    """Read §2.2(a)'s numbers into a verdict.

    Args:
        candidate: The proposed fact, unused beyond its shape - kept in the
            signature so the reasoning below reads against something.
        incumbents: What retrieval found, paired positionally with `judgements`.
        judgements: One per incumbent, in the same order.

    Returns:
        CONTRADICTION at `contradiction >= 0.65`; `NONE` with an `escalate` hint
        in §2.3's ambiguous band, 0.3 to 0.65, where "the NLI is unsure, so a
        human or frontier model decides"; `NONE` and `coexist` below it.

    The worst pair decides, not the nearest. A candidate that contradicts the
    third incumbent and agrees with the first is still a contradiction, and
    reporting the nearest one's comfortable numbers would hide it.
    """
    worst = max(
        zip(incumbents.nearest, judgements, strict=True),
        key=lambda pair: pair[1].contradiction,
    )
    hit, judgement = worst
    if judgement.contradiction >= _CONTRADICTION:
        return _conflict(ConflictKind.CONTRADICTION, hit, "escalate", judgement)
    if judgement.contradiction >= _AMBIGUOUS_FLOOR:
        return _conflict(ConflictKind.NONE, hit, "escalate", judgement)
    return _conflict(ConflictKind.NONE, hit, "coexist", judgement)


def _no_conflict() -> ConflictReport:
    """The report for a candidate with no incumbents at all - a novel fact."""
    return ConflictReport(
        kind=ConflictKind.NONE,
        incumbent_assertion_id=None,
        entailment=0.0,
        contradiction=0.0,
        cosine=0.0,
        resolution_hint="coexist",
    )


def _conflict(
    kind: ConflictKind,
    hit: ScoredAssertion,
    resolution_hint: Literal["merge", "supersede", "coexist", "escalate"],
    judgement: Judgement | None = None,
) -> ConflictReport:
    """Build the report for one incumbent.

    `entailment` is §0's `P(incumbent ⊨ candidate)` - the forward direction, and
    the one `ConflictReport` declares. `entail_rev` is not on that model and is
    not invented here: S4.4's REFINEMENT row is the only reader of the
    asymmetry, and it can ask the judge itself rather than have this widen a
    schema the spec of record owns.

    Zeroes when the deterministic checks answered, because they did so without
    asking - a fabricated 0.9 would read as a measurement.
    """
    return ConflictReport(
        kind=kind,
        incumbent_assertion_id=hit.assertion.assertion_id,
        entailment=judgement.entail_fwd if judgement else 0.0,
        contradiction=judgement.contradiction if judgement else 0.0,
        cosine=hit.cosine,
        resolution_hint=resolution_hint,
    )
