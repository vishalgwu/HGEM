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

**The table itself is `dedupe.py`, as of S4.4.** This module decides *whether to
ask a model*; that one decides *what the answer means*. So the two deterministic
checks below return their row without a judge, and everything past them is
handed to `classify` - including the DUPLICATE and REFINEMENT rows, which read
the cosine and the entailment asymmetry this step deliberately only carried.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from guardmem_core.pipeline.l2_validate.dedupe import (
    CARDINALITY,
    COEXIST,
    TEMPORAL_OVERLAP,
    classify,
    most_severe,
)
from guardmem_core.pipeline.l2_validate.nli import Judgement, NLIJudge
from guardmem_core.schemas.entity import Cardinality
from guardmem_core.schemas.verdict import ConflictKind, ConflictReport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.memory.vector.base import ScoredAssertion
    from guardmem_core.pipeline.l2_validate.dedupe import Resolution
    from guardmem_core.pipeline.l2_validate.incumbents import IncumbentSet
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.schemas.ontology import PredicateSpec

__all__ = ["detect"]


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

    The rest of §2.3's table - DUPLICATE, REFINEMENT, and the precedence that
    picks one answer out of ten incumbents - is `dedupe.classify` and
    `dedupe.most_severe` as of S4.4. What stays here is the pair of checks that
    answer *before* the judge is asked.
    """
    if not incumbents.nearest:
        return _no_conflict()
    if spec.cardinality is Cardinality.ONE and (
        hit := _live_incumbent(incumbents.nearest, candidate.object)
    ):
        return _report(CARDINALITY, hit)
    if spec.cardinality is Cardinality.ONE_PER_TIME and (
        overlapping := _first_overlap(candidate, incumbents.nearest)
    ):
        return _report(TEMPORAL_OVERLAP, overlapping)
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


def _restates(candidate: MemoryCandidate, incumbent: StoredAssertion) -> bool:
    """Does this incumbent already assert the very same value?

    Args:
        candidate: The proposed fact.
        incumbent: One live incumbent.

    Returns:
        True when the objects are equal over intersecting intervals.

    **Evidence for §2.3's DUPLICATE row, and the I2 property suite is what found
    it missing.** That row identifies a duplicate by cosine and entailment,
    which are *proxies for sameness*; when the object is literally equal under
    the same predicate for the same subject, the proxy has nothing left to
    establish. Without this the pair falls through to `coexist` whenever the
    embedder scores the restatement under 0.95 or the judge scores it under
    0.85 - writing a second live row that says exactly what the first one says.
    On a `ONE` predicate that is invariant I2 broken; on a `MANY` predicate it
    is the unbounded duplication §2.4 exists to prevent.

    **It is evidence, not a short circuit, and that distinction is the whole
    care in this function.** An equal `object` does not mean the two claims
    agree, because the object does not carry polarity: "allergic to penicillin"
    and "not allergic to penicillin" both extract `penicillin`, and the
    negation lives only in the verbatim. Returning DUPLICATE here without
    asking the judge would merge a fact with its own negation and raise
    `corroboration_count` for it - the precise failure `dedupe`'s first recorded
    deviation exists to close. So this is handed to `classify`, which reads the
    contradiction score first and only then lets equality speak.

    The interval test is why this is not simply `object ==`. Under
    `ONE_PER_TIME` the same value over *disjoint* intervals is two facts, not
    one - an address lived at, left, and returned to - and merging them would
    collapse a real history into a single span that was never true.
    """
    return incumbent.object == candidate.object and _overlaps(candidate, incumbent)


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
    """Read §2.2(a)'s numbers through §2.3's table.

    Args:
        candidate: The proposed fact, for the object comparison `_restates`
            makes against each incumbent.
        incumbents: What retrieval found, paired positionally with `judgements`.
        judgements: One per incumbent, in the same order. `strict=True` below is
            the second half of the safety check `LLMJudge` makes first - a
            mismatch here would attribute one incumbent's contradiction to
            another, and neither side is willing to let that pass quietly.

    Returns:
        The report for whichever pair `most_severe` picked.

    Every pair is classified, not just the nearest. A candidate that duplicates
    the first incumbent and contradicts the third is a contradiction, and
    `dedupe._PRECEDENCE` is where that ordering is argued.
    """
    pairs = list(zip(incumbents.nearest, judgements, strict=True))
    resolutions = [
        classify(hit.cosine, judgement, restates=_restates(candidate, hit.assertion))
        for hit, judgement in pairs
    ]
    winner = most_severe(resolutions)
    hit, judgement = pairs[winner]
    return _report(resolutions[winner], hit, judgement)


def _no_conflict() -> ConflictReport:
    """The report for a candidate with no incumbents at all - a novel fact."""
    return ConflictReport(
        kind=ConflictKind.NONE,
        incumbent_assertion_id=None,
        entailment=0.0,
        contradiction=0.0,
        cosine=0.0,
        resolution_hint=COEXIST.resolution_hint,
    )


def _report(
    resolution: Resolution, hit: ScoredAssertion, judgement: Judgement | None = None
) -> ConflictReport:
    """Build the report for one incumbent and the row it matched.

    Args:
        resolution: The table row, from `dedupe`.
        hit: The incumbent it was matched against, with its cosine.
        judgement: The judge's numbers, or `None` when a deterministic check
            answered without asking.

    Returns:
        The report.

    `entailment` is §0's `P(incumbent ⊨ candidate)` - the forward direction, and
    the one `ConflictReport` declares. `entail_rev` is not on that model and is
    not invented here: `classify` is the only reader of the asymmetry and it
    reads it from the `Judgement` directly, so widening a schema the spec of
    record owns buys nothing.

    Zeroes when the deterministic checks answered, because they did so without
    asking - a fabricated 0.9 would read as a measurement.
    """
    return ConflictReport(
        kind=resolution.kind,
        incumbent_assertion_id=hit.assertion.assertion_id,
        entailment=judgement.entail_fwd if judgement else 0.0,
        contradiction=judgement.contradiction if judgement else 0.0,
        cosine=hit.cosine,
        resolution_hint=resolution.resolution_hint,
    )
