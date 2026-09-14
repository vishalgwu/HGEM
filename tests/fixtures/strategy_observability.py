"""Hypothesis strategies for the observability models.  S5.5

Its own module for the reason `strategy_l3.py` and `strategy_verdict.py` are -
`strategies.py` is the index and sits on `RULES.md` §2.4's cap. One model today;
S13.1's structured-logging and metric surfaces land beside it.

`ChainVerification` is drawn coherently, like `MeaningClusters` and unlike the
Layer 2 models: `broken_at` is defined as "the seq of the first bad link, or
`None` when verified", so the two fields are one fact wearing two names. An
independent draw would produce a verified chain that also named a break point -
a state `verify_chain` cannot return, and one no property could be stated over.
"""

from __future__ import annotations

from hypothesis import strategies as st

from guardmem_core.observability import ChainVerification
from guardmem_core.schemas import GMModel

__all__ = ["OBSERVABILITY_STRATEGIES"]


@st.composite
def _verifications(draw: st.DrawFn) -> ChainVerification:
    """A verdict whose `broken_at` agrees with its `verified`.

    `checked` is drawn as the count of links examined *before* the break, which
    is what `verify_chain` reports - so a broken chain always has at least one
    link and `checked` never exceeds the position it stopped at.
    """
    checked = draw(st.integers(min_value=0, max_value=50))
    if draw(st.booleans()):
        return ChainVerification(verified=True, broken_at=None, checked=checked)
    return ChainVerification(
        verified=False,
        broken_at=draw(st.integers(min_value=checked, max_value=checked + 1000)),
        checked=checked,
    )


OBSERVABILITY_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    ChainVerification: _verifications(),
}
