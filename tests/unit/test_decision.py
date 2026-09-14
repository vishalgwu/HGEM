"""The decision matrix.  BUILD_NOTEBOOK.md S5.4

S5.4's DONE WHEN asks for three things. Two are here: "a table-driven test with
one case per matrix cell" (`TestEveryCell`, all twelve, plus both halves of the
starred one) and "escalation cannot recurse: `already_escalated=True` never
returns ESCALATE" (`TestEscalationDoesNotRecurse`). The overrides are
`test_overrides.py` and invariant I4 is
`tests/property/test_i4_decision_totality.py`.

The band-boundary cases are here too, and they are the ones worth reading:
`MEMORY_ENGINE.md` §3.4's prose and its table disagree about which side of a
risk threshold a value falls on, and these pin the reading `decision.py` took.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from fixtures.decisions import DEFAULTS, confidence, conflict, risk, signals
from guardmem_core.pipeline.l3_score import decide
from guardmem_core.schemas.verdict import Decision

# Representative values inside each band, given the defaults (0.45 / 0.60 / 0.78
# and 0.35 / 0.70). Named so the table below reads as §3.4's does.
_C_REJECT, _C_ESCALATE, _C_MID, _C_HIGH = 0.20, 0.50, 0.65, 0.90
_R_LOW, _R_MID, _R_HIGH = 0.10, 0.50, 0.90

# S_cor at two independent sources is 0.5507; anything at or above it reads as
# corroborated. See `decision.py` on why this is derived rather than a literal.
_TWO_SOURCES = 0.5507


def run(
    *,
    c: float = 0.95,
    r: float = 0.10,
    corroboration: float = 0.0,
    hint: str = "coexist",
    already_escalated: bool = False,
    **signal_overrides: object,
) -> Decision:
    """`decide()` from the permissive baseline, varying what is named."""
    return decide(
        confidence(c, corroboration=corroboration),
        risk(r),
        conflict(hint),
        DEFAULTS,
        already_escalated,
        signals(**signal_overrides),
    ).decision


class TestEveryCell:
    """S5.4: "a table-driven test with one case per matrix cell"."""

    @pytest.mark.parametrize(
        ("c", "r", "expected"),
        [
            # C >= tau_hi. The middle cell is starred and gets its own tests.
            (_C_HIGH, _R_LOW, Decision.AUTO_WRITE),
            (_C_HIGH, _R_HIGH, Decision.HITL_REVIEW),
            # tau_mid <= C < tau_hi
            (_C_MID, _R_LOW, Decision.AUTO_WRITE),
            (_C_MID, _R_MID, Decision.HITL_REVIEW),
            (_C_MID, _R_HIGH, Decision.HITL_REVIEW),
            # tau_lo <= C < tau_mid
            (_C_ESCALATE, _R_LOW, Decision.ESCALATE),
            (_C_ESCALATE, _R_MID, Decision.ESCALATE),
            (_C_ESCALATE, _R_HIGH, Decision.HITL_REVIEW),
            # C < tau_lo
            (_C_REJECT, _R_LOW, Decision.REJECT),
            (_C_REJECT, _R_MID, Decision.REJECT),
            (_C_REJECT, _R_HIGH, Decision.HITL_REVIEW),
        ],
    )
    def test_cell(self, c: float, r: float, expected: Decision) -> None:
        assert run(c=c, r=r) == expected

    def test_the_starred_cell_auto_writes_when_corroborated(self) -> None:
        """§3.4: "AUTO_WRITE * only if corroboration >= 2"."""
        assert run(c=_C_HIGH, r=_R_MID, corroboration=_TWO_SOURCES) == Decision.AUTO_WRITE

    def test_the_starred_cell_is_a_review_on_a_single_source(self) -> None:
        """§3.4 does not say what the cell is when the asterisk is unmet.

        HITL_REVIEW, because the cell beside it is HITL_REVIEW and nothing in
        §3.4 rejects on risk alone. Recorded as a reading rather than a
        transcription.
        """
        assert run(c=_C_HIGH, r=_R_MID, corroboration=0.0) == Decision.HITL_REVIEW

    def test_the_bottom_right_cell_reviews_rather_than_rejects(self) -> None:
        """The dagger: "CRITICAL impact: a human sees even the rejections".

        Read as a gloss on why the cell is HITL_REVIEW, not as a condition on
        it - so the cell is a review at every impact level, not only CRITICAL.
        """
        assert run(c=_C_REJECT, r=_R_HIGH) == Decision.HITL_REVIEW


class TestTheBandsAreHalfOpen:
    """§3.4: "a band's lower bound is inclusive, its upper bound exclusive"."""

    @pytest.mark.parametrize(
        ("c", "expected"),
        [
            (0.78, Decision.AUTO_WRITE),  # exactly tau_hi -> the higher band
            (0.7799, Decision.AUTO_WRITE),  # tau_mid band, still auto at low R
            (0.60, Decision.AUTO_WRITE),  # exactly tau_mid
            (0.5999, Decision.ESCALATE),  # just below -> escalate band
            (0.45, Decision.ESCALATE),  # exactly tau_lo
            (0.4499, Decision.REJECT),  # just below
        ],
    )
    def test_confidence_on_a_threshold_falls_in_the_higher_band(
        self, c: float, expected: Decision
    ) -> None:
        """§3.4 says this of `C` in as many words."""
        assert run(c=c, r=_R_LOW) == expected

    def test_confidence_exactly_on_tau_hi_reaches_the_top_row(self) -> None:
        """The boundary that the low-risk column cannot see.

        At `R` below `rho_lo` the top two rows are both AUTO_WRITE, so making
        `tau_hi` exclusive changes nothing there and a boundary test written at
        low risk passes either way - which is how the first version of this
        suite let that mutant live. In the middle column the rows differ:
        the top one auto-writes when corroborated, the one below always
        reviews. So this is where `C == tau_hi` has to be checked.
        """
        assert run(c=0.78, r=_R_MID, corroboration=_TWO_SOURCES) == Decision.AUTO_WRITE
        assert run(c=0.7799, r=_R_MID, corroboration=_TWO_SOURCES) == Decision.HITL_REVIEW

    def test_confidence_exactly_on_tau_mid_reaches_the_auto_write_row(self) -> None:
        """`tau_mid`'s boundary is visible in the low-risk column, where the
        band above auto-writes and the band below escalates."""
        assert run(c=0.60, r=_R_LOW) == Decision.AUTO_WRITE
        assert run(c=0.5999, r=_R_LOW) == Decision.ESCALATE

    def test_confidence_exactly_on_tau_lo_escapes_rejection(self) -> None:
        assert run(c=0.45, r=_R_LOW) == Decision.ESCALATE
        assert run(c=0.4499, r=_R_LOW) == Decision.REJECT

    def test_risk_exactly_on_rho_lo_is_the_middle_column(self) -> None:
        """Where §3.4's table and its prose disagree.

        The column header is drawn `<= rho_lo`, which would put 0.35 in the
        low-risk column and auto-write it. The prose's general rule - lower
        bound inclusive, upper exclusive - puts it in the middle column, where
        the starred cell needs corroboration. `decision.py` follows the prose,
        and this is the case that tells them apart.
        """
        assert run(c=_C_HIGH, r=0.35, corroboration=0.0) == Decision.HITL_REVIEW
        assert run(c=_C_HIGH, r=0.3499, corroboration=0.0) == Decision.AUTO_WRITE

    def test_risk_exactly_on_rho_hi_is_the_high_column(self) -> None:
        """The same disagreement at the other threshold. Drawn `> rho_hi`,
        which would leave 0.70 in the middle column."""
        assert run(c=_C_HIGH, r=0.70, corroboration=_TWO_SOURCES) == Decision.HITL_REVIEW
        assert run(c=_C_HIGH, r=0.6999, corroboration=_TWO_SOURCES) == Decision.AUTO_WRITE

    def test_the_disagreement_always_resolves_to_the_stricter_cell(self) -> None:
        """Why that reading was chosen where the spec is ambiguous."""
        strict = run(c=_C_HIGH, r=0.35, corroboration=0.0)
        loose = run(c=_C_HIGH, r=0.3499, corroboration=0.0)

        assert (strict, loose) == (Decision.HITL_REVIEW, Decision.AUTO_WRITE)


class TestEscalationDoesNotRecurse:
    """S5.4's third DONE WHEN, stated as the step states it."""

    @pytest.mark.parametrize("c", [_C_REJECT, _C_ESCALATE, _C_MID, _C_HIGH])
    @pytest.mark.parametrize("r", [_R_LOW, _R_MID, _R_HIGH])
    @pytest.mark.parametrize("hint", ["coexist", "escalate", "merge", "supersede"])
    def test_already_escalated_never_returns_escalate(self, c: float, r: float, hint: str) -> None:
        """Across every cell and every conflict hint, not only the obvious one.

        Two separate paths can produce an ESCALATE - the matrix's middle band
        and override 4 - so a clamp covering one of them would pass a narrower
        test.
        """
        assert run(c=c, r=r, hint=hint, already_escalated=True) != Decision.ESCALATE

    def test_an_escalated_middling_candidate_goes_to_a_human(self) -> None:
        """§3.4: the escalated result "resolves to AUTO_WRITE, HITL_REVIEW, or
        REJECT". The matrix's ESCALATE cells are the middling-confidence band,
        which is exactly where a human is the remaining answer."""
        assert run(c=_C_ESCALATE, r=_R_LOW, already_escalated=True) == Decision.HITL_REVIEW

    def test_a_first_pass_still_escalates(self) -> None:
        """The clamp must not fire when it should not."""
        assert run(c=_C_ESCALATE, r=_R_LOW, already_escalated=False) == Decision.ESCALATE

    def test_the_record_says_what_it_replaced(self) -> None:
        """`escalated_from` is "the decision this one replaced after a FRONTIER
        re-score", and ESCALATE is the only thing it could have been."""
        escalated = decide(
            confidence(_C_ESCALATE),
            risk(),
            conflict(),
            DEFAULTS,
            True,
            signals(),
        )
        first_pass = decide(confidence(_C_ESCALATE), risk(), conflict(), DEFAULTS, False, signals())

        assert escalated.escalated_from is Decision.ESCALATE
        assert first_pass.escalated_from is None


class TestWhatTheRecordCarries:
    def test_it_names_both_bands(self) -> None:
        """`PRD.md` FR-3.4 requires machine-readable rationale. One code per
        axis, so the record says which side of the matrix decided."""
        record = decide(confidence(_C_REJECT), risk(_R_LOW), conflict(), DEFAULTS, False, signals())

        assert "C_BELOW_TAU_LO" in record.reason_codes
        assert "R_BELOW_RHO_LO" in record.reason_codes

    def test_the_starred_cell_says_why_it_was_downgraded(self) -> None:
        record = decide(confidence(_C_HIGH), risk(_R_MID), conflict(), DEFAULTS, False, signals())

        assert "CORROBORATION_BELOW_TWO" in record.reason_codes

    def test_both_versions_are_recorded(self) -> None:
        """`PRD.md` FR-3.3 makes a threshold change an audited event, so the
        record has to name the set that produced it."""
        record = decide(confidence(), risk(), conflict(), DEFAULTS, False, signals())

        assert record.thresholds_version == "test-v1"
        assert record.policy_version == "test-policy"

    def test_all_three_reports_are_carried(self) -> None:
        """What makes `scripts/replay_trace.py` possible: the record holds
        every input the decision was taken from."""
        conf, verdict, finding = confidence(), risk(), conflict()

        record = decide(conf, verdict, finding, DEFAULTS, False, signals())

        assert (record.confidence, record.risk, record.conflict) == (conf, verdict, finding)

    def test_it_is_deterministic(self) -> None:
        """Invariant I4, pinned here as well as in the property suite - this is
        the cheap version that runs on every commit."""
        args = (confidence(0.5), risk(0.5), conflict(), DEFAULTS, False, signals())

        assert decide(*args) == decide(*args)


class TestItReadsNothingButItsArguments:
    def test_neither_module_imports_settings_at_all(self) -> None:
        """S5.4's "Do NOT" list: no settings read, no clock call, no feature
        flag lookup.

        Checked statically as well as behaviourally, because
        `test_the_thresholds_govern_rather_than_the_defaults` below only proves
        the *thresholds* are not read from settings. A feature flag or a
        timeout pulled in later would pass that test and break replay just as
        thoroughly - `scripts/replay_trace.py` cannot reproduce a decision that
        consulted a value the record does not carry.
        """
        from guardmem_core.pipeline.l3_score import decision, overrides

        for module in (decision, overrides):
            tree = ast.parse(inspect.getsource(module))
            imported = {
                node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            offenders = {
                name
                for name in imported
                if "settings" in name or name in {"time", "datetime", "random", "os"}
            }
            assert not offenders, f"{module.__name__} imports {sorted(offenders)}"

    def test_the_thresholds_govern_rather_than_the_defaults(self) -> None:
        """§3.4: the five are "passed in as a `Thresholds` value, never read
        from settings inside the function".

        The same candidate under a stricter set gets a stricter answer. If
        `decide()` read settings, this would return AUTO_WRITE both times.
        """
        strict = DEFAULTS.model_copy(update={"tau_hi": 0.99, "tau_mid": 0.98, "tau_lo": 0.97})

        assert run(c=0.90, r=_R_LOW) == Decision.AUTO_WRITE
        assert (
            decide(confidence(0.90), risk(_R_LOW), conflict(), strict, False, signals()).decision
            == Decision.REJECT
        )
