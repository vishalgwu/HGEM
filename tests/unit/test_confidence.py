"""The confidence composite.  BUILD_NOTEBOOK.md S5.2

S5.2's DONE WHEN: "unit tests pin each term independently; a test asserts
weights sum to 1.0." Both are here - `TestEachTermIndependently` zeroes the
other four and checks that raising one moves `C` by exactly its weight, which
is the only arrangement that pins a term *independently* rather than pinning
the sum and hoping.

The step also states a check of its own - `S_cor` returning 0.0 / 0.55 / 0.80 /
0.91 for one to four sources - and that is
`test_the_four_values_the_step_states`.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Final

import pytest

from guardmem_core.pipeline.l3_score import (
    V1_WEIGHTS,
    ConfidenceWeights,
    consistency,
    corroboration,
    grounding,
    score_confidence,
)
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.verdict import ConfidenceReport, ConflictKind, ConflictReport

# Every weight on one term, so `C` *is* that term. See the class docstring.
_ALL_WEIGHT: Final = {
    name: ConfidenceWeights.model_validate(
        {
            "uncertainty": 0.0,
            "grounding": 0.0,
            "schema_fit": 0.0,
            "corroboration": 0.0,
            "consistency": 0.0,
            name: 1.0,
            "version": f"test-{name}",
        }
    )
    for name in ("uncertainty", "grounding", "schema_fit", "corroboration", "consistency")
}


def report(
    kind: ConflictKind = ConflictKind.NONE,
    contradiction: float = 0.0,
    hint: str = "coexist",
) -> ConflictReport:
    """A `ConflictReport` carrying just what `S_con` reads."""
    return ConflictReport.model_validate(
        {
            "kind": kind,
            "incumbent_assertion_id": None,
            "entailment": 0.0,
            "contradiction": contradiction,
            "cosine": 0.0,
            "resolution_hint": hint,
        }
    )


def score(
    *,
    entropy: float = 0.0,
    span_entailment: float = 1.0,
    tier: SourceTier = SourceTier.TRUSTED_SYSTEM,
    alignment: float = 1.0,
    schema_fit: float = 1.0,
    sources: int = 1,
    conflict: ConflictReport | None = None,
    weights: ConfidenceWeights = V1_WEIGHTS,
) -> ConfidenceReport:
    """`score_confidence` with every term at a neutral default.

    Spelled out rather than taking `**overrides`, so a test that misnames an
    argument fails at the type checker instead of silently scoring the default.
    """
    return score_confidence(
        entropy=entropy,
        span_entailment=span_entailment,
        tier=tier,
        alignment=alignment,
        schema_fit=schema_fit,
        sources=sources,
        conflict=conflict if conflict is not None else report(),
        weights=weights,
    )


class TestTheWeights:
    def test_the_v1_weights_sum_to_one(self) -> None:
        """S5.2's DONE WHEN, and §3.2 writes "sum = 1" into the spec."""
        total = (
            V1_WEIGHTS.uncertainty
            + V1_WEIGHTS.grounding
            + V1_WEIGHTS.schema_fit
            + V1_WEIGHTS.corroboration
            + V1_WEIGHTS.consistency
        )

        assert total == pytest.approx(1.0)

    def test_the_v1_weights_are_the_ones_the_step_states(self) -> None:
        """S5.2: "v1 weights are 0.35 / 0.25 / 0.10 / 0.15 / 0.15"."""
        assert (
            V1_WEIGHTS.uncertainty,
            V1_WEIGHTS.grounding,
            V1_WEIGHTS.schema_fit,
            V1_WEIGHTS.corroboration,
            V1_WEIGHTS.consistency,
        ) == (0.35, 0.25, 0.10, 0.15, 0.15)

    @pytest.mark.parametrize("total", [0.9, 1.1])
    def test_a_set_that_does_not_sum_to_one_is_refused(self, total: float) -> None:
        """Both directions fail quietly, which is why this raises.

        Over 1.0 produces a `C` above 1.0 on some inputs and not others; under
        1.0 caps confidence below every threshold it is compared against, and
        nothing raises anywhere.
        """
        # Spread across two fields, because a single weight of 1.1 is caught
        # by that field's own `le=1.0` bound and would never reach the
        # cross-field validator this test is about.
        with pytest.raises(ValueError, match="sum to"):
            ConfidenceWeights(
                uncertainty=total - 0.5,
                grounding=0.5,
                schema_fit=0.0,
                corroboration=0.0,
                consistency=0.0,
                version="bad",
            )

    def test_the_version_lands_on_the_report(self) -> None:
        """S5.2: "you will change these weights and need to know which
        decisions used which"."""
        assert score().weights_version == "v1"


class TestEachTermIndependently:
    """The DONE WHEN's first half.

    Each case puts the whole weight on one term and zeroes the rest, so `C`
    equals that term exactly. A test that varied one term under the *real*
    weights would move `C` by 0.35 or 0.10 and pass just as well against a
    composite that had two terms transposed.
    """

    def test_uncertainty_enters_flipped(self) -> None:
        """§3.2 uses `1 - H_norm`, because `H_norm` is uncertainty.

        The transposition this catches is the one that would look right: a
        composite using `H_norm` directly scores a confident extraction as
        doubtful and a wildly inconsistent one as certain.
        """
        assert score(entropy=0.0, weights=_ALL_WEIGHT["uncertainty"]).confidence == 1.0
        assert score(entropy=1.0, weights=_ALL_WEIGHT["uncertainty"]).confidence == 0.0

    def test_the_stored_entropy_is_the_uncertainty_not_the_contribution(self) -> None:
        """`ConfidenceReport.semantic_entropy` has to agree with S5.1's output
        under the same name, or the persisted breakdown contradicts itself."""
        assert score(entropy=0.75).semantic_entropy == 0.75

    def test_grounding_is_the_product_of_its_three_inputs(self) -> None:
        scored = score(
            span_entailment=0.8,
            tier=SourceTier.RETRIEVED_WEB,
            alignment=0.5,
            weights=_ALL_WEIGHT["grounding"],
        )

        assert scored.confidence == pytest.approx(0.8 * 0.6 * 0.5)

    def test_schema_fit_is_carried_not_recomputed(self) -> None:
        """S4.1 already computes §3.2's scale - 1.0 exact, 0.7 coerced, 0.4
        unknown - so a second implementation here is a second thing to drift."""
        for fit in (1.0, 0.7, 0.4):
            assert score(schema_fit=fit, weights=_ALL_WEIGHT["schema_fit"]).confidence == fit
            assert score(schema_fit=fit).schema_fit == fit

    def test_corroboration_enters_at_its_own_scale(self) -> None:
        assert score(sources=3, weights=_ALL_WEIGHT["corroboration"]).confidence == pytest.approx(
            0.80, abs=0.005
        )

    def test_consistency_enters_at_its_own_scale(self) -> None:
        scored = score(conflict=report(contradiction=0.3), weights=_ALL_WEIGHT["consistency"])

        assert scored.confidence == pytest.approx(0.7)


class TestCorroboration:
    """`S_cor = 1 - exp(-0.8(n - 1))`."""

    def test_the_four_values_the_step_states(self) -> None:
        """S5.2: "Check `S_cor` returns 0.0 / 0.55 / 0.80 / 0.91 for 1 / 2 / 3 /
        4 sources"."""
        assert [round(corroboration(n), 2) for n in (1, 2, 3, 4)] == [0.0, 0.55, 0.80, 0.91]

    def test_one_source_is_no_corroboration_at_all(self) -> None:
        """Exactly zero, not nearly. A fact stated once is uncorroborated, and
        a floor above zero would hand every candidate a free contribution."""
        assert corroboration(1) == 0.0

    def test_the_curve_is_concave(self) -> None:
        """The second source buys more than the fourth, which is the shape
        corroboration has: two independent witnesses is the big step."""
        steps = [corroboration(n + 1) - corroboration(n) for n in range(1, 6)]

        assert all(earlier > later for earlier, later in pairwise(steps))

    def test_it_stays_below_one_for_the_counts_a_fact_actually_has(self) -> None:
        """Asymptotic, so counting alone never fully corroborates a claim."""
        assert all(corroboration(n) < 1.0 for n in range(1, 21))

    def test_it_saturates_at_forty_eight_sources(self) -> None:
        """Measured, not estimated, and recorded because the first estimate was
        wrong by an order of magnitude.

        `1 - exp(-0.8(n-1))` reaches exactly 1.0 in float64 at **n = 48** -
        where the remainder falls below the epsilon of 1.0, not where `exp`
        underflows, which is far later. Harmless: 1.0 is inside the declared
        bound and 48 independent sources for one claim *is* maximal
        corroboration. Pinned so the saturation is a known property rather than
        something rediscovered as a bug.
        """
        assert corroboration(47) < 1.0
        assert corroboration(48) == 1.0

    def test_zero_sources_is_refused(self) -> None:
        """It would return a negative contribution - something no other term
        can produce - and drag `C` down rather than merely not raising it."""
        with pytest.raises(ValueError, match="at least one source"):
            corroboration(0)


class TestGrounding:
    """`S_src`: does the cited span support the claim?"""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (SourceTier.TRUSTED_SYSTEM, 1.0),
            (SourceTier.VERIFIED_USER, 0.95),
            (SourceTier.UNVERIFIED_USER, 0.8),
            (SourceTier.TOOL_OUTPUT, 0.85),
            (SourceTier.RETRIEVED_WEB, 0.6),
        ],
    )
    def test_the_multipliers_are_the_spec_s(self, tier: SourceTier, expected: float) -> None:
        assert grounding(1.0, tier, 1.0) == pytest.approx(expected)

    def test_a_tool_output_grounds_better_than_an_unverified_human(self) -> None:
        """Pinned because it inverts `RULES.md` §4's trust ordering, and a
        reader who assumes one ordering governs both will 'fix' it.

        §4 ranks `UNVERIFIED_USER` above `TOOL_OUTPUT` for *authority* - what
        may auto-write. §3.2 scores a tool output higher for *grounding* - how
        literally the span supports the claim. Both are deliberate.
        """
        assert grounding(1.0, SourceTier.TOOL_OUTPUT, 1.0) > grounding(
            1.0, SourceTier.UNVERIFIED_USER, 1.0
        )
        assert SourceTier.UNVERIFIED_USER.at_least(SourceTier.TOOL_OUTPUT)

    def test_an_exact_quote_is_not_penalised(self) -> None:
        """§3.2 applies the fuzzy penalty "if span alignment < 1.0", so 1.0
        must be a no-op."""
        assert grounding(1.0, SourceTier.TRUSTED_SYSTEM, 1.0) == 1.0

    def test_a_looser_quote_never_scores_higher(self) -> None:
        scores = [grounding(1.0, SourceTier.TRUSTED_SYSTEM, a) for a in (1.0, 0.95, 0.9, 0.5)]

        assert all(a >= b for a, b in pairwise(scores))

    def test_a_span_that_does_not_support_the_claim_grounds_at_zero(self) -> None:
        """The whole point of the term: a perfect quote from a trusted source
        that does not entail the claim is not evidence for the claim."""
        assert grounding(0.0, SourceTier.TRUSTED_SYSTEM, 1.0) == 0.0


class TestConsistency:
    """`S_con`: how this sits with what is already believed."""

    def test_it_is_one_minus_the_contradiction(self) -> None:
        assert consistency(report(contradiction=0.42)) == pytest.approx(0.58)

    def test_a_novel_fact_is_perfectly_consistent(self) -> None:
        """Nothing to disagree with. This is the case where a `contradiction`
        of 0.0 genuinely means agreement."""
        assert consistency(report()) == 1.0

    def test_a_refinement_is_not_penalised(self) -> None:
        """§3.2: "`REFINEMENT` scores 0.9 rather than penalizing"."""
        assert consistency(report(ConflictKind.REFINEMENT, hint="supersede")) == 0.9

    @pytest.mark.parametrize("kind", [ConflictKind.CARDINALITY, ConflictKind.TEMPORAL_OVERLAP])
    def test_a_conflict_settled_without_a_judge_scores_zero(self, kind: ConflictKind) -> None:
        """The departure from §3.2, and the reason for it.

        These two are decided by arithmetic over the ontology and the clock, so
        `conflict.py` writes `contradiction = 0.0` because nothing measured it -
        deliberately, since "a fabricated 0.9 would read as a measurement".
        Read literally, `1 - contra` then awards a *perfect* consistency score
        to the one kind of candidate that definitionally clashes with live
        memory. That zero is an absence of measurement, not a measurement of
        absence.
        """
        assert consistency(report(kind, hint="supersede")) == 0.0

    def test_a_cardinality_clash_scores_below_a_novel_fact(self) -> None:
        """Stated as the comparison rather than the constant, because the
        constant is not the point - the ordering is."""
        clash = score(conflict=report(ConflictKind.CARDINALITY, hint="supersede"))
        novel = score(conflict=report())

        assert clash.confidence < novel.confidence


class TestTheCompositeAsAWhole:
    def test_a_perfect_candidate_with_one_source_is_not_perfect(self) -> None:
        """`S_cor` is 0.0 at one source, so `C` caps at `1 - w_c` = 0.85 however
        good everything else is. That is the corroboration term doing its job,
        and it is worth pinning so nobody later reads 0.85 as a bug.
        """
        assert score().confidence == pytest.approx(0.85)

    def test_the_worst_candidate_scores_zero(self) -> None:
        scored = score(
            entropy=1.0,
            span_entailment=0.0,
            schema_fit=0.0,
            sources=1,
            conflict=report(ConflictKind.CARDINALITY, hint="supersede"),
        )

        assert scored.confidence == 0.0

    def test_it_stays_inside_the_unit_interval(self) -> None:
        """`ConfidenceReport.confidence` declares `[0, 1]` and S5.4's
        thresholds are expressed against it."""
        for entropy in (0.0, 0.5, 1.0):
            for sources in (1, 4):
                assert 0.0 <= score(entropy=entropy, sources=sources).confidence <= 1.0

    def test_each_weight_is_applied_to_its_own_term(self) -> None:
        """Every term at a *different* value, checked against the arithmetic.

        The gap this closes: the other composite tests leave grounding and
        schema_fit both at 1.0, so transposing `w_g` and `w_s` changes nothing
        and only the weights-are-the-spec's test notices. Here the five terms
        are five distinct numbers, so any pairing of a weight with the wrong
        term moves `C`. A mutation run is what showed the gap.
        """
        scored = score(
            entropy=0.2,  # (1 - H) = 0.8, w_H 0.35 -> 0.280
            span_entailment=0.6,  # S_src = 0.6, w_g 0.25 -> 0.150
            schema_fit=0.4,  # S_sch = 0.4, w_s 0.10 -> 0.040
            sources=2,  # S_cor ~ 0.5507, w_c 0.15 -> 0.0826
            conflict=report(contradiction=0.1),  # S_con = 0.9, w_k 0.15 -> 0.135
        )

        assert scored.confidence == pytest.approx(
            0.35 * 0.8 + 0.25 * 0.6 + 0.10 * 0.4 + 0.15 * corroboration(2) + 0.15 * 0.9
        )
        assert scored.confidence == pytest.approx(0.6876, abs=0.0005)

    def test_it_is_deterministic(self) -> None:
        """Invariant I4's precondition, and what makes replay possible."""
        assert score(entropy=0.3, sources=2) == score(entropy=0.3, sources=2)

    def test_every_term_is_persisted_not_just_the_composite(self) -> None:
        """§3.2: the review UI renders the breakdown and the tuner refits from
        it, and a single scalar makes both impossible."""
        scored = score(entropy=0.25, span_entailment=0.9, alignment=0.9, schema_fit=0.7, sources=2)

        assert scored.semantic_entropy == 0.25
        assert scored.grounding == pytest.approx(0.81)
        assert scored.schema_fit == 0.7
        assert scored.corroboration == pytest.approx(0.55, abs=0.005)
        assert scored.consistency == 1.0

    def test_a_term_outside_the_unit_interval_is_refused(self) -> None:
        """`ConfidenceReport`'s own bounds catch a `span_entailment` of 1.4
        rather than letting it inflate `C` quietly."""
        with pytest.raises(ValueError, match="less than or equal to 1"):
            score(span_entailment=1.4)
