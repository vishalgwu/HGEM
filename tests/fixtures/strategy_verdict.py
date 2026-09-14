"""Hypothesis strategies for `schemas/verdict.py`.  S5.1

Split out when `strategies.py` crossed `RULES.md` §2.4's 400-line cap on the
line that registered Layer 3. The seam is the one this directory already
draws - `strategy_ontology.py`, `strategy_l2.py`, `strategy_l3.py` - one
strategy module per source module, with `strategies.py` left as the index.

These four move together because `DecisionRecord` composes the other three:
`MEMORY_ENGINE.md` §0 makes it the full replayable record of one decision, so
splitting them would put half of one generator in another file.

Loose on purpose, like `strategy_l2.py` and unlike `strategy_l3.py` - with one
exception. `Thresholds` enforces `tau_lo < tau_mid < tau_hi` and
`rho_lo < rho_hi`, so it is drawn sorted rather than filtered: independent
triples satisfy that ordering about one time in six, and a filter would spend
most of hypothesis's budget discarding examples and then warn about it.

Everything else here has no cross-field rule to violate. A `DecisionRecord` whose `reason_codes`
do not explain its `decision` is still a well-formed record, and pinning that
correspondence is `tests/unit/` work at S5.4 - not something a serialisation
property should be asserting.
"""

from __future__ import annotations

from hypothesis import strategies as st

from fixtures.strategy_primitives import (
    _ANY_FLOAT,
    _ASSERTION_IDS,
    _COSINE,
    _ID,
    _TEXT,
    _UNIT,
)
from guardmem_core.schemas import GMModel
from guardmem_core.schemas.policy import ObligationKind
from guardmem_core.schemas.verdict import (
    ConfidenceReport,
    ConflictKind,
    ConflictReport,
    Decision,
    DecisionRecord,
    ImpactLevel,
    RiskVerdict,
    Thresholds,
)

__all__ = ["DECISION_RECORDS", "VERDICT_STRATEGIES"]

_CONFIDENCE = st.builds(
    ConfidenceReport,
    semantic_entropy=_UNIT,
    grounding=_UNIT,
    schema_fit=_UNIT,
    corroboration=_UNIT,
    consistency=_UNIT,
    confidence=_UNIT,
    weights_version=_ID,
)

_RISK = st.builds(
    RiskVerdict,
    impact_level=st.sampled_from(ImpactLevel),
    risk=_UNIT,
    features=st.dictionaries(st.text(max_size=16), _ANY_FLOAT, max_size=4),
    obligations=st.lists(st.sampled_from([k.value for k in ObligationKind]), max_size=3),
)

_CONFLICT = st.builds(
    ConflictReport,
    kind=st.sampled_from(ConflictKind),
    incumbent_assertion_id=st.none() | _ASSERTION_IDS,
    entailment=_UNIT,
    contradiction=_UNIT,
    cosine=_COSINE,
    resolution_hint=st.sampled_from(["merge", "supersede", "coexist", "escalate"]),
)


@st.composite
def _thresholds(draw: st.DrawFn) -> Thresholds:
    """Five cut points in the order the matrix needs them."""
    taus = sorted(draw(st.lists(_UNIT, min_size=3, max_size=3, unique=True)))
    rhos = sorted(draw(st.lists(_UNIT, min_size=2, max_size=2, unique=True)))
    return Thresholds(
        tau_lo=taus[0],
        tau_mid=taus[1],
        tau_hi=taus[2],
        rho_lo=rhos[0],
        rho_hi=rhos[1],
        version=draw(_ID),
    )


# Named rather than inlined below, because `strategy_pipeline.py` composes it
# into `PipelineResult` - a decision record is what the orchestrator produces,
# so the two generators have to agree about its shape.
DECISION_RECORDS = st.builds(
    DecisionRecord,
    decision=st.sampled_from(Decision),
    reason_codes=st.lists(_TEXT, max_size=4),
    confidence=_CONFIDENCE,
    risk=_RISK,
    conflict=_CONFLICT,
    thresholds_version=_ID,
    policy_version=_ID,
    escalated_from=st.none() | st.sampled_from(Decision),
)

VERDICT_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    Thresholds: _thresholds(),
    ConfidenceReport: _CONFIDENCE,
    RiskVerdict: _RISK,
    ConflictReport: _CONFLICT,
    DecisionRecord: DECISION_RECORDS,
}
