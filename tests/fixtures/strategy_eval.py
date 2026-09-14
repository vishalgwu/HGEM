"""Hypothesis strategies for the eval models.  CHECKPOINT B

`Labelled` is drawn loosely - a candidate id, a label and a score map have no
rule between them. `DiscriminationReport` is drawn coherently, like
`MeaningClusters` and `ChainVerification`, because three of its fields are one
fact stated three ways: `verdict` is a band of `auroc`, `auroc` is the
composite's entry in `by_score`, and `kept` cannot exceed `labelled`. An
independent draw produces reports `discriminate` cannot return - a PASS verdict
beside an AUROC of 0.2, or more kept candidates than there were candidates.
"""

from __future__ import annotations

from hypothesis import strategies as st

from fixtures.strategy_primitives import _ID, _UNIT
from guardmem_core.eval import DiscriminationReport, Labelled
from guardmem_core.eval.discrimination import MARGINAL_FLOOR, PASS_FLOOR, Verdict
from guardmem_core.schemas import GMModel

__all__ = ["EVAL_STRATEGIES"]

_SCORES = st.dictionaries(_ID, _UNIT, min_size=1, max_size=4)


def _band(score: float) -> Verdict:
    """The same three bands `_verdict` applies, restated here on purpose.

    A strategy that imported the function under test would agree with it by
    construction, including when it is wrong - so the round-trip property would
    never notice a moved threshold.
    """
    if score >= PASS_FLOOR:
        return Verdict.PASS
    return Verdict.MARGINAL if score >= MARGINAL_FLOOR else Verdict.FAIL


@st.composite
def _reports(draw: st.DrawFn) -> DiscriminationReport:
    """A report whose verdict, AUROC and counts agree with each other."""
    by_score = draw(_SCORES)
    composite = next(iter(by_score))
    labelled = draw(st.integers(min_value=2, max_value=500))
    return DiscriminationReport(
        verdict=_band(by_score[composite]),
        auroc=by_score[composite],
        by_score=by_score,
        labelled=labelled,
        # At least one of each class, which is what `auroc` requires to be
        # defined at all - a report over a one-sided corpus cannot exist.
        kept=draw(st.integers(min_value=1, max_value=labelled - 1)),
    )


EVAL_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    Labelled: st.builds(Labelled, candidate_id=_ID, keep=st.booleans(), scores=_SCORES),
    DiscriminationReport: _reports(),
}
