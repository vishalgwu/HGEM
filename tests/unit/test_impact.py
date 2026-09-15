"""The impact-risk model.  BUILD_NOTEBOOK.md S5.3

S5.3's DONE WHEN: "a CRITICAL-impact candidate with perfect confidence still
scores `R >= 0.80`. That test is the whole point of separating `C` from `R`."
It is `TestTheFloorIsTheSafetyProperty` below, and it is written to show *why*
it passes: the linear score for that candidate is about 0.26, so the assertion
would fail without the floor rather than passing for free.

`impact_features.py`'s vocabularies and conversions are
`tests/unit/test_impact_features.py`; this module is the linear model, the
squash and the floor.
"""

from __future__ import annotations

from typing import Final

import pytest

from guardmem_core.pipeline.l3_score import V1_BETAS, RiskBetas, RiskFeatures, score_impact
from guardmem_core.schemas.risk import ImpactLevel

_FIELDS: Final = (
    "impact_declared",
    "mutation_type",
    "scope",
    "graph_fanout",
    "pii_class",
    "irreversibility",
    "source_tier_risk",
    "novelty",
)


def features(**overrides: float) -> RiskFeatures:
    """All eight at zero unless named."""
    return RiskFeatures.model_validate(dict.fromkeys(_FIELDS, 0.0) | overrides)


class TestTheFloorIsTheSafetyProperty:
    """S5.3's DONE WHEN, and §3.3's opening sentence.

    "Risk is *not* the inverse of confidence. A perfectly-confident write to the
    account-owner field is still a high-risk operation."
    """

    def test_a_critical_candidate_with_perfect_confidence_still_scores_high(self) -> None:
        """The assertion the step asks for.

        "Perfect confidence" is not an input here - `C` and `R` are separate
        axes and `score_impact` never sees confidence. What it means in feature
        terms is the most benign candidate there is: nothing mutated, nothing
        personal, nothing irreversible, a trusted source, an unremarkable claim
        in a private namespace. Only the declared impact is critical.
        """
        benign_but_critical = features(impact_declared=1.0, scope=0.1)

        verdict = score_impact(benign_but_critical, ImpactLevel.CRITICAL)

        assert verdict.risk >= 0.80

    def test_and_it_would_fail_without_the_floor(self) -> None:
        """The other half, which is what makes the test above mean something.

        The linear score for that candidate is about 0.26. If the floor were
        dropped, the DONE WHEN would fail rather than pass by luck - so this
        pins that the floor is load-bearing and not decoration.
        """
        benign_but_critical = features(impact_declared=1.0, scope=0.1)

        # The same features under LOW, where the floor is 0.15 and cannot bite.
        unfloored = score_impact(benign_but_critical, ImpactLevel.LOW)

        assert unfloored.risk == pytest.approx(0.255, abs=0.005)
        assert unfloored.risk < 0.80

    @pytest.mark.parametrize(
        ("level", "floor"),
        [
            (ImpactLevel.LOW, 0.15),
            (ImpactLevel.MEDIUM, 0.35),
            (ImpactLevel.HIGH, 0.60),
            (ImpactLevel.CRITICAL, 0.80),
        ],
    )
    def test_every_level_has_its_floor(self, level: ImpactLevel, floor: float) -> None:
        """§3.3: "floors: low .15, medium .35, high .60, critical .80"."""
        assert score_impact(features(), level).risk == pytest.approx(floor)

    def test_the_floor_never_lowers_a_score(self) -> None:
        """`max`, not a clamp. A LOW-impact write that is dangerous on every
        other axis keeps its high score - the floor raises, never caps."""
        dangerous = features(**dict.fromkeys(_FIELDS, 1.0))

        verdict = score_impact(dangerous, ImpactLevel.LOW)

        assert verdict.risk > 0.99

    def test_the_declared_level_is_carried_onto_the_verdict(self) -> None:
        assert score_impact(features(), ImpactLevel.HIGH).impact_level is ImpactLevel.HIGH


class TestTheBetas:
    def test_they_are_the_ones_the_spec_states(self) -> None:
        """§3.3's table, transcribed."""
        assert (
            V1_BETAS.impact_declared,
            V1_BETAS.mutation_type,
            V1_BETAS.scope,
            V1_BETAS.graph_fanout,
            V1_BETAS.pii_class,
            V1_BETAS.irreversibility,
            V1_BETAS.source_tier_risk,
            V1_BETAS.novelty,
            V1_BETAS.bias,
        ) == (2.20, 1.60, 1.30, 0.90, 1.10, 1.40, 1.00, 0.50, -3.40)

    def test_the_bias_puts_a_neutral_candidate_near_zero(self) -> None:
        """Without it every feature at zero would score 0.5 - the midpoint of
        the scale - and every threshold would be expressed around that.

        Shown as a contrast, because `sigmoid(-3.4)` is 0.032 and the LOW floor
        of 0.15 hides it: the same neutral candidate under a zero-bias fit
        scores 0.5 and is not floored at all.
        """
        neutral = score_impact(features(), ImpactLevel.LOW)
        unbiased = score_impact(
            features(), ImpactLevel.LOW, betas=V1_BETAS.model_copy(update={"bias": 0.0})
        )

        assert unbiased.risk == pytest.approx(0.5)
        assert neutral.risk == pytest.approx(0.15), "sigmoid(-3.4) = 0.032, floored to 0.15"

    @pytest.mark.parametrize("field", _FIELDS)
    def test_every_feature_raises_risk_on_its_own(self, field: str) -> None:
        """Each beta is positive in v1, so no feature can lower `R`.

        Run per feature rather than in aggregate, so a beta that lost its sign
        in a refit names itself instead of being masked by the other seven.

        Measured from a mid-range baseline, not from all-zero. At all-zero
        `z` is -3.4 and a single feature rarely lifts `sigmoid(z)` past even
        the LOW floor of 0.15, so the floor - not the beta - decides, and
        seven of these eight cases passed for the wrong reason first time
        round. From 0.5 across the board `z` is 1.6 and the floor cannot
        reach.
        """
        baseline = dict.fromkeys(_FIELDS, 0.5)
        quiet = score_impact(features(**baseline), ImpactLevel.LOW)
        loud = score_impact(features(**{**baseline, field: 1.0}), ImpactLevel.LOW)

        assert quiet.risk > ImpactLevel.LOW.risk_floor, "the floor must not be deciding this"
        assert loud.risk > quiet.risk, f"{field} did not raise R"

    def test_a_refit_can_be_supplied(self) -> None:
        """§3.3 has `threshold_tuner.py` refit these weekly, so they are an
        argument rather than a module constant."""
        flat = RiskBetas(
            impact_declared=0.0,
            mutation_type=0.0,
            scope=0.0,
            graph_fanout=0.0,
            pii_class=0.0,
            irreversibility=0.0,
            source_tier_risk=0.0,
            novelty=0.0,
            bias=0.0,
            version="flat",
        )

        verdict = score_impact(features(impact_declared=1.0), ImpactLevel.LOW, betas=flat)

        assert verdict.risk == pytest.approx(0.5), "a zero bias puts sigmoid at its midpoint"


class TestTheSquash:
    def test_an_extreme_negative_score_does_not_overflow(self) -> None:
        """`1 / (1 + exp(-z))` raises `OverflowError` once `-z` passes ~710.

        One refit beta of the wrong sign on a feature at 1.0 is all it would
        take, and the failure would be an exception out of a pure scoring
        function rather than a number. The two-branch form cannot overflow.
        """
        huge = RiskBetas(
            impact_declared=-800.0,
            mutation_type=0.0,
            scope=0.0,
            graph_fanout=0.0,
            pii_class=0.0,
            irreversibility=0.0,
            source_tier_risk=0.0,
            novelty=0.0,
            bias=0.0,
            version="pathological",
        )

        verdict = score_impact(features(impact_declared=1.0), ImpactLevel.LOW, betas=huge)

        assert verdict.risk == 0.15, "floored, having squashed to ~0 without raising"

    def test_an_extreme_positive_score_saturates_below_one(self) -> None:
        assert score_impact(features(**dict.fromkeys(_FIELDS, 1.0)), ImpactLevel.LOW).risk > 0.99

    def test_risk_stays_inside_the_unit_interval(self) -> None:
        """`RiskVerdict.risk` declares `[0, 1]` and S5.4's thresholds are
        expressed against it."""
        for value in (0.0, 0.5, 1.0):
            for level in ImpactLevel:
                assert (
                    0.0
                    <= score_impact(features(**dict.fromkeys(_FIELDS, value)), level).risk
                    <= 1.0
                )


class TestWhatTheVerdictCarries:
    def test_the_features_are_persisted_verbatim(self) -> None:
        """§3.3 asks for this twice: so the review UI can show *why* something
        was flagged, and so the tuner can refit beta from reviewer labels."""
        given = features(impact_declared=0.66, pii_class=0.8, novelty=0.25)

        persisted = score_impact(given, ImpactLevel.HIGH).features

        assert persisted == given.model_dump()
        assert persisted["pii_class"] == 0.8

    def test_all_eight_are_present_under_the_spec_s_names(self) -> None:
        """The keys are the vocabulary `threshold_tuner.py` refits against, so
        a renamed one drops a feature from the refit with nothing raising."""
        assert sorted(score_impact(features(), ImpactLevel.LOW).features) == sorted(_FIELDS)

    def test_a_feature_outside_the_unit_interval_is_refused(self) -> None:
        """Caught at `RiskFeatures` rather than pushed through the sigmoid,
        which would squash it into a plausible-looking number."""
        with pytest.raises(ValueError, match="less than or equal to 1"):
            features(novelty=1.4)

    def test_an_unknown_obligation_is_refused(self) -> None:
        """`RiskVerdict`'s own validator: S5.4 composes the obligations it knows
        and ignores the rest, so a typo would leave the auto-write path open
        with nothing in the record to show for it."""
        with pytest.raises(ValueError, match="unknown obligations"):
            score_impact(features(), ImpactLevel.LOW, obligations=["require_reviewi"])

    def test_it_is_deterministic(self) -> None:
        """Invariant I4's precondition - `decide()` composes this."""
        given = features(impact_declared=0.66, scope=0.5, novelty=0.3)

        assert score_impact(given, ImpactLevel.HIGH) == score_impact(given, ImpactLevel.HIGH)
