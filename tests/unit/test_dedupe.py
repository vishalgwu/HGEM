"""§2.3's table and §2.4's merge.  BUILD_NOTEBOOK.md S4.4

The thresholds are written as literals here rather than imported from
`dedupe`. That is the point of the module: a test that imported
`_DUPLICATE_COSINE` and compared `classify` against it would pass just as
happily if the constant were 0.6, because both sides would move together. The
numbers below are transcribed from `MEMORY_ENGINE.md` §2.3, and each pair of
tests straddles a boundary - one example that must match a row and one a hair
under it that must not.

What the 60-pair probe at S4.3 does *not* reach, and this module has to: the
DUPLICATE and REFINEMENT rows. `fixtures/conflict.incumbents` scores every pair
at cosine 0.9 (under 0.95) and `judge_pair` sets `entail_rev` equal to
`entail_fwd`, so the corpus cannot express "the candidate entails the incumbent
and the incumbent does not entail the candidate" - which is the whole content of
row 2. Two rows of the table had no coverage at all before this file.
"""

from __future__ import annotations

import pytest

from fixtures.assertions import citation, stored_assertion
from fixtures.conflict import candidate
from guardmem_core.pipeline.l2_validate import Judgement, classify, merge, most_severe
from guardmem_core.pipeline.l2_validate.dedupe import (
    AMBIGUOUS,
    CARDINALITY,
    COEXIST,
    CONTRADICTION,
    DUPLICATE,
    REFINEMENT,
    TEMPORAL_OVERLAP,
)
from guardmem_core.schemas.verdict import ConflictKind


def judged(*, fwd: float = 0.1, rev: float = 0.1, contra: float = 0.0) -> Judgement:
    """One NLI verdict, defaulting to "these two are unrelated"."""
    return Judgement(entail_fwd=fwd, entail_rev=rev, contradiction=contra)


class TestTheDuplicateRow:
    """Row 1: cosine >= 0.95, both entailments >= 0.85 -> merge."""

    def test_a_restatement_merges(self) -> None:
        assert classify(0.97, judged(fwd=0.9, rev=0.9)) is DUPLICATE

    def test_the_cosine_boundary_is_inclusive(self) -> None:
        assert classify(0.95, judged(fwd=0.85, rev=0.85)) is DUPLICATE

    def test_a_hair_under_the_cosine_is_not_a_duplicate(self) -> None:
        """0.94 with perfect entailment falls into the gap §2.3 leaves open.

        Not REFINEMENT - that needs the asymmetry - and not row 5, which wants
        cosine below 0.80. The module's documented fall-through answers
        `coexist`, which costs a near-duplicate row and retires nothing.
        """
        assert classify(0.94, judged(fwd=0.9, rev=0.9)) is COEXIST

    def test_one_sided_entailment_is_not_a_duplicate(self) -> None:
        """Both directions, or it is not the same claim twice."""
        assert classify(0.99, judged(fwd=0.4, rev=0.9)) is not DUPLICATE


class TestTheRefinementRow:
    """Row 2: cosine >= 0.80, rev >= 0.85, fwd < 0.85 -> supersede."""

    def test_the_narrower_claim_supersedes(self) -> None:
        """ "Allergic to penicillin and amoxicillin" against "allergic to
        penicillin": the candidate entails the incumbent, not the reverse."""
        assert classify(0.88, judged(fwd=0.30, rev=0.92)) is REFINEMENT

    def test_the_asymmetry_is_required_in_that_direction(self) -> None:
        """Reversed, it is the incumbent that is more specific.

        Superseding here would replace a precise fact with a vaguer one, which
        is the single most damaging way to read this row backwards.
        """
        assert classify(0.88, judged(fwd=0.92, rev=0.30)) is not REFINEMENT

    def test_the_entailment_boundary_is_inclusive(self) -> None:
        assert classify(0.80, judged(fwd=0.84, rev=0.85)) is REFINEMENT

    def test_it_needs_the_cosine_too(self) -> None:
        """Below 0.80 the two are not close enough to call one a refinement of
        the other, whatever the entailment says."""
        assert classify(0.79, judged(fwd=0.30, rev=0.92)) is COEXIST


class TestTheContradictionRows:
    def test_at_the_floor_it_is_a_contradiction(self) -> None:
        assert classify(0.5, judged(contra=0.65)) is CONTRADICTION

    def test_it_escalates_rather_than_superseding(self) -> None:
        """§2.3 supersedes only if the candidate is newer *and* `C` >= tau_hi.

        `C` is Layer 3's and does not exist when Layer 2 runs, so the conjunct
        cannot be established and the row's own `else` applies. S5.4 is where a
        confident, newer candidate may be upgraded.
        """
        assert classify(0.5, judged(contra=0.9)).resolution_hint == "escalate"

    def test_the_ambiguous_band_escalates_without_a_finding(self) -> None:
        """§2.3's last row: the NLI is unsure, so a human decides.

        `kind` stays NONE - what is ambiguous is the resolution, not the
        finding, and `ConflictKind` has no member for "maybe".
        """
        ambiguous = classify(0.5, judged(contra=0.45))
        assert ambiguous is AMBIGUOUS
        assert ambiguous.kind is ConflictKind.NONE
        assert ambiguous.resolution_hint == "escalate"

    def test_the_bands_meet_without_a_gap_or_an_overlap(self) -> None:
        assert classify(0.5, judged(contra=0.30)) is AMBIGUOUS
        assert classify(0.5, judged(contra=0.2999)) is COEXIST
        assert classify(0.5, judged(contra=0.6499)) is AMBIGUOUS


class TestContradictionOutranksSimilarity:
    """The first of the two documented departures from the printed table.

    §2.3 lists DUPLICATE first with `—` in the contradiction column, so a
    literal top-to-bottom match merges a pair the judge called mutually
    exclusive. A merge bumps `corroboration_count`, which feeds `S_cor`, which
    raises `C` - so the printed order lets an incoherent judgement raise
    confidence in a fact by feeding it its own negation.
    """

    def test_an_incoherent_judgement_never_merges(self) -> None:
        assert classify(0.99, judged(fwd=0.95, rev=0.95, contra=0.9)) is CONTRADICTION

    def test_an_unsure_judgement_never_merges_either(self) -> None:
        """0.45 is the ambiguous band. A human decides, rather than the
        similarity rows deciding for them."""
        assert classify(0.99, judged(fwd=0.95, rev=0.95, contra=0.45)) is AMBIGUOUS

    def test_a_confident_no_still_reaches_the_similarity_rows(self) -> None:
        """The cost of the reordering, stated: nothing, when the numbers are
        coherent."""
        assert classify(0.99, judged(fwd=0.95, rev=0.95, contra=0.29)) is DUPLICATE


class TestPickingOneAnswerFromTen:
    def test_a_contradiction_anywhere_outranks_a_duplicate_in_front(self) -> None:
        """The reason this ranks rather than taking the nearest.

        A candidate that restates the first incumbent and contradicts the third
        is a contradiction; reporting the first one's comfortable numbers would
        hide it.
        """
        assert most_severe([DUPLICATE, COEXIST, CONTRADICTION]) == 2

    def test_merge_outranks_supersede(self) -> None:
        """Among the actions that change memory, the one that writes least
        wins: a merge adds no row, a supersession retires one."""
        assert most_severe([REFINEMENT, DUPLICATE]) == 1

    def test_the_deterministic_rows_rank_above_both(self) -> None:
        assert most_severe([DUPLICATE, CARDINALITY]) == 1
        assert most_severe([COEXIST, TEMPORAL_OVERLAP]) == 1

    def test_ties_go_to_the_nearest(self) -> None:
        """`IncumbentSet.nearest` is cosine-ordered, so the first of two equal
        resolutions is the closer fact."""
        assert most_severe([COEXIST, COEXIST, COEXIST]) == 0

    def test_an_empty_set_is_a_caller_bug(self) -> None:
        """A candidate with no incumbents is answered before the table is
        consulted, so `None` would be a third meaning nobody needs."""
        with pytest.raises(ValueError, match="at least one resolution"):
            most_severe([])


class TestMerge:
    """§2.4: increment `corroboration_count`, append the `Provenance`, no row."""

    def test_it_does_not_create_a_row(self) -> None:
        """S4.4 states this outright, and the assertion id is what carries it:
        a merge returns the same fact, not a second one beside it."""
        incumbent = stored_assertion(visible=True)

        merged = merge(incumbent, candidate(predicate="allergy", obj="penicillin"))

        assert merged.assertion_id == incumbent.assertion_id

    def test_a_new_source_corroborates(self) -> None:
        incumbent = stored_assertion(visible=True, provenance=[citation(source_hash="sha256:one")])

        merged = merge(incumbent, candidate(predicate="allergy", obj="penicillin"))

        assert merged.corroboration_count == 2
        assert len(merged.provenance) == 2

    def test_a_second_span_of_the_same_document_does_not(self) -> None:
        """The distinction `StoredAssertion` draws and this is the function
        that keeps or loses it.

        `corroboration_count` is independent *sources*; two spans of one
        document are two citations and one source. §3.2's `S_cor` is a function
        of sources, so counting citations would let a single planted document
        quoted three times score as corroborated - inflating confidence exactly
        where a poisoning attempt wants it inflated.
        """
        incumbent = stored_assertion(visible=True, provenance=[citation(span=(40, 60))])
        restated = candidate(predicate="allergy", obj="penicillin")
        assert restated.provenance.source_hash == "sha256:abc", "the same document"
        assert restated.provenance.source_span == (0, 10), "a different span of it"

        merged = merge(incumbent, restated)

        assert merged.corroboration_count == 1, "same document, so no new source"
        assert len(merged.provenance) == 2, "but §2.4 appends the span regardless"

    def test_merging_the_same_citation_twice_changes_nothing(self) -> None:
        """Idempotent, because S3.3's relay delivers at least once.

        Without this, a retry appends a duplicate citation and - on an
        independent source - inflates the corroboration count on every attempt.
        """
        incumbent = stored_assertion(visible=True, provenance=[citation(source_hash="sha256:one")])
        proposal = candidate(predicate="allergy", obj="penicillin")

        once = merge(incumbent, proposal)
        twice = merge(once, proposal)

        assert twice == once

    def test_it_leaves_the_rest_of_the_fact_alone(self) -> None:
        """A merge is not a rewrite: the belief, its interval and its scores are
        the incumbent's, and only §2.4's two fields move."""
        incumbent = stored_assertion(visible=True, provenance=[citation(source_hash="sha256:one")])

        merged = merge(incumbent, candidate(predicate="allergy", obj="penicillin"))

        assert merged.object == incumbent.object
        assert merged.valid_from == incumbent.valid_from
        assert merged.valid_to is None
        assert merged.confidence == incumbent.confidence
        assert merged.visible == incumbent.visible

    def test_confidence_is_not_recomputed_here(self) -> None:
        """§2.4's sentence ends "and recomputes confidence with the
        corroboration term", and that half is deliberately S5.2's.

        `S_cor` is one of five inputs to §3.2's composite. Recomputing `C` from
        this one would mean reimplementing the composite a layer early and from
        a fifth of its evidence. What this does is make the input correct.
        """
        incumbent = stored_assertion(visible=True, provenance=[citation(source_hash="sha256:one")])

        merged = merge(incumbent, candidate(predicate="allergy", obj="penicillin"))

        assert merged.confidence == incumbent.confidence
        assert merged.corroboration_count > incumbent.corroboration_count


class TestEqualityAsEvidence:
    """The DUPLICATE row's third route, added at S4.4 and found by invariant I2.

    Cosine and entailment are proxies for "these are the same claim". When the
    incumbent already holds the exact object over an intersecting interval, the
    proxies have nothing left to establish - and letting them decide means a
    restatement scored at 0.94 becomes a second live row saying what the first
    one says.
    """

    def test_an_exact_restatement_merges_whatever_the_proxies_say(self) -> None:
        """Cosine 0.2 and no entailment at all: numbers that would otherwise
        read as two unrelated facts."""
        assert classify(0.2, judged(fwd=0.0, rev=0.0), restates=True) is DUPLICATE

    def test_it_is_read_below_the_contradiction_rows_not_above(self) -> None:
        """The care in the whole feature.

        An equal `object` does not mean the claims agree: "allergic to
        penicillin" and "not allergic to penicillin" both extract `penicillin`,
        and the negation lives only in the verbatim. Merging on equality alone
        would corroborate a fact with its own negation - raising
        `corroboration_count`, which feeds `S_cor`, which raises `C`.
        """
        assert classify(0.99, judged(contra=0.9), restates=True) is CONTRADICTION
        assert classify(0.99, judged(contra=0.45), restates=True) is AMBIGUOUS

    def test_not_comparing_is_not_the_same_as_comparing_equal(self) -> None:
        """The default is false, so a caller that never compared cannot assert
        sameness by leaving the argument off."""
        assert classify(0.2, judged(fwd=0.0, rev=0.0)) is COEXIST
