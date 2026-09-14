"""One entrypoint, Layer 1 through Layer 3.  BUILD_NOTEBOOK.md S5.6

The step every "nothing joins the stages yet" note has been pointing at. Noise
filter, extractor, schema gate, incumbent retrieval, conflict detection,
entropy, confidence, impact, decision - in that order, for one proposal.

**What this returns, and the one thing it does not do.** A `DecisionRecord` per
candidate: no assertion is written here. That is not a shortcut, it is where the
transaction boundary falls. `RULES.md` non-negotiable #4 requires the audit
event to commit "in the same transaction as the state change", and
`VectorStore.upsert` owns the only transaction in the write path by design - the
protocol hands out no connection, because `ARCHITECTURE.md` §2.4 makes the
backend an operator decision and a Qdrant store has no Postgres transaction to
join. Writing and auditing atomically is therefore a Postgres-specific
composition owning one connection, and building it here would mean either
widening the protocol or teaching the store about audit. Both are ADR-sized
(`RULES.md` §8). What ships is the decision layer, complete and replayable.

**Candidates are scored concurrently and the bound is explicit.** S5.6's sketch
says "bounded by semaphore, TaskGroup" and `RULES.md` §2.2 says the same for any
fan-out. Each candidate costs up to three model calls - the judge, and `entail`
twice - so an unbounded batch of forty is forty concurrent completions against
one provider's rate limit. The bound comes off `Deps`, which reads it from
`settings.max_concurrent_scores`: this module shipped with a module-level
constant instead, which meant the setting S1.4 declared for exactly this had no
reader, and `GM_MAX_CONCURRENT_SCORES=2` changed nothing while looking as though
it had.

**A failure is attributed to its candidate rather than losing the batch.** A
`TaskGroup` cancels its siblings when a task raises, which is right for a dual
write and wrong here: one candidate whose judge timed out should not discard the
nineteen that scored cleanly. `asyncio.gather` without `return_exceptions`
behaves the same way, so each candidate catches its own and `run` returns the
failures beside the results.

**Nothing here reads a clock.** `captured_at` comes off the turns, not from
`now()`, which is what lets `scripts/replay_trace.py` mean anything - see
`_context`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.inputs import (
    claim_text,
    draws_for,
    override_signals,
    render_content,
    risk_features,
)
from guardmem_core.pipeline.l1_extract.extractor import ExtractionContext, extract
from guardmem_core.pipeline.l1_extract.noise_filter import filter_noise
from guardmem_core.pipeline.l2_validate import detect, gate, retrieve_incumbents
from guardmem_core.pipeline.l3_score import (
    cluster_meanings,
    decide,
    score_confidence,
    score_impact,
)
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.schemas.verdict import DecisionRecord

# `Proposal` and `PipelineResult` are pydantic models, so every type in their
# annotations has to exist at runtime - pydantic resolves them when the class is
# built, and a `TYPE_CHECKING`-only import leaves the model "not fully defined"
# until somebody calls `model_rebuild()`. The `NewType` ids and `Turn` are
# therefore imported here rather than below.
from guardmem_core.types import CandidateId, EntityId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.pipeline.deps import Deps
    from guardmem_core.pipeline.l2_validate import GatedCandidate, IncumbentSet
    from guardmem_core.schemas.candidate import ExtractedFact
    from guardmem_core.schemas.ontology import PredicateSpec
    from guardmem_core.schemas.verdict import ConflictReport

__all__ = ["CandidateFailure", "PipelineResult", "Proposal", "run"]


class Proposal(GMModel):
    """One submission to govern.  `MCP_INTEGRATION.md` §2.2

    Attributes:
        trace_id: This proposal's trace, assigned by the caller.
        tenant_id: Owning tenant.
        namespace: Isolation scope the candidates land in.
        turns: The raw conversation. §2.2's `content` is a string; this is the
            structured form the noise filter needs, and the gateway that accepts
            a string is what splits it (S8.1).
        source_tier: Trust of the source. §2.2 defaults it to
            `unverified_user`; there is no default here, because a tier nobody
            stated is a tier nobody chose.
        k: How many samples to draw. §1.2 ties it to §2.2's `risk_hint`.
        tier: Which model tier extraction runs on.
        subject_hint: §2.2's `hints.subject`, handed to the resolver where the
            caller already knows the answer.

    Named `Proposal` rather than `MemoryProposal`: §2.2 publishes that name as a
    *tool schema* carrying fields this layer has no use for - `mode`,
    `idempotency_key`, `predicates_of_interest` - and the model that holds them
    belongs to the gateway that accepts them (S8.1). This is the pipeline's half.
    """

    trace_id: TraceId
    tenant_id: TenantId
    namespace: Namespace
    turns: list[Turn]
    source_tier: SourceTier
    k: int
    tier: Tier
    subject_hint: str | None = None


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


class PipelineResult(GMModel):
    """What one proposal produced.

    Attributes:
        trace_id: The proposal's trace.
        decisions: One record per candidate that was scored, in candidate order.
        quarantined: Candidate ids the schema gate sent to quarantine - §2.1's
            unknown predicate. Not scored, and not failures either.
        rejected: Candidate ids the gate rejected outright, `REJECT(SCHEMA)`.
        dropped_noise: How many turns the filter ate. §1.1 is emphatic that
            everything dropped is counted.
        dropped_unsourced: How many facts had no locatable span (§1.3).

    Failures are deliberately not a field: they hold exceptions, so this would
    stop being JSON-serialisable and could not be an audit payload. `run`
    returns them alongside.
    """

    trace_id: TraceId
    decisions: list[DecisionRecord]
    quarantined: list[str]
    rejected: list[str]
    dropped_noise: int
    dropped_unsourced: int


async def run(proposal: Proposal, deps: Deps) -> tuple[PipelineResult, list[CandidateFailure]]:
    """Govern one proposal, end to end.

    Args:
        proposal: What to govern.
        deps: Everything to govern it with. See `deps.py` on the three
            dependencies nothing in this repository supplies.

    Returns:
        The result, and one `CandidateFailure` per candidate that raised.

    Raises:
        InjectionDetected: extraction itself was compromised. Not attributed to
            a candidate, because at that point no candidate is trustworthy.
        ProviderUnavailable: the noise filter or the extractor could not run.
            Retryable, and the whole proposal is retried.
        ValueError: no surviving turn carries a capture time - see `_context`.

    Layer 1 runs once for the proposal; Layers 2 and 3 run per candidate,
    concurrently and bounded. Nothing is written - see the module docstring.
    """
    denoised = await filter_noise(
        proposal.turns,
        deps.llm,
        namespace=proposal.namespace,
        trace_id=proposal.trace_id,
    )
    extracted = await extract(
        render_content(denoised.kept),
        deps.llm,
        context=_context(proposal, denoised.kept),
        ontology_yaml=deps.ontology.as_prompt_yaml(),
        k=proposal.k,
        tier=proposal.tier,
        dropped_noise=len(denoised.dropped),
    )
    admitted = gate(extracted.candidates, deps.ontology)

    limit = asyncio.Semaphore(deps.max_concurrent_scores)
    scored = await asyncio.gather(
        *(
            _score_one(verdict, extracted.samples, proposal, deps, limit)
            for verdict in admitted.admitted
        )
    )
    return (
        PipelineResult(
            trace_id=proposal.trace_id,
            decisions=[o for o in scored if isinstance(o, DecisionRecord)],
            quarantined=[v.candidate.candidate_id for v in admitted.quarantined],
            rejected=[v.candidate.candidate_id for v in admitted.rejected],
            dropped_noise=len(denoised.dropped),
            dropped_unsourced=extracted.dropped_unsourced,
        ),
        [o for o in scored if isinstance(o, CandidateFailure)],
    )


def _context(proposal: Proposal, kept: Sequence[Turn]) -> ExtractionContext:
    """The caller-owned half of extraction.

    Args:
        proposal: For the tenant, namespace, trace and source tier.
        kept: The surviving turns.

    Returns:
        The context `extract` stamps onto every candidate.

    Raises:
        ValueError: no surviving turn carries `captured_at`.

    **`captured_at` comes off the turns, never from a clock**, and that is what
    makes `scripts/replay_trace.py` possible at all. `Provenance.captured_at` is
    "when the source was captured"; reading `now()` here would stamp a replayed
    proposal with the replay's own time, so a re-run could never be compared
    with the original. The earliest surviving turn is the one the document
    starts at.

    Raising when none carries a time is the strict choice, and the alternative
    was worse: a fallback to `now()` is a clock read hidden behind a condition,
    which is exactly the kind of impurity that only shows up as a replay diff
    months later.
    """
    stamps = [turn.captured_at for turn in kept if turn.captured_at is not None]
    if not stamps:
        raise ValueError(
            "no surviving turn carries captured_at; Provenance requires it, and "
            "falling back to a clock would make this proposal unreplayable"
        )
    return ExtractionContext(
        tenant_id=proposal.tenant_id,
        namespace=proposal.namespace,
        trace_id=proposal.trace_id,
        source_tier=proposal.source_tier,
        captured_at=min(stamps),
    )


async def _score_one(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    proposal: Proposal,
    deps: Deps,
    limit: asyncio.Semaphore,
) -> DecisionRecord | CandidateFailure:
    """Layers 2 and 3 for one candidate, under the concurrency bound.

    Returns:
        The decision record, or a `CandidateFailure` naming the exception.

    Catching `Exception` is deliberate and is the narrowest thing that works
    here: a candidate is one unit of work among many, and the alternative is a
    batch that loses nineteen good results to one bad provider response. The
    exception is carried rather than swallowed - what a `BudgetExceeded` means
    is the caller's decision, not this function's.
    """
    async with limit:
        try:
            return await _decide_one(verdict, samples, proposal, deps)
        except Exception as exc:
            return CandidateFailure(candidate_id=verdict.candidate.candidate_id, error=exc)


async def _decide_one(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    proposal: Proposal,
    deps: Deps,
) -> DecisionRecord:
    """Layer 2 for one candidate, then Layer 3 over what it found.

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
    )
    incumbents = await retrieve_incumbents(
        candidate,
        subject_id=subject_id,
        vector=deps.vector,
        graph=deps.graph,
        embedder=deps.embedder,
    )
    conflict = await detect(candidate, incumbents, spec, deps.nli)
    return await _score_and_decide(
        verdict,
        samples,
        deps,
        spec=spec,
        subject_id=subject_id,
        incumbents=incumbents,
        conflict=conflict,
    )


async def _score_and_decide(
    verdict: GatedCandidate,
    samples: Sequence[Sequence[ExtractedFact]],
    deps: Deps,
    *,
    spec: PredicateSpec,
    subject_id: EntityId,
    incumbents: IncumbentSet,
    conflict: ConflictReport,
) -> DecisionRecord:
    """All of Layer 3, given what Layer 2 found."""
    candidate = verdict.candidate
    confidence = score_confidence(
        entropy=cluster_meanings(draws_for(samples, candidate), deps.entail).entropy,
        # 3.2's `S_src`: does the cited span entail the claim it is cited for?
        # Both texts are the candidate's own, which is what makes this a
        # grounding check rather than a second opinion about the world.
        span_entailment=deps.entail(candidate.provenance.verbatim, claim_text(candidate)),
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
    risk = score_impact(
        risk_features(
            candidate,
            classified=await deps.classifier.classify(candidate),
            kind=conflict.kind,
            impact=spec.impact,
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
