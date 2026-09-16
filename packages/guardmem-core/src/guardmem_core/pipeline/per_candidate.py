"""Layers 2 and 3 for one candidate.  BUILD_NOTEBOOK.md S5.6

`orchestrator.py` says the seam this module is cut along: "Layer 1 runs once for
the proposal; Layers 2 and 3 run per candidate, concurrently and bounded." That
sentence was true before this file existed and the two halves lived together;
`RULES.md` §2.4's cap is what made it structural.

What is on this side of the seam: everything that happens once *per candidate* -
resolving its subject, retrieving what is already believed, judging the conflict,
scoring confidence and impact, and reaching a decision. What is on the other:
the noise filter, the extractor and the schema gate, which run once for a whole
proposal, and the fan-out that calls this.

**`CandidateFailure` moved with it**, because a failure here is attributed to
one candidate and never to the batch - which is the property `score_candidate`
exists to keep.

Named `per_candidate` rather than `candidate`: `schemas/candidate.py` already
holds `MemoryCandidate`, and two source modules with one basename is the trap
open item #35 records - mypy tolerates it where the packages differ, but a
reader and a parametrised test id both have to disambiguate by directory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardmem_core.pipeline.inputs import (
    claim_text,
    draws_for,
    entail_pairs,
    override_signals,
    risk_features,
)
from guardmem_core.pipeline.l2_validate import detect, retrieve_incumbents
from guardmem_core.pipeline.l3_score import (
    cluster_meanings,
    decide,
    score_confidence,
    score_impact,
)
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.verdict import DecisionRecord
from guardmem_core.types import EntityId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.pipeline.deps import Deps
    from guardmem_core.pipeline.l2_validate import GatedCandidate, IncumbentSet
    from guardmem_core.pipeline.orchestrator import Proposal
    from guardmem_core.schemas.candidate import ExtractedFact
    from guardmem_core.schemas.ontology import PredicateSpec
    from guardmem_core.schemas.verdict import ConfidenceReport, ConflictReport
    from guardmem_core.types import CandidateId, EntityId, Namespace, TraceId

__all__ = ["CandidateFailure", "GovernedCandidate", "score_candidate"]


@dataclass(frozen=True, slots=True)
class CandidateFailure:
    """One candidate that could not be scored, and why.

    Attributes:
        candidate_id: Which one.
        error: The exception itself. Carried rather than its message, so a
            caller can retry on `ProviderUnavailable` and quarantine on
            `InjectionDetected` without parsing strings.

    A dataclass rather than a `GMModel` because it holds an exception, which is
    not a JSON value - and this never reaches an audit payload. What does is a
    `DecisionRecord`, and a candidate that failed produced none.
    """

    candidate_id: CandidateId
    error: Exception


class GovernedCandidate(GMModel):
    """One candidate and what was decided about it.

    Attributes:
        candidate: The proposed fact, as Layer 1 extracted it.
        subject_id: The entity the candidate resolved to. Carried because it
            exists nowhere else afterwards - `MemoryCandidate.subject` is the
            surface form the speaker used, `StoredAssertion.subject_id` is the
            resolved entity, and the resolution happens inside `_decide_one`.
            ADR-0010's applier needs it to write a row at all.
        record: What Layer 3 decided.
        incumbent: The live assertion the decision was taken *against*, where
            there was one - the row `ConflictReport.incumbent_assertion_id`
            names. Carried because ADR-0010's applier needs it and cannot get it
            any other way: §2.4's merge folds a duplicate into the incumbent
            rather than inserting, which means calling `dedupe.merge` with the
            whole object. Re-reading it by id after the fact would be a second
            read of state that may have moved, and the decision was made against
            *this* version.

    **The pairing is structural because positional pairing is the bug this
    codebase keeps catching.** `LLMJudge` checks its judgement count because a
    short reply "would read one fact's contradiction as another's"; the same
    hazard applies here and is worse, because a mismatched decision is attached
    to a fact a *reviewer* then reads.

    It exists because a `DecisionRecord` cannot say what it is about.
    `MEMORY_ENGINE.md` §0 declares its eight fields and none of them is an
    identity - `decide()` takes reports and thresholds, not a candidate, so
    there was nowhere for one to come from. That is fine for replay, which is
    what §0 designed it for, and not fine for anything that has to *show* a
    decision: this, the HITL queue (S18.1) and the review UI (S19.2) all need
    the fact beside the verdict. Whether §0 should carry a `candidate_id` is a
    spec question and needs an ADR; this model is `PipelineResult`'s own and
    answers it for the pipeline without pre-empting that.
    """

    candidate: MemoryCandidate
    subject_id: EntityId
    record: DecisionRecord
    incumbent: StoredAssertion | None = None


async def score_candidate(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    proposal: Proposal,
    deps: Deps,
    limit: asyncio.Semaphore,
) -> GovernedCandidate | CandidateFailure:
    """Layers 2 and 3 for one candidate, under the concurrency bound.

    Returns:
        The candidate paired with its decision, or a `CandidateFailure`
        naming the exception. Paired rather than the record alone because a
        `DecisionRecord` cannot say what it is about - see
        `GovernedCandidate`.

    Catching `Exception` is deliberate and is the narrowest thing that works
    here: a candidate is one unit of work among many, and the alternative is a
    batch that loses nineteen good results to one bad provider response. The
    exception is carried rather than swallowed - what a `BudgetExceeded` means
    is the caller's decision, not this function's.
    """
    async with limit:
        try:
            record, subject_id, incumbent = await _decide_one(verdict, samples, proposal, deps)
            return GovernedCandidate(
                candidate=verdict.candidate,
                subject_id=subject_id,
                record=record,
                incumbent=incumbent,
            )
        except Exception as exc:
            return CandidateFailure(candidate_id=verdict.candidate.candidate_id, error=exc)


async def _decide_one(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    proposal: Proposal,
    deps: Deps,
) -> tuple[DecisionRecord, EntityId, StoredAssertion | None]:
    """Layer 2 for one candidate, then Layer 3 over what it found.

    Returns the decision, the entity the subject resolved to, and the
    incumbent it was taken against. All three are in scope only here, and
    ADR-0010's applier needs all three - see `GovernedCandidate`.

    Split at that seam rather than run as one function: Layer 2 asks what is
    already believed and needs two stores and a judge, Layer 3 asks what to
    think of the candidate given the answer and needs none of them. It is also
    where `RULES.md` §2.4's 50-line cap fell, which is the cap working - the
    two halves read better apart.
    """
    candidate = verdict.candidate
    spec = deps.ontology.predicate(candidate.predicate)
    if spec is None:  # pragma: no cover - `gate` admits only known predicates.
        raise ValueError(f"{candidate.predicate} is not in the ontology")

    subject_id = await deps.resolver.resolve(
        proposal.subject_hint or candidate.subject,
        tenant_id=proposal.tenant_id,
        namespace=proposal.namespace,
        # ADR-0008: `entity.type` is NOT NULL and the ontology is the only thing
        # that knows it. The spec was in scope here before the ADR and was not
        # passed, which is what made the protocol unimplementable.
        expected_type=spec.subject,
    )
    incumbents = await retrieve_incumbents(
        candidate,
        subject_id=subject_id,
        vector=deps.vector,
        graph=deps.graph,
        embedder=deps.embedder,
    )
    conflict = await detect(candidate, incumbents, spec, deps.nli)
    record = await _score_and_decide(
        verdict,
        samples,
        deps,
        spec=spec,
        namespace=proposal.namespace,
        trace_id=proposal.trace_id,
        subject_id=subject_id,
        incumbents=incumbents,
        conflict=conflict,
    )
    return record, subject_id, _incumbent_of(conflict, incumbents)


async def _score_and_decide(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    deps: Deps,
    *,
    spec: PredicateSpec,
    namespace: Namespace,
    trace_id: TraceId,
    subject_id: EntityId,
    incumbents: IncumbentSet,
    conflict: ConflictReport,
) -> DecisionRecord:
    """All of Layer 3, given what Layer 2 found.

    §3.2 is `_confidence_for`, below; what is left here is §3.3's risk and
    §3.4's matrix, which need no model call at all.
    """
    candidate = verdict.candidate
    confidence = await _confidence_for(verdict, samples, deps, conflict=conflict, trace_id=trace_id)
    risk = score_impact(
        risk_features(
            candidate,
            # ADR-0009: three of the eight features are declared by the
            # predicate and one by the namespace. Nothing is inferred, which is
            # what makes `R` replayable.
            spec=spec,
            namespace=namespace,
            kind=conflict.kind,
            degree=await deps.graph.degree(subject_id),
            incumbents=incumbents,
        ),
        spec.impact,
        # Empty until S12.2 builds the policy engine. `ObligationKind` is the
        # vocabulary and `RiskVerdict` validates against it; nothing evaluates a
        # pack yet, so an obligation here would be one this step invented.
        obligations=[],
        betas=deps.betas,
    )
    return decide(
        confidence,
        risk,
        conflict,
        deps.thresholds,
        # No escalation loop yet. 3.4's ESCALATE means "re-run Layer 3 on the
        # FRONTIER tier with K=5 and the incumbent context", which is a second
        # pass this function does not make - so every candidate is a first pass
        # and an ESCALATE is returned for the caller to act on. S9.1 gives the
        # tiers real providers and is where a loop could be measured rather than
        # guessed.
        False,
        override_signals(
            candidate, spec=spec, policy_version=deps.policy_version, kind=conflict.kind
        ),
    )


async def _confidence_for(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    deps: Deps,
    *,
    conflict: ConflictReport,
    trace_id: TraceId,
) -> ConfidenceReport:
    """§3.2's five terms for one candidate, and the one model call they need.

    Args:
        verdict: The gated candidate; `schema_fit` is §3.2's `S_sch`.
        samples: Every draw's facts, for §3.1's clustering.
        deps: For the entailment lookup and the weight set.
        conflict: Layer 2's report, which is §3.2's `S_con`.
        trace_id: The proposal's trace, for the lookup's errors.

    Returns:
        The report, with `C` and every term that produced it.

    **One entailment call, before any scoring, and that shape is the point.**
    `EntailFn` is a sync callable - what a local cross-encoder is - and
    `LLMEntailer` is async. `entropy.py` anticipated exactly this: "an async
    backend should precompute the pairs it needs and pass a lookup". So
    `entail_pairs` assembles every question this function is about to ask - the
    `K(K-1)` comparisons inside `cluster_meanings` and §3.2's one grounding pair
    - and `deps.entail` answers them in a single batched completion. Asking them
    one at a time would be the slowest thing in the pipeline.

    Split from `_score_and_decide` at `RULES.md` §2.4's 50-line function cap.
    The seam the cap found is §3.2's own boundary: everything here needs a model
    and everything left there does not.
    """
    candidate = verdict.candidate
    draws = draws_for(samples, candidate)
    claim = claim_text(candidate)
    entail = await deps.entail(
        entail_pairs(draws, verbatim=candidate.provenance.verbatim, claim=claim),
        trace_id=trace_id,
    )
    return score_confidence(
        entropy=cluster_meanings(draws, entail).entropy,
        # 3.2's `S_src`: does the cited span entail the claim it is cited for?
        # Both texts are the candidate's own, which is what makes this a
        # grounding check rather than a second opinion about the world.
        span_entailment=entail(candidate.provenance.verbatim, claim),
        tier=candidate.provenance.source_tier,
        alignment=candidate.provenance.alignment,
        schema_fit=verdict.schema_fit,
        # A fresh candidate rests on exactly one citation. It becomes more than
        # one only through 2.4's merge, which raises the *incumbent's*
        # `corroboration_count` - and a merge produces no new candidate to score.
        sources=1,
        conflict=conflict,
        weights=deps.weights,
    )


def _incumbent_of(conflict: ConflictReport, incumbents: IncumbentSet) -> StoredAssertion | None:
    """The live assertion this decision was taken against, if there was one.

    Args:
        conflict: Layer 2's report; `incumbent_assertion_id` names the row.
        incumbents: What retrieval found.

    Returns:
        The matching assertion, or `None` when the candidate conflicted with
        nothing - which is the common case, since most facts are novel.

    Looked up in what retrieval already returned rather than re-read from the
    store. The decision was taken against *this* version of the row, and a
    second read could return one that has since been superseded - so the applier
    would merge into a fact the scorer never saw.
    """
    if conflict.incumbent_assertion_id is None:
        return None
    return next(
        (
            scored.assertion
            for scored in incumbents.nearest
            if scored.assertion.assertion_id == conflict.incumbent_assertion_id
        ),
        None,
    )
