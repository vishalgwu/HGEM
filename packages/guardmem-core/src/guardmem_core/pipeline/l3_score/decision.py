"""The decision matrix.  BUILD_NOTEBOOK.md S5.4

`MEMORY_ENGINE.md` §3.4's twelve cells, then `overrides.py`'s seven rules, then
the one clamp that stops an escalation recursing.

**Pure, total, deterministic - invariant I4.** No I/O, no clock, no randomness,
no settings read: every threshold arrives as a `Thresholds` value and every flag
as data. That is what makes `scripts/replay_trace.py` possible, and it is the
reason S5.4's "Do NOT" list says not to add "a settings read, not a clock call,
not a feature flag lookup" to this function.

**Bands are half-open and §3.4's own table disagrees with itself about `R`.**
The prose says "a band's lower bound is inclusive, its upper bound exclusive"
and then, specifically of confidence, "`C` exactly at a threshold falls in the
higher band". The matrix's column headers are drawn `<= rho_lo` and `> rho_hi`,
which puts `R` exactly on a threshold in the *lower* band - the opposite. Read
uniformly here, so `R = rho_lo` is the middle column and `R = rho_hi` is the
right-hand one. Two reasons: the general sentence is the one stated as a rule,
and every disagreement lands on the stricter cell. At `C >= tau_hi` the choice
is AUTO_WRITE against AUTO_WRITE-if-corroborated at `rho_lo`, and
AUTO_WRITE-if-corroborated against HITL_REVIEW at `rho_hi`.

**The asterisk and the dagger.** §3.4 marks the middle-risk, high-confidence
cell "AUTO_WRITE * only if corroboration >= 2" and says nothing about what it is
otherwise; HITL_REVIEW, since the cell beside it is HITL_REVIEW and nothing in
§3.4 rejects on risk alone. The dagger on the bottom-right cell - "CRITICAL
impact: a human sees even the rejections" - reads as a gloss on why that cell is
HITL_REVIEW rather than REJECT, not as a condition, so the cell is HITL_REVIEW
for every impact level.

**"corroboration >= 2" is a count, and the report carries a score.**
`ConfidenceReport.corroboration` is §3.2's `S_cor`, not `n_sources`, and nothing
in `decide()`'s inputs carries the count. They are recoverable from each other:
`S_cor = 1 - exp(-0.8(n-1))` is exactly 0.0 at one source and strictly positive
above it. So the test is `S_cor >= corroboration(2)`, written against S5.2's own
function rather than against a literal, so a change to lambda moves both
together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core.pipeline.l3_score.confidence import corroboration
from guardmem_core.pipeline.l3_score.overrides import OverrideSignals, apply_overrides
from guardmem_core.schemas.verdict import Decision, DecisionRecord

if TYPE_CHECKING:
    from guardmem_core.schemas.verdict import (
        ConfidenceReport,
        ConflictReport,
        RiskVerdict,
        Thresholds,
    )

__all__ = ["OverrideSignals", "decide"]

# What `S_cor` reads at exactly two independent sources. Computed from S5.2's
# function rather than written as 0.5507, so the two cannot drift.
_CORROBORATED: Final = corroboration(2)

# Reason codes for the band a candidate landed in, in `MEMORY_ENGINE.md` §0's
# style. Two per decision - one per axis - so the record says which side of the
# matrix put it there.
_C_BANDS: Final = (
    "C_BELOW_TAU_LO",
    "C_IN_TAU_LO_BAND",
    "C_IN_TAU_MID_BAND",
    "C_AT_OR_ABOVE_TAU_HI",
)
_R_BANDS: Final = ("R_BELOW_RHO_LO", "R_IN_RHO_BAND", "R_AT_OR_ABOVE_RHO_HI")

# §3.4's grid, indexed `[confidence band][risk band]`, both ascending. `None` is
# the starred cell: AUTO_WRITE when at least two sources corroborate, and
# HITL_REVIEW when they do not.
_MATRIX: Final[tuple[tuple[Decision | None, ...], ...]] = (
    # C < tau_lo
    (Decision.REJECT, Decision.REJECT, Decision.HITL_REVIEW),
    # tau_lo <= C < tau_mid
    (Decision.ESCALATE, Decision.ESCALATE, Decision.HITL_REVIEW),
    # tau_mid <= C < tau_hi
    (Decision.AUTO_WRITE, Decision.HITL_REVIEW, Decision.HITL_REVIEW),
    # C >= tau_hi
    (Decision.AUTO_WRITE, None, Decision.HITL_REVIEW),
)

# The starred cell's two outcomes.
_STARRED_CORROBORATED: Final = Decision.AUTO_WRITE
_STARRED_ALONE: Final = Decision.HITL_REVIEW
_STARRED_CODE: Final = "CORROBORATION_BELOW_TWO"

# Fired when `already_escalated` stops the matrix's ESCALATE from repeating.
_NO_SECOND_ESCALATION: Final = "ESCALATION_NOT_REPEATABLE"


def decide(
    conf: ConfidenceReport,
    risk: RiskVerdict,
    conflict: ConflictReport,
    thresholds: Thresholds,
    already_escalated: bool,
    signals: OverrideSignals,
) -> DecisionRecord:
    """Turn three verdicts into one decision.  §3.4

    Args:
        conf: Layer 3's confidence composite.
        risk: Layer 3's impact verdict, carrying the obligations.
        conflict: Layer 2's finding.
        thresholds: The five cut points and their version. Never read from
            settings here - `Settings.thresholds()` is the seam.
        already_escalated: Whether this candidate has been round the FRONTIER
            once. §3.4: the escalated result "cannot escalate again".
        signals: The context the four non-matrix overrides need. See
            `OverrideSignals` on why this parameter exists at all.

    Returns:
        The full `DecisionRecord`, carrying every input's report, the reason
        codes that explain it, and both versions in force.

    Total over `C, R` in `[0, 1]^2` and deterministic - invariant I4. Every
    `(C, R)` lands in exactly one cell because the bands tile the square, and
    nothing below consults anything outside its arguments.
    """
    confidence_band = _confidence_band(conf.confidence, thresholds)
    risk_band = _risk_band(risk.risk, thresholds)
    corroborated = conf.corroboration >= _CORROBORATED

    decision, codes = _from_matrix(confidence_band, risk_band, corroborated=corroborated)
    decision, override_codes = apply_overrides(
        decision,
        conf=conf,
        risk=risk,
        conflict=conflict,
        signals=signals,
        already_escalated=already_escalated,
        corroborated=corroborated,
    )
    codes.extend(override_codes)

    if already_escalated and decision is Decision.ESCALATE:
        # S5.4's DONE WHEN: "`already_escalated=True` never returns ESCALATE".
        # HITL_REVIEW rather than a re-score, because §3.4 resolves an escalated
        # result to "AUTO_WRITE, HITL_REVIEW, or REJECT" and the matrix's
        # ESCALATE cells are the middling-confidence band - exactly where a
        # human is the remaining answer. Applied after the overrides because
        # override 4 can introduce an ESCALATE of its own.
        decision = Decision.HITL_REVIEW
        codes.append(_NO_SECOND_ESCALATION)

    return DecisionRecord(
        decision=decision,
        reason_codes=codes,
        confidence=conf,
        risk=risk,
        conflict=conflict,
        thresholds_version=thresholds.version,
        policy_version=signals.policy_version,
        # `DecisionRecord.escalated_from` is "the decision this one replaced
        # after a FRONTIER re-score", and the only decision an escalated
        # candidate can have been is ESCALATE - `already_escalated` is a bool,
        # so there is nothing else to record and nothing else it could be.
        escalated_from=Decision.ESCALATE if already_escalated else None,
    )


def _confidence_band(confidence: float, thresholds: Thresholds) -> int:
    """Which of §3.4's four confidence rows this `C` falls in, ascending.

    Half-open upward: a `C` exactly on a threshold belongs to the higher band,
    which §3.4 states in as many words.
    """
    if confidence >= thresholds.tau_hi:
        return 3
    if confidence >= thresholds.tau_mid:
        return 2
    if confidence >= thresholds.tau_lo:
        return 1
    return 0


def _risk_band(risk: float, thresholds: Thresholds) -> int:
    """Which of §3.4's three risk columns this `R` falls in, ascending.

    The same half-open reading as `_confidence_band`, which is where §3.4's
    table and its prose disagree - see the module docstring.
    """
    if risk >= thresholds.rho_hi:
        return 2
    if risk >= thresholds.rho_lo:
        return 1
    return 0


def _from_matrix(
    confidence_band: int, risk_band: int, *, corroborated: bool
) -> tuple[Decision, list[str]]:
    """Read one cell, resolving the starred one.

    Args:
        confidence_band: Row, 0 to 3 ascending.
        risk_band: Column, 0 to 2 ascending.
        corroborated: Whether two or more independent sources back the claim.

    Returns:
        The cell's decision and the reason codes naming both bands - plus
        `CORROBORATION_BELOW_TWO` when that is what turned the starred cell into
        a review.
    """
    codes = [_C_BANDS[confidence_band], _R_BANDS[risk_band]]
    cell = _MATRIX[confidence_band][risk_band]
    if cell is not None:
        return cell, codes
    if corroborated:
        return _STARRED_CORROBORATED, codes
    codes.append(_STARRED_CODE)
    return _STARRED_ALONE, codes
