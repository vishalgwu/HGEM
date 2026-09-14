"""The three conflict checks, and the sixty-pair probe.  BUILD_NOTEBOOK.md S4.3

`detect` answers one question - can this candidate and what is already on record
both be true - and §2.2 gives it three ways to answer. Two are arithmetic over
the ontology and the clock; only the third asks a model. The order they run in
is the design: (b) and (c) short-circuit, so a cardinality violation costs no
BALANCED call at all.

What each class here is for:

- `TestTheProbe` is the step's DONE WHEN, against `fixtures/contradiction_corpus`.
- `TestTheCheapChecksRunFirst` holds the ordering to account, using a judge that
  records whether it was consulted - a result set cannot show that, only `calls`
  can.
- `TestReadingTheJudge` walks §2.3's thresholds from both sides.
- `TestWhatTheReportCarries` covers the fields a `ConflictReport` hands to S4.4.

The scaffolding they share is in `fixtures/conflict.py`. `LLMJudge`, which is
where the numbers come from in production, is tested in
`tests/unit/test_nli_judge.py`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fixtures.assertions import WHEN
from fixtures.conflict import (
    CLINICAL,
    PROBE_FLOOR,
    ScriptedJudge,
    candidate,
    incumbents,
    judge_pair,
)
from fixtures.contradiction_corpus import PAIRS
from guardmem_core.pipeline.l2_validate import IncumbentSet, Judgement, detect
from guardmem_core.schemas.verdict import ConflictKind
from guardmem_core.types import CandidateId


class TestTheProbe:
    async def test_the_sixty_pair_set_classifies_at_least_ninety_percent(self) -> None:
        """S4.3's DONE WHEN.

        Reported as a ratio with the misses named, not as a bare assertion: a
        probe that fails without saying *which* pairs it got wrong sends you
        back to read sixty rows by hand.
        """
        verdicts = [(pair, await judge_pair(pair)) for pair in PAIRS]
        misses = [
            (pair.key, pair.expected.value, got.value)
            for pair, got in verdicts
            if got is not pair.expected
        ]

        correct = (len(PAIRS) - len(misses)) / len(PAIRS)
        assert correct >= PROBE_FLOOR, f"{correct:.0%} correct; missed {misses}"

    def test_the_set_is_sixty_pairs(self) -> None:
        """The step says sixty. A set that quietly shrank would raise the
        percentage by removing the hard cases."""
        assert len(PAIRS) == 60

    def test_every_pair_has_a_distinct_key(self) -> None:
        """A duplicated key is a pair that was meant to be two."""
        assert len({pair.key for pair in PAIRS}) == len(PAIRS)

    def test_the_set_covers_every_kind_this_step_can_produce(self) -> None:
        """A probe with no cardinality pairs would score well and measure a
        third of the module."""
        assert {pair.expected for pair in PAIRS} == {
            ConflictKind.NONE,
            ConflictKind.CONTRADICTION,
            ConflictKind.CARDINALITY,
            ConflictKind.TEMPORAL_OVERLAP,
        }


class TestTheCheapChecksRunFirst:
    async def test_a_cardinality_violation_never_reaches_the_judge(self) -> None:
        """§2.2(b): a CARDINALITY conflict fires "regardless of NLI".

        The saving is the point - `MEMORY_ENGINE.md` §3.5 budgets tiers, and a
        BALANCED call paid for an answer arithmetic already had is the kind of
        cost nobody notices until the bill.
        """
        spec = CLINICAL.predicate("primary_dx")
        assert spec is not None
        judge = ScriptedJudge()

        report = await detect(
            candidate(predicate="primary_dx", obj="I10"),
            incumbents(("primary_dx", "E11.9", "type 2 diabetes")),
            spec,
            judge,
        )

        assert report.kind is ConflictKind.CARDINALITY
        assert judge.calls == 0

    async def test_a_temporal_overlap_never_reaches_the_judge(self) -> None:
        spec = CLINICAL.predicate("home_address")
        assert spec is not None
        judge = ScriptedJudge()

        report = await detect(
            candidate(predicate="home_address", obj="3 Calder Way"),
            incumbents(("home_address", "14 Ashfield Road", "14 Ashfield Road")),
            spec,
            judge,
        )

        assert report.kind is ConflictKind.TEMPORAL_OVERLAP
        assert judge.calls == 0

    async def test_a_candidate_that_closed_before_the_incumbent_opened_does_not_overlap(
        self,
    ) -> None:
        """§2.2(c) fires on intervals that *intersect*, not on any two values.

        Joan lived at the old address until January and the incumbent begins in
        March: both rows are true, of different periods, and retiring one would
        lose a fact the bitemporal model exists to keep. Undated candidates are
        read as unbounded in the past, so this case is unreachable from the
        extractor today - it becomes reachable at S4.5, and the arithmetic is
        what the judge is spared from guessing.
        """
        spec = CLINICAL.predicate("home_address")
        assert spec is not None
        judge = ScriptedJudge()

        report = await detect(
            candidate(
                predicate="home_address",
                obj="3 Calder Way",
                valid_from=WHEN - timedelta(days=365),
                valid_to=WHEN - timedelta(days=30),
            ),
            incumbents(("home_address", "14 Ashfield Road", "14 Ashfield Road")),
            spec,
            judge,
        )

        assert report.kind is not ConflictKind.TEMPORAL_OVERLAP
        assert judge.calls == 1

    async def test_a_candidate_with_no_incumbents_never_reaches_the_judge(self) -> None:
        """A novel fact is the common case. Paying for a completion to be told
        there was nothing to compare is a cost that only shows up on a bill."""
        spec = CLINICAL.predicate("allergy")
        assert spec is not None
        judge = ScriptedJudge()

        report = await detect(
            candidate(predicate="allergy", obj="penicillin"),
            IncumbentSet(candidate_id=CandidateId("c_1"), nearest=[], neighbours=[]),
            spec,
            judge,
        )

        assert report.kind is ConflictKind.NONE
        assert report.incumbent_assertion_id is None
        assert judge.calls == 0

    async def test_an_agreeing_incumbent_is_not_a_cardinality_conflict(self) -> None:
        """§2.2(b) fires on a live value "with a different object".

        An incumbent that already says what the candidate says is S4.4's merge.
        Treating it as a violation would retire a fact in favour of itself.
        """
        spec = CLINICAL.predicate("primary_dx")
        assert spec is not None
        judge = ScriptedJudge()

        report = await detect(
            candidate(predicate="primary_dx", obj="E11.9"),
            incumbents(("primary_dx", "E11.9", "type 2 diabetes")),
            spec,
            judge,
        )

        assert report.kind is not ConflictKind.CARDINALITY
        assert judge.calls == 1


class TestReadingTheJudge:
    @pytest.mark.parametrize(
        ("contradiction", "kind", "hint"),
        [
            (0.95, ConflictKind.CONTRADICTION, "escalate"),
            (0.65, ConflictKind.CONTRADICTION, "escalate"),
            (0.64, ConflictKind.NONE, "escalate"),
            (0.30, ConflictKind.NONE, "escalate"),
            (0.29, ConflictKind.NONE, "coexist"),
            (0.01, ConflictKind.NONE, "coexist"),
        ],
    )
    async def test_the_thresholds_are_the_ones_the_table_names(
        self, contradiction: float, kind: ConflictKind, hint: str
    ) -> None:
        """§2.3's boundaries, checked on both sides of each.

        The 0.3-0.65 band is the one worth pinning: §2.3 calls it ambiguous and
        says "the NLI is unsure, so a human or frontier model decides". It is
        `NONE` - nothing is established - with an `escalate` hint, and a gate
        that let it `coexist` would silently accept a fact the model could not
        vouch for.
        """
        spec = CLINICAL.predicate("allergy")
        assert spec is not None

        report = await detect(
            candidate(predicate="allergy", obj="penicillin"),
            incumbents(("allergy", "penicillin", "allergic to penicillin")),
            spec,
            ScriptedJudge(
                judgements=[Judgement(entail_fwd=0.1, entail_rev=0.1, contradiction=contradiction)]
            ),
        )

        assert report.kind is kind
        assert report.resolution_hint == hint

    async def test_the_worst_pair_decides_not_the_nearest(self) -> None:
        """A candidate that contradicts the third incumbent and agrees with the
        first is still a contradiction.

        Reporting the nearest one's comfortable numbers would hide it, and the
        nearest is what the vector search puts first - so this is the failure
        that looks correct in every log line.
        """
        spec = CLINICAL.predicate("allergy")
        assert spec is not None

        report = await detect(
            candidate(predicate="allergy", obj="latex"),
            incumbents(
                ("allergy", "penicillin", "allergic to penicillin"),
                ("allergy", "latex", "latex is fine now"),
            ),
            spec,
            ScriptedJudge(
                judgements=[
                    Judgement(entail_fwd=0.0, entail_rev=0.0, contradiction=0.02),
                    Judgement(entail_fwd=0.0, entail_rev=0.0, contradiction=0.91),
                ]
            ),
        )

        assert report.kind is ConflictKind.CONTRADICTION
        assert report.contradiction == pytest.approx(0.91)

    async def test_a_contradiction_never_comes_back_as_supersede(self) -> None:
        """§2.3 resolves a contradiction by "supersede if candidate newer *and*
        C >= tau_hi, else escalate", and `C` is Layer 3's composite - which has
        not run when this does.

        `ARCHITECTURE.md` §0: degradation never widens the auto-write path.
        Guessing `supersede` here would be exactly that, so S5.4 is where a
        confident, newer candidate may be upgraded.
        """
        spec = CLINICAL.predicate("allergy")
        assert spec is not None

        report = await detect(
            candidate(predicate="allergy", obj="penicillin"),
            incumbents(("allergy", "penicillin", "allergic to penicillin")),
            spec,
            ScriptedJudge(
                judgements=[Judgement(entail_fwd=0.0, entail_rev=0.0, contradiction=0.99)]
            ),
        )

        assert report.resolution_hint == "escalate"


class TestWhatTheReportCarries:
    async def test_the_deterministic_checks_report_zeroes_rather_than_numbers(self) -> None:
        """They answered without asking, so there is nothing to report.

        A fabricated 0.9 would read as a measurement in the audit record and in
        the review UI, and nothing downstream could tell it from one.
        """
        spec = CLINICAL.predicate("primary_dx")
        assert spec is not None

        report = await detect(
            candidate(predicate="primary_dx", obj="I10"),
            incumbents(("primary_dx", "E11.9", "type 2 diabetes")),
            spec,
            ScriptedJudge(),
        )

        assert (report.entailment, report.contradiction) == (0.0, 0.0)

    async def test_it_carries_the_cosine_the_store_measured(self) -> None:
        """S4.4's table keys three of its six rows on cosine, and the store is
        the only thing that knows it - see `ScoredAssertion`."""
        spec = CLINICAL.predicate("allergy")
        assert spec is not None

        report = await detect(
            candidate(predicate="allergy", obj="latex"),
            incumbents(("allergy", "penicillin", "allergic to penicillin"), cosine=0.42),
            spec,
            ScriptedJudge(),
        )

        assert report.cosine == pytest.approx(0.42)

    async def test_it_names_the_incumbent_it_conflicted_with(self) -> None:
        """`DecisionRecord` cites it, and a conflict that cannot say which fact
        it is about is a review task nobody can action."""
        spec = CLINICAL.predicate("primary_dx")
        assert spec is not None
        existing = incumbents(("primary_dx", "E11.9", "type 2 diabetes"))

        report = await detect(
            candidate(predicate="primary_dx", obj="I10"), existing, spec, ScriptedJudge()
        )

        assert report.incumbent_assertion_id == existing.nearest[0].assertion.assertion_id
