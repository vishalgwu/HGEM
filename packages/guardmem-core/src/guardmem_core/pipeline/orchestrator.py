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

**Layers 2 and 3 live in `candidate.py`.** This module is the per-*proposal*
half - noise filter, extraction, schema gate, and the bounded fan-out - and
that split is the one the paragraph below already described before
`RULES.md` §2.4's cap made it structural.

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
from typing import TYPE_CHECKING

from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.inputs import render_content
from guardmem_core.pipeline.l1_extract.extractor import ExtractionContext, extract
from guardmem_core.pipeline.l1_extract.noise_filter import filter_noise
from guardmem_core.pipeline.l2_validate import gate
from guardmem_core.pipeline.per_candidate import CandidateFailure, score_candidate
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.schemas.verdict import DecisionRecord

# `Proposal` and `PipelineResult` are pydantic models, so every type in their
# annotations has to exist at runtime - pydantic resolves them when the class is
# built, and a `TYPE_CHECKING`-only import leaves the model "not fully defined"
# until somebody calls `model_rebuild()`. The `NewType` ids and `Turn` are
# therefore imported here rather than below.
from guardmem_core.types import Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.pipeline.deps import Deps

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
            score_candidate(verdict, extracted.samples, proposal, deps, limit)
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
