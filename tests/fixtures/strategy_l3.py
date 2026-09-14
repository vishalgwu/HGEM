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

from guardmem_core.pipeline.l3_score import MeaningClusters
from guardmem_core.schemas import GMModel

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


L3_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    MeaningClusters: _meaning_clusters(),
}
