"""Semantic entropy over meaning clusters.  BUILD_NOTEBOOK.md S5.1

The DONE WHEN is one assertion and the step says where to put it: "the worked
example in MEMORY_ENGINE.md 3.1 reproduces `H_norm = 0.590` to 3 decimals. That
exact assertion goes in the test file." It is
`test_the_worked_example_reproduces_to_three_decimals` below, written against
the spec's own five samples rather than a fixture that happens to give the same
number.

`entail` is a dictionary lookup here, not a model. That is the point of the
injection: §3.1's arithmetic is what this module owns, and a real entailment
model would make every assertion below a measurement of the model instead. The
judge's accuracy is the nightly eval gate's question, the same way the 60-pair
conflict probe draws that line.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Final

import pytest

from guardmem_core.pipeline.l3_score import (
    MeaningClusters,
    cluster_meanings,
    semantic_entropy,
)

# MEMORY_ENGINE.md §3.1's worked example, transcribed: K=5 on
# `primary_care_provider`, samples -> {"Dr. Alvarez", "Dr. Alvarez",
# "Alvarez, MD", "Dr. Chen", "unclear"}.
WORKED: Final = ["Dr. Alvarez", "Dr. Alvarez", "Alvarez, MD", "Dr. Chen", "unclear"]

# The meanings a competent NLI model would find in them: the first three are one
# provider under two spellings, and the other two are each their own answer.
_MEANING: Final = {
    "Dr. Alvarez": "alvarez",
    "Alvarez, MD": "alvarez",
    "Dr. Chen": "chen",
    "unclear": "unclear",
}


def by_meaning(premise: str, hypothesis: str) -> float:
    """An oracle `EntailFn`: 1.0 when two strings mean the same thing."""
    return 1.0 if _MEANING[premise] == _MEANING[hypothesis] else 0.0


def literal(premise: str, hypothesis: str) -> float:
    """An `EntailFn` that only recognises identical strings.

    What a lexical comparison would give, and §3.1 exists because it is wrong:
    "lives in Austin" and "resides in Austin, TX" are one meaning.
    """
    return 1.0 if premise == hypothesis else 0.0


class TestTheWorkedExample:
    def test_the_worked_example_reproduces_to_three_decimals(self) -> None:
        """S5.1's DONE WHEN, stated exactly as the step states it."""
        assert round(semantic_entropy(WORKED, by_meaning), 3) == 0.590

    def test_the_clusters_are_the_ones_the_spec_names(self) -> None:
        """§3.1: clusters -> {Alvarez: 3}, {Chen: 1}, {unclear: 1}.

        Checked as well as the number, because several wrong partitions round
        to 0.590 from a different `p` and the step's assertion alone would not
        tell them apart.
        """
        clusters = cluster_meanings(WORKED, by_meaning)

        sizes = sorted(clusters.labels.count(label) for label in set(clusters.labels))
        assert sizes == [1, 1, 3]

    def test_the_unnormalised_entropy_is_the_spec_s_0_950(self) -> None:
        """§3.1 quotes `H = 0.950` before normalising, and that number is only
        reproducible in nats - which is what pins the log base."""
        normalised = semantic_entropy(WORKED, by_meaning)

        assert round(normalised * math.log(len(WORKED)), 3) == 0.950

    def test_a_lexical_comparison_gives_a_different_answer(self) -> None:
        """The reason §3.1 clusters by entailment rather than by string.

        "Alvarez, MD" and "Dr. Alvarez" are one meaning and two strings, so a
        lexical reading splits the majority cluster and reports more
        uncertainty than there is.
        """
        assert semantic_entropy(WORKED, literal) > semantic_entropy(WORKED, by_meaning)


class TestTheEdgesOfTheFormula:
    def test_one_sample_is_zero_by_definition(self) -> None:
        """§3.1: "H_norm := 0 when K = 1". Not a limit of the formula - `log 1`
        is zero and the division would raise."""
        assert semantic_entropy(["only one"], by_meaning) == 0.0

    def test_total_agreement_is_zero(self) -> None:
        assert semantic_entropy(["Dr. Alvarez"] * 5, by_meaning) == 0.0

    def test_total_disagreement_is_one(self) -> None:
        """`H` is `log K` when every sample is its own cluster, so the ratio is
        1.0 - to within the float64 error of computing it, which is why this is
        `approx` and `test_five_samples_that_all_disagree_do_not_raise` exists.
        """
        assert semantic_entropy(
            ["Dr. Alvarez", "Dr. Chen", "unclear"], by_meaning
        ) == pytest.approx(1.0)

    def test_five_samples_that_all_disagree_do_not_raise(self) -> None:
        """A regression, and the bug was reachable rather than theoretical.

        `H / log K` overshoots 1.0 by up to 8e-16 in float64 for 51 of the K
        values between 2 and 199 - **K = 5 among them**, which is §1.2's largest
        sample count. `MeaningClusters.entropy` is declared `le=1.0`, so before
        the clamp this raised a `ValidationError` on five samples that all
        disagree: the commonest maximum-uncertainty case, and precisely the one
        the whole term exists to detect.
        """
        five = ["Dr. Alvarez", "Dr. Chen", "unclear", "Dr. Okafor", "Dr. Reyes"]

        def none_agree(premise: str, hypothesis: str) -> float:
            return 1.0 if premise == hypothesis else 0.0

        assert semantic_entropy(five, none_agree) == 1.0

    def test_it_stays_inside_the_unit_interval(self) -> None:
        """`ConfidenceReport.semantic_entropy` declares `[0, 1]`, and §3.2 uses
        `1 - H_norm`, so a value outside it would raise confidence above 1."""
        for size in range(1, 6):
            assert 0.0 <= semantic_entropy(WORKED[:size], by_meaning) <= 1.0

    def test_no_samples_is_refused_rather_than_scored(self) -> None:
        """The failure that would be invisible.

        The arithmetic hands back 0.0 for an empty set, which reads as *total
        confidence* - and §3.2 would weight it at 0.35 on the strength of no
        evidence at all. `ARCHITECTURE.md` §0: degradation never widens the
        auto-write path.
        """
        with pytest.raises(ValueError, match="at least one sample"):
            semantic_entropy([], by_meaning)


class TestAbstentions:
    """A sample that proposed nothing for this `(subject, predicate)` is `None`.

    S5.6's correction to this step: `ExtractionResult.samples` holds K draws and
    not every draw proposes every fact, so `K` is the *draw* count and the gaps
    are abstentions. See the module docstring for why they are not dropped.
    """

    @pytest.mark.parametrize(
        ("samples", "expected"),
        [
            (["A", "A", "A", "A", "A"], 0.0),
            (["A", "A", "A", "A", None], 0.3109),
            (["A", "A", "A", None, None], 0.5904),
            (["A", "A", None, None, None], 0.8277),
            (["A", None, None, None, None], 1.0),
        ],
    )
    def test_entropy_rises_as_support_falls(
        self, samples: list[str | None], expected: float
    ) -> None:
        """The property that made the first implementation wrong.

        Fewer draws proposing the fact must mean more uncertainty, monotonically.
        """
        assert cluster_meanings(samples, literal).entropy == pytest.approx(expected, abs=0.0005)

    def test_abstentions_do_not_cluster_with_each_other(self) -> None:
        """The bug this class exists for, stated as the mechanism.

        Clustering the silent draws together makes one claim against four
        abstentions a 0.2/0.8 split - low spread, so *low* entropy - and a fact
        one sample proposed scores more confident than one three samples agreed
        on. `same_meaning` is bidirectional entailment; an abstention asserts no
        proposition and so entails nothing, including another abstention.
        """
        clusters = cluster_meanings([None, None, None], literal)

        assert clusters.labels == [0, 1, 2]
        assert clusters.entropy == pytest.approx(1.0)

    def test_one_of_five_is_not_more_confident_than_three_of_five(self) -> None:
        """The comparison the first version got backwards."""
        sparse = cluster_meanings(["A", None, None, None, None], literal).entropy
        supported = cluster_meanings(["A", "A", "A", None, None], literal).entropy

        assert sparse > supported

    def test_entail_is_never_asked_about_an_abstention(self) -> None:
        """There is no text to judge, and a cross-encoder handed `None` would
        raise rather than return a number."""
        asked: list[tuple[str, str]] = []

        def counting(premise: str, hypothesis: str) -> float:
            asked.append((premise, hypothesis))
            return 0.0

        cluster_meanings(["A", None, "B"], counting)

        assert asked == [("A", "B")]

    def test_an_abstaining_sample_zero_still_anchors_the_minority(self) -> None:
        """Possible only if candidates are ever pooled across samples, but the
        rule is defined against sample 0 either way."""
        clusters = cluster_meanings([None, "A", "A"], literal)

        assert clusters.minority == [1, 2]


class TestBidirectionalIsNotOptional:
    """§3.1 step 1 requires entailment >= 0.8 **in both directions**."""

    def test_one_way_entailment_does_not_merge(self) -> None:
        """The failure a one-way test would cause, in its most common shape.

        "Allergic to penicillin and amoxicillin" entails "allergic to
        penicillin" and not the reverse: the narrower claim is strictly more
        informative. A one-way test reads that as agreement and reports
        certainty about which of the two the model actually meant.
        """
        wide, narrow = "allergic to penicillin", "allergic to penicillin and amoxicillin"

        def one_way(premise: str, hypothesis: str) -> float:
            """Only the narrow claim entails the wide one."""
            return 1.0 if premise == hypothesis or (premise, hypothesis) == (narrow, wide) else 0.0

        # `narrow` first, so the *forward* comparison succeeds and only the
        # reverse one fails. Ordered the other way this passes without ever
        # consulting the reverse direction - which is what it is here to test,
        # and how the first version of it was vacuous.
        clusters = cluster_meanings([narrow, wide], one_way)

        assert clusters.labels[0] != clusters.labels[1]
        assert clusters.entropy == 1.0

    @pytest.mark.parametrize(("forward", "reverse"), [(0.8, 0.8), (0.8, 1.0), (1.0, 0.8)])
    def test_the_threshold_is_inclusive_on_both_sides(self, forward: float, reverse: float) -> None:
        """§3.1 writes `>= 0.8`, so 0.8 itself is agreement."""
        assert cluster_meanings(["a", "b"], _scripted(forward, reverse)).entropy == 0.0

    @pytest.mark.parametrize(("forward", "reverse"), [(0.79, 1.0), (1.0, 0.79)])
    def test_a_hair_under_on_either_side_is_not_agreement(
        self, forward: float, reverse: float
    ) -> None:
        assert cluster_meanings(["a", "b"], _scripted(forward, reverse)).entropy == 1.0

    def test_the_reverse_direction_is_not_asked_when_the_forward_one_failed(self) -> None:
        """A cost property, and the module claims it in as many words.

        Most pairs in a disagreeing sample set fail forward, so short-circuiting
        roughly halves the comparisons - which matters once `entail` is a
        cross-encoder rather than a dict.
        """
        asked: list[tuple[str, str]] = []

        def counting(premise: str, hypothesis: str) -> float:
            asked.append((premise, hypothesis))
            return 0.0

        cluster_meanings(["a", "b"], counting)

        assert asked == [("a", "b")], "the reverse direction was asked for anyway"


class TestTheMinorityDrop:
    """§3.1: "a candidate that appears in zero clusters containing sample 0's
    meaning is dropped as a minority hallucination"."""

    def test_it_names_the_samples_that_disagree_with_the_canonical_one(self) -> None:
        clusters = cluster_meanings(WORKED, by_meaning)

        assert clusters.minority == [3, 4]

    def test_sample_zero_is_never_its_own_minority(self) -> None:
        """It is the canonical draw - §1.2 takes it at temperature 0 - so it is
        the thing the others are minorities *of*."""
        for size in range(1, 6):
            assert 0 not in cluster_meanings(WORKED[:size], by_meaning).minority

    def test_agreement_leaves_nobody_out(self) -> None:
        assert cluster_meanings(["Dr. Alvarez"] * 3, by_meaning).minority == []

    def test_a_majority_that_disagrees_with_sample_zero_is_still_the_minority(self) -> None:
        """Not a vote. Four samples saying Chen do not outrank the canonical
        draw saying Alvarez: §3.1 defines the rule against sample 0, and the
        disagreement is what `entropy` is for.
        """
        samples = ["Dr. Alvarez", "Dr. Chen", "Dr. Chen", "Dr. Chen", "Dr. Chen"]

        clusters = cluster_meanings(samples, by_meaning)

        assert clusters.minority == [1, 2, 3, 4]


class TestItIsReplayable:
    """`RULES.md` §1.6 and invariant I4: same inputs, same record."""

    def test_labels_do_not_depend_on_comparison_order(self) -> None:
        """Union-find keeps the lowest index as the root, so `labels[0]` is
        always 0 and two runs produce identical labels."""
        first = cluster_meanings(WORKED, by_meaning)
        second = cluster_meanings(WORKED, by_meaning)

        assert first == second
        assert first.labels[0] == 0

    def test_transitivity_holds_through_a_chain(self) -> None:
        """A entails B entails C, and A is never compared with C directly.

        Union-find is what makes that one cluster rather than two, and it is
        the case a naive pairwise grouping gets wrong.
        """
        pairs = {("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")}

        def chain(premise: str, hypothesis: str) -> float:
            return 1.0 if premise == hypothesis or (premise, hypothesis) in pairs else 0.0

        clusters = cluster_meanings(["a", "b", "c"], chain)

        assert clusters.labels == [0, 0, 0]
        assert clusters.entropy == 0.0

    def test_the_result_is_a_frozen_model(self) -> None:
        """It lands on `ConfidenceReport` and into the audit record."""
        clusters = cluster_meanings(WORKED, by_meaning)

        assert isinstance(clusters, MeaningClusters)
        with pytest.raises(ValueError, match="frozen"):
            clusters.entropy = 0.0  # type: ignore[misc]


def _scripted(forward: float, reverse: float) -> Callable[[str, str], float]:
    """An `EntailFn` over exactly two samples, `a` then `b`."""

    def entail(premise: str, hypothesis: str) -> float:
        if premise == hypothesis:
            return 1.0
        return forward if (premise, hypothesis) == ("a", "b") else reverse

    return entail
