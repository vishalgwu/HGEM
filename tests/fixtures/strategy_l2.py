"""Hypothesis strategies for the Layer 2 models.  S4.1

Its own module for the reason `strategy_ontology.py` is: `strategies.py` sits a
handful of lines under `RULES.md` §2.4's cap, and the registry there should stay
a readable index rather than becoming the place every new model's generator
lands.

`SchemaGateResult` and `IncumbentSet` are drawn as independent lists, which is
*looser* than
the gate can actually produce - a real result never puts a `REJECTED` verdict in
`admitted`. That is deliberate. These strategies feed the schema-layer property
tests, whose job is round-tripping and `extra="forbid"`, and a generator that
enforced the gate's invariants would be asserting the gate's behaviour in the
suite that tests serialisation. `tests/unit/test_schema_gate.py` is where the
grouping is held to its meaning.
"""

from __future__ import annotations

from hypothesis import strategies as st

from fixtures.strategy_primitives import _ID, _UNIT
from guardmem_core.pipeline.l2_validate import (
    AdjudicationBatch,
    GatedCandidate,
    GateOutcome,
    IncumbentSet,
    Judgement,
    Resolution,
    SchemaGateResult,
)
from guardmem_core.schemas import GMModel
from guardmem_core.schemas.verdict import ConflictKind
from guardmem_core.types import CandidateId

__all__ = ["l2_strategies"]


# One NLI verdict. `_UNIT` is [0, 1], which is what all three fields declare.
_JUDGEMENTS = st.builds(Judgement, entail_fwd=_UNIT, entail_rev=_UNIT, contradiction=_UNIT)

# Any (kind, hint) pairing, including ones §2.3's table never emits - DUPLICATE
# resolved by `escalate`, say. Loose for the reason in the module docstring: this
# feeds the serialisation properties, and `tests/unit/test_dedupe.py` is where
# the seven real rows are pinned to their meanings.
_RESOLUTIONS = st.builds(
    Resolution,
    kind=st.sampled_from(ConflictKind),
    resolution_hint=st.sampled_from(["merge", "supersede", "coexist", "escalate"]),
)


def _gated(candidates: st.SearchStrategy[object]) -> st.SearchStrategy[GatedCandidate]:
    """One verdict over a generated candidate."""
    return st.builds(
        GatedCandidate,
        candidate=candidates,
        outcome=st.sampled_from(GateOutcome),
        schema_fit=_UNIT,
        reason_code=_ID,
    )


def l2_strategies(
    candidates: st.SearchStrategy[object],
    scored: st.SearchStrategy[object],
    edges: st.SearchStrategy[object],
) -> dict[type[GMModel], st.SearchStrategy[GMModel]]:
    """Build the registry entries, given the generators this layer composes.

    Takes them as arguments rather than importing them, because they live in
    `strategies.py` and importing them back would make the two modules import
    each other.
    """
    gated = _gated(candidates)
    return {
        GatedCandidate: gated,
        SchemaGateResult: st.builds(
            SchemaGateResult,
            admitted=st.lists(gated, max_size=3),
            quarantined=st.lists(gated, max_size=2),
            rejected=st.lists(gated, max_size=2),
        ),
        Judgement: _JUDGEMENTS,
        Resolution: _RESOLUTIONS,
        AdjudicationBatch: st.builds(
            AdjudicationBatch, judgements=st.lists(_JUDGEMENTS, max_size=3)
        ),
        IncumbentSet: st.builds(
            IncumbentSet,
            candidate_id=_ID.map(CandidateId),
            nearest=st.lists(scored, max_size=3),
            neighbours=st.lists(edges, max_size=2),
        ),
    }
