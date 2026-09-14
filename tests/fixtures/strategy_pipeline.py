"""Hypothesis strategies for the orchestrator's own models.  S5.6

`Proposal`, `PipelineResult` and `CandidateRisk` - the three models S5.6 added
that are not a layer's output. Its own module for the reason every other
`strategy_*.py` is: `strategies.py` is the index and sits on `RULES.md` §2.4's
cap.

All three are drawn loosely, like `strategy_l2.py` and unlike `strategy_l3.py`.
Nothing here has a cross-field rule: a `PipelineResult` whose `decisions` do not
correspond to its `quarantined` ids is still a well-formed result, and pinning
that correspondence is `tests/unit/test_orchestrator.py`'s work, not a
serialisation property's.
"""

from __future__ import annotations

from hypothesis import strategies as st

from fixtures.strategy_primitives import _ID, _NAMESPACES, _TENANT_IDS, _TRACE_IDS
from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.deps import CandidateRisk
from guardmem_core.pipeline.l3_score import Irreversibility, PiiClass, Scope
from guardmem_core.pipeline.orchestrator import PipelineResult, Proposal
from guardmem_core.schemas import GMModel
from guardmem_core.schemas.receipt import SourceTier

__all__ = ["pipeline_strategies"]


_CANDIDATE_RISKS = st.builds(
    CandidateRisk,
    scope=st.sampled_from(Scope),
    pii_class=st.sampled_from(PiiClass),
    irreversibility=st.sampled_from(Irreversibility),
)


def pipeline_strategies(
    turns: st.SearchStrategy[object], records: st.SearchStrategy[object]
) -> dict[type[GMModel], st.SearchStrategy[GMModel]]:
    """Build the registry entries, given the generators these compose.

    Takes `turns` and `records` as arguments rather than importing them, for the
    reason `l2_strategies` does: they live in `strategies.py`, and importing
    them back would make the two modules import each other.
    """
    return {
        CandidateRisk: _CANDIDATE_RISKS,
        Proposal: st.builds(
            Proposal,
            trace_id=_TRACE_IDS,
            tenant_id=_TENANT_IDS,
            namespace=_NAMESPACES,
            turns=st.lists(turns, max_size=3),
            source_tier=st.sampled_from(SourceTier),
            # §1.2 draws 1, 3 or 5 by risk hint. Drawn wider than that here -
            # the model declares a plain `int` and this suite tests the model,
            # not the policy that picks the number.
            k=st.integers(min_value=1, max_value=5),
            tier=st.sampled_from(Tier),
            subject_hint=st.none() | _ID,
        ),
        PipelineResult: st.builds(
            PipelineResult,
            trace_id=_TRACE_IDS,
            decisions=st.lists(records, max_size=2),
            quarantined=st.lists(_ID, max_size=3),
            rejected=st.lists(_ID, max_size=3),
            dropped_noise=st.integers(min_value=0, max_value=50),
            dropped_unsourced=st.integers(min_value=0, max_value=50),
        ),
    }
