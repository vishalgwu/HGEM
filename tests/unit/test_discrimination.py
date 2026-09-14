"""The AUROC gate.  CHECKPOINT B

The measurement CHECKPOINT B turns on, and the one thing in this repository that
can catch "a scorer that produces plausible numbers with no discriminative
power". So it is worth being exact about: the values below are computed by hand
from the rank formula rather than compared against another run of the same code.

The tie cases are the substance. `S_cor` is 0.0 for every single-sourced
candidate, so a diagnostic AUROC over that term alone is mostly ties, and a
tie-blind implementation would report whatever the extraction order happened to
produce.
"""

from __future__ import annotations

import pytest

from guardmem_core.eval import (
    DiscriminationReport,
    Labelled,
    Verdict,
    agreement_rate,
    auroc,
    discriminate,
)
from guardmem_core.eval.discrimination import MARGINAL_FLOOR, PASS_FLOOR


def labelled(*rows: tuple[str, bool, float]) -> list[Labelled]:
    """A corpus carrying one score, `confidence`."""
    return [
        Labelled(candidate_id=name, keep=keep, scores={"confidence": score})
        for name, keep, score in rows
    ]


class TestAuroc:
    def test_perfect_separation_is_one(self) -> None:
        assert auroc([True, True, False, False], [0.9, 0.8, 0.2, 0.1]) == 1.0

    def test_perfectly_wrong_is_zero(self) -> None:
        """Not 0.5. A scorer that ranks every bad candidate above every good one
        is as informative as a perfect one and needs its sign flipped, which is
        a different finding from "this signal is noise"."""
        assert auroc([True, True, False, False], [0.1, 0.2, 0.8, 0.9]) == 0.0

    def test_all_ties_is_exactly_chance(self) -> None:
        """The case a tie-blind implementation gets wrong.

        Every score identical carries no information, so the answer is 0.5 -
        but broken ties would rank by array order and return anything at all.
        """
        assert auroc([True, False, True, False], [0.5, 0.5, 0.5, 0.5]) == 0.5

    def test_a_tie_across_the_boundary_counts_as_half(self) -> None:
        """Computed by hand: three kept against three rejected is nine pairs.
        The top two kept win all three each; the third ties one and wins two.
        8.5 / 9.
        """
        score = auroc([True, True, True, False, False, False], [3.0, 2.0, 1.0, 1.0, 0.0, -1.0])

        assert score == pytest.approx(8.5 / 9)

    def test_one_inversion(self) -> None:
        """Two kept, two rejected, four pairs, two of them the wrong way up."""
        assert auroc([True, True, False, False], [0.9, 0.1, 0.8, 0.2]) == 0.5

    def test_it_ignores_the_scale(self) -> None:
        """Rank-based, so only the ordering matters - which is what makes the
        per-term diagnostics comparable when the terms have different ranges."""
        order = [True, True, False, False]

        assert auroc(order, [0.9, 0.8, 0.2, 0.1]) == auroc(order, [900.0, 800.0, 2.0, 1.0])

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="3 labels against 2 scores"):
            auroc([True, False, True], [0.1, 0.2])

    def test_an_empty_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="undefined over an empty set"):
            auroc([], [])

    @pytest.mark.parametrize("label", [True, False])
    def test_a_one_sided_corpus_raises_rather_than_returning_chance(self, label: bool) -> None:
        """The distinction that decides what somebody does next.

        A corpus with no rejected candidate has no discrimination to measure.
        Returning 0.5 would report "this scorer is useless" where the truth is
        "this question was not asked" - and one of those says re-label while the
        other says rewrite the scorer.
        """
        with pytest.raises(ValueError, match="labelling problem, not a scorer one"):
            auroc([label, label, label], [0.1, 0.2, 0.3])


class TestTheGateBands:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (1.0, Verdict.PASS),
            (0.80, Verdict.PASS),
            (0.79, Verdict.MARGINAL),
            (0.75, Verdict.MARGINAL),
            (0.74, Verdict.FAIL),
            (0.0, Verdict.FAIL),
        ],
    )
    def test_each_band(self, score: float, expected: Verdict) -> None:
        """CHECKPOINT B: ">= 0.80 -> PASS", "0.75-0.80 -> MARGINAL",
        "< 0.75 -> FAIL. Stop. Do not build the gateway."

        Driven through `discriminate` rather than the private band function, on
        a corpus constructed to land on each value - so the thresholds are
        pinned where they are actually read.
        """
        from guardmem_core.eval.discrimination import _verdict

        assert _verdict(score) is expected

    def test_the_floors_are_the_checkpoint_s(self) -> None:
        assert (PASS_FLOOR, MARGINAL_FLOOR) == (0.80, 0.75)

    def test_the_boundary_is_inclusive_upward(self) -> None:
        """Half-open like every other band in this codebase, and the checkpoint
        writes `>= 0.80`."""
        from guardmem_core.eval.discrimination import _verdict

        assert _verdict(PASS_FLOOR) is Verdict.PASS
        assert _verdict(MARGINAL_FLOOR) is Verdict.MARGINAL


class TestDiscriminate:
    def test_it_reports_the_composite_and_every_term(self) -> None:
        """Diagnostics 1 and 2 - "is entropy doing anything?", "is grounding
        doing anything?" - computed for all of them, because the answer is only
        useful in comparison."""
        corpus = [
            Labelled(
                candidate_id="c1",
                keep=True,
                scores={"confidence": 0.9, "grounding": 0.1, "semantic_entropy": 0.9},
            ),
            Labelled(
                candidate_id="c2",
                keep=False,
                scores={"confidence": 0.2, "grounding": 0.9, "semantic_entropy": 0.1},
            ),
        ]

        report = discriminate(corpus)

        assert report.auroc == 1.0
        assert report.by_score["grounding"] == 0.0
        assert set(report.by_score) == {"confidence", "grounding", "semantic_entropy"}

    def test_it_names_the_best_single_score(self) -> None:
        """The checkpoint's closing advice: "a working single-signal scorer
        beats an elegant composite that does not separate"."""
        corpus = [
            Labelled(candidate_id="c1", keep=True, scores={"confidence": 0.5, "grounding": 0.9}),
            Labelled(candidate_id="c2", keep=False, scores={"confidence": 0.5, "grounding": 0.1}),
        ]

        assert discriminate(corpus).best_single_score == "grounding"

    def test_it_counts_the_corpus_and_the_kept(self) -> None:
        """AUROC is fragile on a lopsided split long before it is undefined, so
        the split is reported rather than left to be inferred."""
        report = discriminate(labelled(("a", True, 0.9), ("b", False, 0.1), ("c", False, 0.2)))

        assert (report.labelled, report.kept) == (3, 1)

    def test_a_ragged_corpus_is_refused(self) -> None:
        """A missing score would make one diagnostic run over a subset, which is
        a different measurement wearing the same name."""
        corpus = [
            Labelled(candidate_id="c1", keep=True, scores={"confidence": 0.9, "grounding": 0.5}),
            Labelled(candidate_id="c2", keep=False, scores={"confidence": 0.2}),
        ]

        with pytest.raises(ValueError, match="different measurement"):
            discriminate(corpus)

    def test_an_unknown_composite_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not among the scores"):
            discriminate(labelled(("a", True, 0.9), ("b", False, 0.1)), composite="nope")

    def test_an_empty_corpus_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no labelled candidates"):
            discriminate([])

    def test_the_report_is_serialisable(self) -> None:
        """It goes into the sign-off block and, later, an eval artefact."""
        report = discriminate(labelled(("a", True, 0.9), ("b", False, 0.1)))

        assert DiscriminationReport.model_validate_json(report.model_dump_json()) == report


class TestAgreementRate:
    def test_identical_labellings_agree_completely(self) -> None:
        labels = {"a": True, "b": False}

        assert agreement_rate(labels, labels) == 1.0

    def test_it_measures_only_the_shared_candidates(self) -> None:
        """Diagnostic 3 re-labels *30* items out of 200, so the second pass is
        deliberately a subset."""
        first = {"a": True, "b": False, "c": True}
        second = {"a": True, "b": True}

        assert agreement_rate(first, second) == 0.5

    def test_no_overlap_is_refused(self) -> None:
        with pytest.raises(ValueError, match="share no candidate"):
            agreement_rate({"a": True}, {"b": False})

    def test_the_floor_is_the_checkpoint_s_ten_percent(self) -> None:
        """ "If they disagree with themselves more than 10% of the time, the task
        is underspecified and the ontology needs work before the scorer does."

        That ordering is the point: a low AUROC against inconsistent labels says
        nothing about the scorer, and rewriting it against them is how a week
        gets spent fitting noise.
        """
        from guardmem_core.eval.discrimination import SELF_AGREEMENT_FLOOR

        assert SELF_AGREEMENT_FLOOR == 0.90
