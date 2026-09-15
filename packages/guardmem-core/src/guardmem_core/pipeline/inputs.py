"""The adapters between one stage's output and the next one's input.  S5.6

`orchestrator.py` is control flow - which stage runs when, how many at once,
what happens when one fails. This is everything it has to *convert*, and the
conversions are separated because they are where the joins actually go wrong.
Each one is a pure function over values, so each is testable on its own, and
none of them is obvious:

- **The extractor's spans index into the text this module renders.** §1.3 makes
  `source_span` an offset into the denoised document, so the join between
  `filter_noise` and `extract` is a string concatenation that both sides have to
  agree on exactly. Getting it wrong lands every stored span a few characters
  off, onto real text, with nothing raising. It was held by convention until
  this step; `render_content` is the one renderer now.
- **§3.1 clusters "the K samples for a given `(subject, predicate)`"** and
  `ExtractionResult.samples` is ungrouped. `draws_for` is that grouping, and
  `None` marks a draw that proposed nothing - see `entropy.py` on why an
  abstention is a sample rather than a gap.
- **§3.2's `S_src` compares two texts and one of them does not exist.** A
  candidate has a verbatim span and a `(subject, predicate, object)` triple, not
  a sentence. `claim_text` renders one, and it is a *rendering* rather than a
  fact about the candidate, which is why it lives here and not on the model.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from guardmem_core.pipeline.l3_score import (
    MutationType,
    OverrideSignals,
    RiskFeatures,
    graph_fanout,
    novelty,
    source_tier_risk,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.pipeline.deps import CandidateRisk
    from guardmem_core.pipeline.l2_validate import IncumbentSet
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.candidate import ExtractedFact, MemoryCandidate
    from guardmem_core.schemas.ontology import PredicateSpec
    from guardmem_core.schemas.turn import Turn
    from guardmem_core.schemas.verdict import ConflictKind, ImpactLevel

__all__ = ["claim_text", "draws_for", "override_signals", "render_content", "risk_features"]


def render_content(turns: Sequence[Turn]) -> str:
    """Join the surviving turns into the document spans index into.

    Args:
        turns: What the noise filter kept, in order.

    Returns:
        One newline-joined string of the turn texts.

    **This is the only renderer, and that is the point.** `Provenance
    .source_span` is a pair of offsets into whatever string `extract` was given,
    and `ARCHITECTURE.md` §5 stores it as an `INT4RANGE` that the review UI
    slices the source with. If the caller that stores the document and the
    caller that extracted from it join the turns differently - a space instead
    of a newline, a role prefix, a dropped blank line - every span in the
    proposal points a few characters off, at real text, and nothing raises.

    The roles are not included. A `"user: "` prefix would be part of the offsets,
    so the stored span would quote the prefix back; whether a reviewer should
    see who said it is a rendering decision for the review UI, made against the
    turn ids it already has.
    """
    return "\n".join(turn.text for turn in turns)


def draws_for(
    samples: Sequence[Sequence[ExtractedFact]], candidate: MemoryCandidate
) -> list[str | None]:
    """What each draw said about this candidate's `(subject, predicate)`.  §3.1

    Args:
        samples: Every sample's facts, in draw order, canonical first.
        candidate: The fact being scored.

    Returns:
        One entry per draw: the rendered object where that draw proposed this
        `(subject, predicate)`, and `None` where it did not. Length is always
        `len(samples)`, which is §3.1's `K`.

    The *object* is what gets clustered, not the whole fact: §3.1's worked
    example clusters `{"Dr. Alvarez", "Dr. Alvarez", "Alvarez, MD", "Dr. Chen",
    "unclear"}` for one `primary_care_provider`, so subject and predicate are the
    grouping key and the object is the answer whose spread is being measured.

    A draw that proposed the pair twice contributes its first answer. That is a
    real case - a model listing two allergies emits two facts with the same
    subject and predicate - and it is the one place this grouping is lossy.
    `MANY`-cardinality predicates are exactly where §3.1's "the K samples for a
    given (subject, predicate)" stops being a well-defined set, and the spec does
    not say. First rather than last so the answer does not depend on emission
    order within a draw.
    """
    return [_first_object(draw, candidate) for draw in samples]


def claim_text(candidate: MemoryCandidate) -> str:
    """Render a candidate as a sentence an entailment model can judge.  §3.2

    Args:
        candidate: The fact.

    Returns:
        `"<subject> <predicate> <object>"`, with the predicate's underscores
        opened out.

    §3.2 asks for "entailment of the candidate by its own verbatim span", and a
    candidate is a triple rather than a sentence, so something has to render one.
    This is deliberately not `embed_text`: that renders `predicate: object` for
    *vector* comparison, where both sides go through the same function and the
    format only has to be consistent. Here one side is natural language written
    by a human, so the other side has to look like a sentence or the entailment
    score measures the formatting.

    Crude, and knowingly so - "patient:8812 primary care provider Dr. Alvarez"
    is not English. It is enough for a cross-encoder to align subject and object
    against a quoted span, and the alternative is a model call to phrase the
    claim, which would put a generation step inside a grounding check.
    """
    return (
        f"{candidate.subject} {candidate.predicate.replace('_', ' ')} {_render(candidate.object)}"
    )


def risk_features(
    candidate: MemoryCandidate,
    *,
    classified: CandidateRisk,
    kind: ConflictKind,
    impact: ImpactLevel,
    degree: int,
    incumbents: IncumbentSet,
) -> RiskFeatures:
    """Assemble §3.3's eight features for one candidate.

    Args:
        candidate: The fact. Only its citation is read here - the three features
            that would need more of it are declared rather than inferred, two on
            the predicate and one on the namespace (ADR-0009).
        classified: The three §3.3 named and defined nowhere. **This argument is
            replaced by the spec and the namespace when ADR-0009 lands**; it is
            still here because `PredicateSpec` does not carry the two fields
            yet.
        kind: Layer 2's finding, which decides `mutation_type`.
        impact: The predicate's declared impact.
        degree: Live edges touching the subject, from `GraphStore.degree`.
        incumbents: What retrieval found; the nearest cosine is `novelty`.

    Returns:
        The eight, each normalised to [0, 1].

    `novelty` reads `nearest[0].cosine` because `IncumbentSet.nearest` is
    cosine-ordered - "1 - max cosine to existing memory" is the *best* match, so
    it is the first, and an empty set means no neighbour at all.
    """
    return RiskFeatures(
        impact_declared=impact.risk_feature,
        mutation_type=MutationType.from_conflict(kind).risk_feature,
        scope=classified.scope.risk_feature,
        graph_fanout=graph_fanout(degree),
        pii_class=classified.pii_class.risk_feature,
        irreversibility=classified.irreversibility.risk_feature,
        source_tier_risk=source_tier_risk(candidate.provenance.source_tier),
        novelty=novelty(incumbents.nearest[0].cosine if incumbents.nearest else None),
    )


def override_signals(
    candidate: MemoryCandidate,
    *,
    spec: PredicateSpec,
    policy_version: str,
    kind: ConflictKind,
) -> OverrideSignals:
    """Assemble the context 3.4's seven overrides read.

    Args:
        candidate: For its citation's tier.
        spec: For the ontology's `requires_corroboration`.
        policy_version: Which pack produced the obligations.
        kind: Layer 2's finding, which names the mutation.

    Returns:
        The signals. Four of the six are benign today, each for a reason that is
        a missing *surface* rather than a shortcut.

    `injection_detected` is false because Layer 1 raises `InjectionDetected`
    rather than returning a flag - a compromised extraction never reaches a
    candidate, and `run` propagates it for the whole proposal. The signal stays
    in the vocabulary because S11.1's pre-flight detector quarantines instead of
    raising, and that one will set it.

    `budget_exhausted` and `circuit_open` are runtime state from surfaces that
    do not exist: the budget ledger is S9.2 and the circuit breaker S9.3. Both
    only ever *tighten*, so a false here can never widen the auto-write path -
    it can only fail to narrow it, which is the right direction to be wrong in
    while the surfaces are missing.
    """
    return OverrideSignals(
        injection_detected=False,
        source_tier=candidate.provenance.source_tier,
        mutation=MutationType.from_conflict(kind),
        requires_corroboration=spec.requires_corroboration,
        budget_exhausted=False,
        circuit_open=False,
        policy_version=policy_version,
    )


def _first_object(draw: Sequence[ExtractedFact], candidate: MemoryCandidate) -> str | None:
    """This draw's answer for the candidate's pair, or `None`."""
    for fact in draw:
        if fact.subject == candidate.subject and fact.predicate == candidate.predicate:
            return _render(fact.object)
    return None


def _render(value: ObjectValue) -> str:
    """An object value as text.

    `json.dumps` for the structured case so a dict renders deterministically -
    §3.1 compares these for equality of *meaning*, and two spellings of one
    mapping would cluster apart on formatting alone.
    """
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)
