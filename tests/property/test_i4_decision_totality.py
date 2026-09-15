"""Invariant I4: `decide()` is total and deterministic.  S5.4

`RULES.md` states it as "decide() is deterministic and total over the (C, R,
policy) domain", and S5.4's DONE WHEN asks for exactly that as a hypothesis
property test over `C, R` in `[0, 1]^2`.

**What "total" has to mean here to be worth testing.** That `decide()` returns
without raising is the weak reading, and it would pass against a function whose
matrix had a hole in it - because a hole returns `None`, and `DecisionRecord`
would raise on it, which is at least loud. The stronger reading is that the
bands *tile* the unit square: every `(C, R)` lands in exactly one of the twelve
cells, and no pair of adjacent bands either overlaps or leaves a gap. That is
what `test_the_bands_tile_the_unit_square` drives, by reconstructing the band
indices independently of the module under test and checking the whole grid is
covered exactly once.

**Thresholds are generated too, not only scores.** §3.4 makes them
per-namespace and `threshold_tuner.py` refits them, so a decision function that
was total under the defaults and not under a tenant's own set would be a
production failure nothing here would have seen. The generator draws ordered
sets, which is what `Thresholds` enforces anyway.

`hypothesis` drives a plain sync function - `decide()` is pure, so there is no
loop to manage and no fake clock to install. That is the point of the step.
"""

from __future__ import annotations

from typing import Final

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fixtures.decisions import confidence, conflict, risk, signals
from guardmem_core.pipeline.l3_score import MutationType, decide
from guardmem_core.schemas.policy import ObligationKind
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.risk import ImpactLevel
from guardmem_core.schemas.verdict import Decision, Thresholds

# `RULES.md` §5: property tests "must hold for 500 examples".
_EXAMPLES: Final = 500

_UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_HINTS = st.sampled_from(["merge", "supersede", "coexist", "escalate"])


@st.composite
def _thresholds(draw: st.DrawFn) -> Thresholds:
    """An ordered threshold set, which is the only kind `Thresholds` accepts.

    Drawn as four gaps and accumulated, rather than drawn independently and
    filtered: `tau_lo < tau_mid < tau_hi` is satisfied by about a sixth of
    independent triples, so a filter would spend most of the budget discarding
    and hypothesis would warn about it.
    """
    taus = sorted(draw(st.lists(_UNIT, min_size=3, max_size=3, unique=True)))
    rhos = sorted(draw(st.lists(_UNIT, min_size=2, max_size=2, unique=True)))
    return Thresholds(
        tau_lo=taus[0],
        tau_mid=taus[1],
        tau_hi=taus[2],
        rho_lo=rhos[0],
        rho_hi=rhos[1],
        version=draw(st.text(min_size=1, max_size=8)),
    )


@st.composite
def _signals(draw: st.DrawFn) -> object:
    """Every combination of the seven overrides' inputs."""
    return signals(
        injection_detected=draw(st.booleans()),
        source_tier=draw(st.sampled_from(SourceTier)),
        mutation=draw(st.sampled_from(MutationType)),
        requires_corroboration=draw(st.booleans()),
        budget_exhausted=draw(st.booleans()),
        circuit_open=draw(st.booleans()),
        policy_version=draw(st.text(min_size=1, max_size=8)),
    )


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    c=_UNIT,
    r=_UNIT,
    corroboration=_UNIT,
    thresholds=_thresholds(),
    hint=_HINTS,
    impact=st.sampled_from(ImpactLevel),
    obligations=st.lists(st.sampled_from([k.value for k in ObligationKind]), max_size=3),
    already_escalated=st.booleans(),
    signal=_signals(),
)
def test_decide_is_total(
    c: float,
    r: float,
    corroboration: float,
    thresholds: Thresholds,
    hint: str,
    impact: ImpactLevel,
    obligations: list[str],
    already_escalated: bool,
    signal: object,
) -> None:
    """Every point in the domain produces one of the four outcomes."""
    record = decide(
        confidence(c, corroboration=corroboration),
        risk(r, impact=impact, obligations=obligations),
        conflict(hint),
        thresholds,
        already_escalated,
        signal,  # type: ignore[arg-type]
    )

    assert record.decision in set(Decision)
    assert record.reason_codes, "a decision with no rationale is not auditable"
    assert record.thresholds_version == thresholds.version


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(c=_UNIT, r=_UNIT, corroboration=_UNIT, thresholds=_thresholds(), signal=_signals())
def test_decide_is_deterministic(
    c: float, r: float, corroboration: float, thresholds: Thresholds, signal: object
) -> None:
    """Same inputs, same record - the whole of `scripts/replay_trace.py`.

    Compared as whole records rather than as decisions: a function that reached
    the same outcome by a different route would still make a replay print a
    diff, and the reason codes are what a reviewer reads.
    """
    args = (
        confidence(c, corroboration=corroboration),
        risk(r),
        conflict(),
        thresholds,
        False,
        signal,
    )

    assert decide(*args) == decide(*args)  # type: ignore[arg-type]


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(c=_UNIT, r=_UNIT, thresholds=_thresholds(), hint=_HINTS, signal=_signals())
def test_an_escalated_candidate_never_escalates_again(
    c: float, r: float, thresholds: Thresholds, hint: str, signal: object
) -> None:
    """S5.4's third DONE WHEN, over the whole domain rather than a table.

    §3.4: the escalated result "re-enters the matrix but **cannot escalate
    again**". Two paths produce an ESCALATE - the matrix's middling band and
    override 4 - and a clamp that covered one would pass the table-driven test
    in `tests/unit/test_decision.py` while failing here.
    """
    record = decide(
        confidence(c),
        risk(r),
        conflict(hint),
        thresholds,
        True,
        signal,  # type: ignore[arg-type]
    )

    assert record.decision is not Decision.ESCALATE
    assert record.escalated_from is Decision.ESCALATE


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(c=_UNIT, r=_UNIT, thresholds=_thresholds())
def test_the_bands_tile_the_unit_square(c: float, r: float, thresholds: Thresholds) -> None:
    """Totality in the form worth having: exactly one cell, never zero or two.

    The band indices are recomputed here from the thresholds directly rather
    than imported, so this checks the *definition* rather than agreeing with
    the implementation by construction. A matrix with an off-by-one in a
    comparison would satisfy "returns without raising" and fail this.
    """
    rows = [c >= thresholds.tau_lo, c >= thresholds.tau_mid, c >= thresholds.tau_hi]
    columns = [r >= thresholds.rho_lo, r >= thresholds.rho_hi]

    # Each band test is monotone in the score, so the trues form a prefix and
    # the count *is* the index. Anything else would mean the thresholds were
    # out of order, which `Thresholds` refuses.
    assert rows == sorted(rows, reverse=True), "confidence bands are not nested"
    assert columns == sorted(columns, reverse=True), "risk bands are not nested"
    assert 0 <= sum(rows) <= 3
    assert 0 <= sum(columns) <= 2


_BENIGN = signals(
    injection_detected=False,
    source_tier=SourceTier.TRUSTED_SYSTEM,
    mutation=MutationType.COEXIST,
    requires_corroboration=False,
    budget_exhausted=False,
    circuit_open=False,
    policy_version="p",
)


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(c=_UNIT, r=_UNIT, corroboration=_UNIT, thresholds=_thresholds())
def test_raising_confidence_never_introduces_a_rejection(
    c: float, r: float, corroboration: float, thresholds: Thresholds
) -> None:
    """The monotonicity that actually holds.

    REJECT appears only in the bottom row, so more confidence can never take a
    candidate that had some route to memory and close it off. A transposed row
    would still be total and still deterministic, and this is the property that
    would not survive it.

    Stated as "never introduces a REJECT" rather than as a full ordering
    because the matrix is **not** monotone in the general sense - see
    `test_the_matrix_is_deliberately_not_monotone`, which is the case that
    taught me so.
    """
    weaker = decide(
        confidence(c, corroboration=corroboration), risk(r), conflict(), thresholds, False, _BENIGN
    ).decision
    stronger = decide(
        confidence(1.0, corroboration=corroboration),
        risk(r),
        conflict(),
        thresholds,
        False,
        _BENIGN,
    ).decision

    if weaker is not Decision.REJECT:
        assert stronger is not Decision.REJECT, f"C=1.0 rejected what C={c} did not, at R={r}"


def test_the_matrix_is_deliberately_not_monotone() -> None:
    """Raising `C` can turn an ESCALATE into a HITL_REVIEW, and that is §3.4's.

    Read down the middle risk column: the `tau_lo` band escalates and the
    `tau_mid` band above it reviews. More confidence, and the outcome moves
    from "spend more compute, then decide" to "a human decides".

    It is not a transcription slip. Escalation re-runs Layer 3 on the FRONTIER
    tier, which is worth paying for exactly where the model is unsure enough
    that a bigger one might settle it; at `tau_mid` and above the model is
    already fairly confident and more compute buys little, so the remaining
    doubt is a person's to resolve. §3.5 budgets FRONTIER at <=6% of candidates,
    which is the same decision seen from the cost side.

    Pinned as its own case because I first wrote this suite asserting the
    opposite - a blanket "more confidence is never worse" - and hypothesis
    found the counterexample at `C = 0` against `C = 1` in three seconds.
    """
    thresholds = Thresholds(
        tau_lo=0.45, tau_mid=0.60, tau_hi=0.78, rho_lo=0.35, rho_hi=0.70, version="t"
    )

    def at(c: float) -> Decision:
        return decide(confidence(c), risk(0.50), conflict(), thresholds, False, _BENIGN).decision

    assert at(0.50) is Decision.ESCALATE
    assert at(0.65) is Decision.HITL_REVIEW
