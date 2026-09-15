"""The eight features, and where each comes from.  BUILD_NOTEBOOK.md S5.3

`tests/unit/test_impact.py` holds the linear model; this holds §3.3's left-hand
column - the four vocabularies and the four conversions. Split the same way the
modules are, because these change when the domain gains a PII class and the
betas change when the tuner refits.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest

from guardmem_core.pipeline.l3_score import (
    Irreversibility,
    MutationType,
    PiiClass,
    Scope,
    graph_fanout,
    novelty,
    source_tier_risk,
)
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.risk import ImpactLevel
from guardmem_core.schemas.verdict import ConflictKind


class TestTheVocabularies:
    """Each mapping transcribed from §3.3's table."""

    def test_impact_declared(self) -> None:
        """ "ontology impact mapped {0,.33,.66,1}"."""
        assert [level.risk_feature for level in ImpactLevel] == [0.0, 0.33, 0.66, 1.0]

    def test_impact_feature_is_not_impact_floor(self) -> None:
        """Two mappings of one enum, and conflating them is the easy mistake.

        The feature is an *input* to the linear score, traded off against seven
        others at beta 2.20. The floor is applied afterwards and traded off
        against nothing. A critical write is expensive twice over, and it has
        to be both.
        """
        assert ImpactLevel.CRITICAL.risk_feature == 1.0
        assert ImpactLevel.CRITICAL.risk_floor == 0.80
        assert all(level.risk_feature != level.risk_floor for level in ImpactLevel)

    def test_scope(self) -> None:
        """ "session .1, user .5, org 1.0 (shared blast radius)"."""
        assert [s.risk_feature for s in Scope] == [0.1, 0.5, 1.0]

    def test_pii_class(self) -> None:
        """ "none 0, quasi-identifier .5, direct .8, special-category 1"."""
        assert [p.risk_feature for p in PiiClass] == [0.0, 0.5, 0.8, 1.0]

    def test_irreversibility(self) -> None:
        """ "can this be practically undone downstream? {0,.5,1}"."""
        assert [i.risk_feature for i in Irreversibility] == [0.0, 0.5, 1.0]

    def test_mutation_type(self) -> None:
        """ "coexist 0, refine .3, supersede .7, delete/retract 1"."""
        assert [m.risk_feature for m in MutationType] == [0.0, 0.3, 0.7, 1.0]

    def test_every_vocabulary_lands_inside_the_unit_interval(self) -> None:
        """`RiskFeatures` bounds all eight at [0, 1], so a vocabulary that
        strayed would fail at construction rather than here - but it would fail
        on some candidates and not others."""
        every = [*ImpactLevel, *Scope, *PiiClass, *Irreversibility, *MutationType]

        assert all(0.0 <= member.risk_feature <= 1.0 for member in every)


class TestMutationFromConflict:
    """Layer 2's finding to §3.3's mutation scale."""

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (ConflictKind.NONE, MutationType.COEXIST),
            (ConflictKind.DUPLICATE, MutationType.COEXIST),
            (ConflictKind.REFINEMENT, MutationType.REFINE),
            (ConflictKind.CONTRADICTION, MutationType.SUPERSEDE),
            (ConflictKind.CARDINALITY, MutationType.SUPERSEDE),
            (ConflictKind.TEMPORAL_OVERLAP, MutationType.SUPERSEDE),
        ],
    )
    def test_each_kind_maps(self, kind: ConflictKind, expected: MutationType) -> None:
        assert MutationType.from_conflict(kind) is expected

    def test_every_conflict_kind_is_covered(self) -> None:
        """Total over the enum, so a kind added later cannot fall off the map
        silently - it falls through to SUPERSEDE, which the next test pins."""
        assert all(isinstance(MutationType.from_conflict(k), MutationType) for k in ConflictKind)

    def test_an_escalated_contradiction_still_scores_as_a_supersession(self) -> None:
        """Read from `kind`, not from `resolution_hint`.

        S4.4 resolves a CONTRADICTION to `escalate` because `C` does not exist
        at Layer 2. Scoring the mutation from the hint would then price it as
        `coexist` - the risk of the *decision* rather than of the write - when
        what is actually proposed is retiring a live fact.
        """
        assert MutationType.from_conflict(ConflictKind.CONTRADICTION) is MutationType.SUPERSEDE

    def test_a_duplicate_is_the_least_disturbing_thing_that_can_happen(self) -> None:
        """§2.4's merge writes no new row at all."""
        assert MutationType.from_conflict(ConflictKind.DUPLICATE).risk_feature == 0.0


class TestGraphFanout:
    """`min(1, log(1 + deg) / log(1 + 50))`."""

    def test_an_isolated_subject_scores_zero(self) -> None:
        assert graph_fanout(0) == 0.0

    def test_fifty_is_the_saturation_point(self) -> None:
        assert graph_fanout(50) == pytest.approx(1.0)

    def test_it_is_capped_rather_than_allowed_past_one(self) -> None:
        """A hub with 500 dependants is not five times as risky as one with 50,
        and `RiskFeatures` would refuse the unclamped value anyway."""
        assert graph_fanout(500) == 1.0

    def test_it_matches_the_formula(self) -> None:
        assert graph_fanout(7) == pytest.approx(math.log(8) / math.log(51))

    def test_the_first_edges_matter_more_than_the_last(self) -> None:
        """Logarithmic on purpose: one dependant to five is a real change in
        blast radius, and fifty to fifty-five is not."""
        early = graph_fanout(5) - graph_fanout(1)
        late = graph_fanout(49) - graph_fanout(45)

        assert early > late

    def test_it_never_falls(self) -> None:
        scores = [graph_fanout(n) for n in range(60)]

        assert all(a <= b for a, b in pairwise(scores))

    def test_a_negative_degree_is_refused(self) -> None:
        """A count cannot be negative, and one would make the logarithm
        undefined rather than merely wrong."""
        with pytest.raises(ValueError, match="cannot be negative"):
            graph_fanout(-1)


class TestSourceTierRisk:
    """`1 - trust multiplier`."""

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (SourceTier.TRUSTED_SYSTEM, 0.0),
            (SourceTier.VERIFIED_USER, 0.05),
            (SourceTier.UNVERIFIED_USER, 0.2),
            (SourceTier.TOOL_OUTPUT, 0.15),
            (SourceTier.RETRIEVED_WEB, 0.4),
        ],
    )
    def test_it_inverts_the_grounding_multiplier(self, tier: SourceTier, expected: float) -> None:
        assert source_tier_risk(tier) == pytest.approx(expected)

    def test_the_section_three_two_inversion_is_inherited_here(self) -> None:
        """A tool output scores *less* risky than an unverified human, because
        §3.2's multipliers rank them that way and §3.3 reuses those numbers.

        Pinned in both places rather than only one, so whoever resolves the
        §3.2-vs-§4 question finds every site that depends on the answer.
        """
        assert source_tier_risk(SourceTier.TOOL_OUTPUT) < source_tier_risk(
            SourceTier.UNVERIFIED_USER
        )

    def test_it_stays_inside_the_unit_interval(self) -> None:
        assert all(0.0 <= source_tier_risk(tier) <= 1.0 for tier in SourceTier)


class TestNovelty:
    """`1 - max cosine to existing memory`."""

    def test_a_claim_with_no_neighbour_is_maximally_novel(self) -> None:
        """§3.3's parenthesis: "unprecedented claims are riskier"."""
        assert novelty(None) == 1.0

    def test_an_exact_match_is_not_novel_at_all(self) -> None:
        assert novelty(1.0) == 0.0

    def test_it_inverts_the_cosine(self) -> None:
        assert novelty(0.4) == pytest.approx(0.6)

    def test_a_negative_cosine_is_clamped_rather_than_exceeding_one(self) -> None:
        """The bug this exists for.

        Cosine over unnormalised embeddings is genuinely negative sometimes -
        `ConflictReport.cosine` is bounded at -1 for exactly that reason - and
        `1 - (-0.4)` is 1.4: a feature outside its own range, weighted as
        though it were more than maximally novel. `RiskFeatures` would refuse
        it, so the failure would be a validation error on a legitimate
        retrieval result rather than a wrong number.
        """
        assert novelty(-0.4) == 1.0
        assert novelty(-1.0) == 1.0
