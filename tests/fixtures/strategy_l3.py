"""Hypothesis strategies for the Layer 3 models.  S5.1

Its own module for the reason `strategy_l2.py` and `strategy_ontology.py` are:
`strategies.py` sits exactly on `RULES.md` §2.4's 400-line cap, and the registry
there should stay a readable index rather than the place every new model's
generator lands.

`MeaningClusters` is drawn *coherently* rather than from three independent
fields, which is the opposite of the choice `strategy_l2.py` makes and the
reason is the same one stated backwards. There, a loose generator is harmless
because the looseness cannot contradict a field's own declaration. Here it can:
`entropy` is bounded `[0, 1]` and `minority` is defined as "the indices whose
label is not `labels[0]`", so an independent draw produces objects the module
cannot emit *and* that no property could be stated over - a `minority` naming
index 0 makes the round-trip test pass while describing something impossible.
So the labels are drawn first and the other two are derived from them, exactly
as `cluster_meanings` derives them.
"""

from __future__ import annotations

import math
from collections import Counter

from hypothesis import strategies as st

from fixtures.strategy_primitives import _ID, _UNIT
from guardmem_core.pipeline.l3_score import (
    ConfidenceWeights,
    MeaningClusters,
    MutationType,
    OverrideSignals,
    RiskBetas,
    RiskFeatures,
)
from guardmem_core.schemas import GMModel
from guardmem_core.schemas.receipt import SourceTier

__all__ = ["L3_STRATEGIES"]


def _clusters(labels: list[int]) -> MeaningClusters:
    """Build the model `cluster_meanings` would build from these labels."""
    size = len(labels)
    entropy = 0.0
    if size > 1:
        counts = Counter(labels)
        probabilities = [count / size for count in counts.values()]
        raw = -sum(p * math.log(p) for p in probabilities)
        entropy = min(1.0, max(0.0, raw / math.log(size)))
    return MeaningClusters(
        labels=labels,
        entropy=entropy,
        minority=[index for index, label in enumerate(labels) if label != labels[0]],
    )


@st.composite
def _meaning_clusters(draw: st.DrawFn) -> MeaningClusters:
    """Draw a partition the way union-find can actually produce one.

    Each sample either starts its own cluster or joins one that already has a
    root, so the label of index `i` is always `i` itself or the root of an
    earlier index - never a middle member. `[0, 0, 1]` is unreachable, because
    index 1 is in cluster 0 and cannot also be a root, and generating it would
    describe a state `cluster_meanings` has no way to return.

    Constructed rather than drawn-and-repaired: a filter or a fixup pass here
    would be a second implementation of `_find` living in the test fixtures,
    and the first attempt at one was wrong.
    """
    size = draw(st.integers(min_value=1, max_value=5))
    labels: list[int] = []
    for index in range(size):
        labels.append(draw(st.sampled_from(sorted({*labels, index}))))
    return _clusters(labels)


@st.composite
def _confidence_weights(draw: st.DrawFn) -> ConfidenceWeights:
    """Draw five weights that sum to 1.

    `ConfidenceWeights` validates the sum, so an independent draw across five
    `[0, 1]` floats is rejected for all but a measure-zero set of examples -
    hypothesis would spend its budget being filtered out. Drawn as five
    non-negative shares and normalised instead, which is the same construction
    argument `_meaning_clusters` makes: generate what the model accepts rather
    than generate-and-discard.

    Integer shares so the division is over exact values; the result still only
    sums to 1 within float tolerance, which is what the validator allows for.
    """
    shares = [draw(st.integers(min_value=0, max_value=100)) for _ in range(5)]
    if not any(shares):
        shares[draw(st.integers(min_value=0, max_value=4))] = 1
    total = sum(shares)
    uncertainty, grounding, schema_fit, corroboration, consistency = (s / total for s in shares)
    return ConfidenceWeights(
        uncertainty=uncertainty,
        grounding=grounding,
        schema_fit=schema_fit,
        corroboration=corroboration,
        consistency=consistency,
        version=draw(st.text(min_size=1, max_size=8)),
    )


# The eight features, drawn independently - unlike the two above, nothing here
# is derived from anything else, and §3.3 bounds each one at [0, 1] on its own.
_RISK_FEATURES = st.builds(
    RiskFeatures,
    impact_declared=_UNIT,
    mutation_type=_UNIT,
    scope=_UNIT,
    graph_fanout=_UNIT,
    pii_class=_UNIT,
    irreversibility=_UNIT,
    source_tier_risk=_UNIT,
    novelty=_UNIT,
)

# Logistic-regression coefficients, so deliberately *not* `_UNIT`: they do not
# sum to anything, and `threshold_tuner.py` refitting from reviewer labels may
# legitimately return a negative one. Bounded only enough to keep `z` in a range
# the sigmoid can express, which is what a real fit would produce anyway.
_BETA = st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)
_RISK_BETAS = st.builds(
    RiskBetas,
    impact_declared=_BETA,
    mutation_type=_BETA,
    scope=_BETA,
    graph_fanout=_BETA,
    pii_class=_BETA,
    irreversibility=_BETA,
    source_tier_risk=_BETA,
    novelty=_BETA,
    bias=_BETA,
    version=_ID,
)

# Every combination of the seven overrides' inputs. Independent, because these
# are seven unrelated facts about a candidate and nothing constrains one by
# another - which is also why `OverrideSignals` gives none of them a default.
_OVERRIDE_SIGNALS = st.builds(
    OverrideSignals,
    injection_detected=st.booleans(),
    source_tier=st.sampled_from(SourceTier),
    mutation=st.sampled_from(MutationType),
    requires_corroboration=st.booleans(),
    budget_exhausted=st.booleans(),
    circuit_open=st.booleans(),
    policy_version=_ID,
)

L3_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    OverrideSignals: _OVERRIDE_SIGNALS,
    MeaningClusters: _meaning_clusters(),
    ConfidenceWeights: _confidence_weights(),
    RiskFeatures: _RISK_FEATURES,
    RiskBetas: _RISK_BETAS,
}
