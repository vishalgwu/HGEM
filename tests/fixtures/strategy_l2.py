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
    GatedCandidate,
    GateOutcome,
    IncumbentSet,
    SchemaGateResult,
)
from guardmem_core.schemas import GMModel
from guardmem_core.types import CandidateId

__all__ = ["l2_strategies"]


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
    assertions: st.SearchStrategy[object],
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
        IncumbentSet: st.builds(
            IncumbentSet,
            candidate_id=_ID.map(CandidateId),
            nearest=st.lists(assertions, max_size=3),
            neighbours=st.lists(edges, max_size=2),
        ),
    }
