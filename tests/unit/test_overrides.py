"""The seven hard overrides.  BUILD_NOTEBOOK.md S5.4

S5.4's DONE WHEN asks for "one case per override" and they are
`TestEachOverride`, in §3.4's own numbering. What earns the rest of the file is
the sentence beside them: "obligations compose, they never relax". That is a
property over every pair of (matrix result, override), not a case, so
`TestTheyNeverRelax` drives it exhaustively rather than sampling.

Each case starts from the permissive baseline - a perfectly confident,
zero-risk, unconflicted candidate that auto-writes - and changes exactly one
signal. So a failure names the override that did not fire, and a builder that
forgot to wire a field through shows up as an override that never fires at all.
"""

from __future__ import annotations

import itertools

import pytest

from fixtures.decisions import DEFAULTS, confidence, conflict, risk, signals
from guardmem_core.pipeline.l3_score import MutationType, decide, tighten
from guardmem_core.schemas.policy import ObligationKind
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.risk import ImpactLevel
from guardmem_core.schemas.verdict import Decision, DecisionRecord

# S_cor at two independent sources.
_TWO_SOURCES = 0.5507


def record(
    *,
    c: float = 0.95,
    r: float = 0.10,
    corroboration: float = _TWO_SOURCES,
    impact: ImpactLevel = ImpactLevel.LOW,
    obligations: list[str] | None = None,
    hint: str = "coexist",
    already_escalated: bool = False,
    **signal_overrides: object,
) -> DecisionRecord:
    """`decide()` from the baseline that auto-writes, varying what is named."""
    return decide(
        confidence(c, corroboration=corroboration),
        risk(r, impact=impact, obligations=obligations),
        conflict(hint),
        DEFAULTS,
        already_escalated,
        signals(**signal_overrides),
    )


def run(**kwargs: object) -> Decision:
    """Just the decision."""
    return record(**kwargs).decision  # type: ignore[arg-type]


def test_the_baseline_auto_writes() -> None:
    """The premise every case below rests on.

    Without it, an override test that passed because the baseline was already
    HITL_REVIEW would look identical to one that passed because the override
    fired.
    """
    assert run() == Decision.AUTO_WRITE


class TestEachOverride:
    """§3.4's seven, in its numbering."""

    def test_1_injection_detected_never_auto_writes(self) -> None:
        """§3.4 writes "-> QUARANTINE (never AUTO_WRITE)" and `Decision` has no
        QUARANTINE member.

        `MEMORY_ENGINE.md` §0 declares four outcomes and `PRD.md` FR-3.2 says
        exactly one per candidate, so the quarantine is a *namespace* (§2.1) and
        the decision is REJECT: `RULES.md` §3 treats a canary echo as confirmed
        injection rather than a heuristic, and confirmed-hostile content does
        not need a human to adjudicate it.
        """
        assert run(injection_detected=True) == Decision.REJECT
        assert "OVR_INJECTION_DETECTED" in record(injection_detected=True).reason_codes

    @pytest.mark.parametrize("impact", [ImpactLevel.HIGH, ImpactLevel.CRITICAL])
    def test_2_web_content_never_auto_writes_a_high_impact_predicate(
        self, impact: ImpactLevel
    ) -> None:
        """`RULES.md` §4 states the same cap independently of §3.4."""
        assert run(source_tier=SourceTier.RETRIEVED_WEB, impact=impact) == Decision.HITL_REVIEW

    @pytest.mark.parametrize("impact", [ImpactLevel.LOW, ImpactLevel.MEDIUM])
    def test_2_web_content_below_high_impact_is_untouched(self, impact: ImpactLevel) -> None:
        """The override is scoped, and a `StrEnum` compared with `>=` would
        order these "critical" < "high" < "low" < "medium" and fire on the
        wrong ones without raising."""
        assert run(source_tier=SourceTier.RETRIEVED_WEB, impact=impact) == Decision.AUTO_WRITE

    def test_3_a_predicate_needing_corroboration_gets_a_review(self) -> None:
        assert run(requires_corroboration=True, corroboration=0.0) == Decision.HITL_REVIEW

    def test_3_is_satisfied_by_a_second_source(self) -> None:
        assert run(requires_corroboration=True, corroboration=_TWO_SOURCES) == Decision.AUTO_WRITE

    def test_4_a_conflict_asking_to_escalate_escalates(self) -> None:
        assert run(hint="escalate") == Decision.ESCALATE

    def test_4_becomes_a_review_once_already_escalated(self) -> None:
        """§3.4: "(or HITL if already escalated)"."""
        assert run(hint="escalate", already_escalated=True) == Decision.HITL_REVIEW

    @pytest.mark.parametrize("signal", ["budget_exhausted", "circuit_open"])
    def test_5_degraded_capacity_goes_to_a_human(self, signal: str) -> None:
        """`ARCHITECTURE.md` §0: degradation never widens the auto-write path."""
        assert run(**{signal: True}) == Decision.HITL_REVIEW

    def test_6_a_policy_obligation_gets_a_review(self) -> None:
        obligations = [ObligationKind.REQUIRE_REVIEW.value]

        assert run(obligations=obligations) == Decision.HITL_REVIEW

    def test_6_other_obligations_do_not_fire_this_one(self) -> None:
        """`redact_field` is an obligation on the *write*, not on the decision."""
        assert run(obligations=[ObligationKind.REDACT_FIELD.value]) == Decision.AUTO_WRITE

    def test_7_a_critical_retraction_needs_step_up_auth(self) -> None:
        """§3.4 writes "mutation == delete"; nothing deletes, and `RETRACT` is
        the vocabulary's name for withdrawing a belief.

        "+ step-up auth" rides on the reason code: `DecisionRecord` has nowhere
        else for it, and inventing a fifth `Decision` would be a spec change.
        """
        assert (
            run(impact=ImpactLevel.CRITICAL, mutation=MutationType.RETRACT) == Decision.HITL_REVIEW
        )
        assert (
            "OVR_CRITICAL_RETRACTION_STEP_UP"
            in record(impact=ImpactLevel.CRITICAL, mutation=MutationType.RETRACT).reason_codes
        )

    def test_7_needs_both_halves(self) -> None:
        assert run(impact=ImpactLevel.CRITICAL, mutation=MutationType.SUPERSEDE) != (
            Decision.HITL_REVIEW
        )
        assert run(impact=ImpactLevel.LOW, mutation=MutationType.RETRACT) == Decision.AUTO_WRITE


class TestTheyNeverRelax:
    """§3.4: "obligations compose, they never relax"."""

    @pytest.mark.parametrize(("current", "proposed"), list(itertools.product(Decision, repeat=2)))
    def test_tighten_never_returns_the_more_permissive_of_the_two(
        self, current: Decision, proposed: Decision
    ) -> None:
        """Exhaustive over all sixteen pairs, because this one function is what
        makes "never relax" a property of the code rather than of the care
        taken writing each of the seven rules."""
        order = [Decision.AUTO_WRITE, Decision.ESCALATE, Decision.HITL_REVIEW, Decision.REJECT]
        result = tighten(current, proposed)

        assert order.index(result) == max(order.index(current), order.index(proposed))

    def test_tighten_is_commutative(self) -> None:
        """So the order the seven run in cannot change the answer."""
        for a, b in itertools.product(Decision, repeat=2):
            assert tighten(a, b) == tighten(b, a)

    def test_an_obligation_cannot_pull_a_rejection_up_into_a_review(self) -> None:
        """The consequence of the ordering that is worth stating outright.

        A REJECT is already stricter than a review, so override 6 leaves it
        alone. An obligation tightens; it does not reopen a closed door.
        """
        rejected = run(c=0.20, obligations=[ObligationKind.REQUIRE_REVIEW.value])

        assert rejected == Decision.REJECT

    def test_a_human_beats_a_frontier_model(self) -> None:
        """Override 4 asks for ESCALATE, and a cell already at HITL_REVIEW
        stays there: re-scoring on a bigger model is a weaker gate than a
        person looking at it."""
        assert run(c=0.65, r=0.50, hint="escalate") == Decision.HITL_REVIEW

    def test_every_single_override_only_ever_tightens(self) -> None:
        """Each of the seven, against every matrix cell it could land on.

        The property the module docstring claims, driven rather than asserted:
        turning any one signal on never moves a decision *down* the
        permissiveness order.
        """
        order = [Decision.AUTO_WRITE, Decision.ESCALATE, Decision.HITL_REVIEW, Decision.REJECT]
        switches: list[dict[str, object]] = [
            {"injection_detected": True},
            {"source_tier": SourceTier.RETRIEVED_WEB, "impact": ImpactLevel.CRITICAL},
            {"requires_corroboration": True, "corroboration": 0.0},
            {"hint": "escalate"},
            {"budget_exhausted": True},
            {"circuit_open": True},
            {"obligations": [ObligationKind.REQUIRE_REVIEW.value]},
            {"impact": ImpactLevel.CRITICAL, "mutation": MutationType.RETRACT},
        ]

        for c, r in itertools.product((0.20, 0.50, 0.65, 0.90), (0.10, 0.50, 0.90)):
            plain = run(c=c, r=r)
            for switch in switches:
                assert order.index(run(c=c, r=r, **switch)) >= order.index(plain), (
                    f"{switch} relaxed {plain} at C={c} R={r}"
                )


class TestTheSignalsHaveNoDefaults:
    def test_every_field_must_be_supplied(self) -> None:
        """A benign default on any of these is an override that quietly does
        not fire - and the caller who forgets one gets the *permissive* answer,
        which is `ARCHITECTURE.md` §0's "fail quiet" exactly."""
        from guardmem_core.pipeline.l3_score import OverrideSignals

        with pytest.raises(ValueError, match="Field required"):
            OverrideSignals()  # type: ignore[call-arg]
