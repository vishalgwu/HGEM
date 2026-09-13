"""The anti-hallucination rule, exact and fuzzy.  S2.3

`MEMORY_ENGINE.md` §1.3: "No span → `REJECT(reason=UNSOURCED)`. This single rule
kills most confabulated facts before any scoring happens."

It works because it is not a judgement. A model that invents a fact must also
invent the sentence it came from, and an invented sentence is not in the source.
So the tests that matter are the ones pinning where the line falls - every case
in `TestWhatItRefuses` is a claim that a slightly looser matcher would attach a
real span to, and the reviewer would then be shown a highlight that does not say
what the candidate claims.

Two of those rejections cost recall and are recorded here as measurements rather
than defended as ideals: a case-different quote scores 86.36 and a
doubled-whitespace quote 91.67, both under §1.3's threshold of 92. Checkpoint B
is where that number gets revisited, with an AUROC to justify the move, and its
ordered diagnosis says to tighten rather than loosen it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from guardmem_core.pipeline.l1_extract.span_linker import link_span
from guardmem_core.schemas import Provenance, SourceTier

_SOURCE = (
    "Patient: I'm allergic to penicillin - it gives me hives. "
    "I use the CVS on Elm Street now, not the one on Main."
)

# A model that pasted from a word processor. Built by escape so this file
# stays pure ASCII - ruff's RUF001 flags the literal character, and it is
# right to: a typographic apostrophe is invisible in a diff.
_SMART_QUOTE = "I" + chr(0x2019) + "m allergic to penicillin"


class TestExactMatches:
    @pytest.mark.parametrize(
        "verbatim", ["allergic to penicillin", "it gives me hives", "CVS on Elm Street"]
    )
    def test_a_span_slices_back_to_the_text_it_quotes(self, verbatim: str) -> None:
        # The only property that really matters. Everything else is a corollary.
        match = link_span(verbatim, _SOURCE)
        assert match is not None
        assert _SOURCE[match.span[0] : match.span[1]] == match.text == verbatim

    def test_an_exact_match_aligns_perfectly(self) -> None:
        match = link_span("penicillin", _SOURCE)
        assert match is not None
        assert match.alignment == 1.0

    def test_the_span_is_half_open_and_the_length_matches(self) -> None:
        match = link_span("penicillin", _SOURCE)
        assert match is not None
        assert match.span[1] - match.span[0] == len("penicillin")

    def test_the_first_occurrence_wins_when_the_text_repeats(self) -> None:
        # Any occurrence is a true citation; preferring the first keeps the
        # function deterministic, which replay depends on.
        assert link_span("hives", "hives. later: hives.").span == (0, 5)  # type: ignore[union-attr]

    def test_the_whole_source_is_a_valid_span(self) -> None:
        match = link_span(_SOURCE, _SOURCE)
        assert match is not None
        assert match.span == (0, len(_SOURCE))

    def test_offsets_are_characters_not_bytes(self) -> None:
        # Postgres INT4RANGE and the reviewer's highlight both index characters.
        # A byte offset would drift on any non-ASCII source - an accented name,
        # a typographic dash - and drift silently, pointing a few characters off.
        source = "Señora Alvarez — the patient's PCP since 2019."
        match = link_span("the patient's PCP", source)
        assert match is not None
        assert source[match.span[0] : match.span[1]] == "the patient's PCP"


class TestFuzzyMatches:
    @pytest.mark.parametrize(
        ("verbatim", "why"),
        [
            ("allergic to penicilin", "a dropped letter"),
            ("allergic to penicillin.", "punctuation the model added"),
            (_SMART_QUOTE, "a typographic apostrophe for an ASCII one"),
        ],
    )
    def test_a_near_quote_is_repaired_rather_than_rejected(self, verbatim: str, why: str) -> None:
        match = link_span(verbatim, _SOURCE)
        assert match is not None, why
        assert 0.9 <= match.alignment < 1.0, "a repaired quote is not a perfect one"

    def test_the_stored_text_is_the_source_never_the_claim(self) -> None:
        # ADR-0007. The reviewer's quote and the reviewer's highlight are
        # rendered from different fields and must not be able to disagree.
        match = link_span("allergic to penicilin", _SOURCE)
        assert match is not None
        assert match.text == "allergic to penicillin"
        assert _SOURCE[match.span[0] : match.span[1]] == match.text

    def test_a_fuzzy_span_is_snapped_to_whole_words(self) -> None:
        # The aligner optimises a score, not readability: left alone it returns
        # `allergic to penicilli` here, truncating the word mid-way. Half a word
        # is not a quote a reviewer can act on.
        match = link_span("allergic to penicilin", _SOURCE)
        assert match is not None
        assert not match.text.startswith(" ") and not match.text.endswith(" ")
        assert match.text == "allergic to penicillin"

    def test_a_fuzzy_span_never_starts_or_ends_mid_word(self) -> None:
        match = link_span("I use the CVs on Elm Stret", _SOURCE)
        assert match is not None
        start, end = match.span
        assert start == 0 or _SOURCE[start - 1].isspace()
        assert end == len(_SOURCE) or _SOURCE[end].isspace()
        assert match.text == "I use the CVS on Elm Street"

    def test_a_raw_span_starting_mid_word_is_widened_to_the_word(self) -> None:
        # The aligner returns `icillin - it give` for this claim: both edges
        # inside a word. Snapping walks each out to the nearest boundary.
        match = link_span("icilin - it gives", _SOURCE)
        assert match is not None
        assert match.text == "penicillin - it gives"

    def test_a_raw_span_ending_in_whitespace_is_trimmed(self) -> None:
        # Found by brute-force search rather than by guessing: the aligner
        # returns `e the CVS on Elm Street ` here, with a trailing space and a
        # leading fragment of `use`. Both ends are repaired.
        match = link_span("e the CVS on Elm Strfet ", _SOURCE)
        assert match is not None
        assert match.text == "use the CVS on Elm Street"

    def test_an_exact_match_is_never_snapped(self) -> None:
        # `gives me hive` IS a substring of `gives me hives`, so it takes the
        # exact path and ends mid-word - correctly. Snapping applies only to the
        # fuzzy fallback, where the span is the aligner's guess; on an exact
        # match the model quoted the source literally, and widening the quote it
        # chose would put words in its mouth for no benefit.
        match = link_span("gives me hive", _SOURCE)
        assert match is not None
        assert match.text == "gives me hive"
        assert match.alignment == 1.0


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        ("verbatim", "why"),
        [
            ("allergic to sulfa", "a drug the source never mentions"),
            ("the patient denies any drug allergies", "wholly invented - the confabulation case"),
            ("allergic to penicillin and Dr. Alvarez", "two spans joined into one claim"),
            ("PCQ", "a short needle one character wrong"),
            ("", "quotes nothing at all"),
        ],
    )
    def test_a_claim_the_source_does_not_support_is_unsourced(
        self, verbatim: str, why: str
    ) -> None:
        assert link_span(verbatim, _SOURCE) is None, why

    def test_an_inverted_claim_is_below_the_bar_but_not_by_much(self) -> None:
        # `not allergic to penicillin` scores 88.46 against a source that says
        # the opposite. It is rejected here - but on a longer sentence the same
        # inversion would pass, and that is not this module's job to catch.
        # §2.2's NLI compares meaning; the span rule only claims the text is
        # there. Pinned so nobody mistakes this for negation handling.
        assert link_span("not allergic to penicillin", _SOURCE) is None

    @pytest.mark.parametrize(
        ("verbatim", "score", "why"),
        [
            ("Allergic To Penicillin", 86.36, "case differs"),
            ("allergic  to  penicillin", 91.67, "doubled whitespace"),
        ],
    )
    def test_a_benign_paraphrase_below_the_threshold_still_costs_recall(
        self, verbatim: str, score: float, why: str
    ) -> None:
        # Recorded as a measurement, not defended as an ideal. Both are harmless
        # quoting differences and both fall under §1.3's 92. The number is the
        # spec's, and Checkpoint B is where it gets revisited with an AUROC
        # behind it - its own diagnosis says to tighten, not loosen.
        assert link_span(verbatim, _SOURCE) is None, f"{why} scored {score}"

    def test_an_empty_source_sources_nothing(self) -> None:
        assert link_span("anything", "") is None

    def test_an_empty_verbatim_is_not_a_zero_width_span(self) -> None:
        # `"" in source` is trivially true, so a naive `find` hands back (0, 0):
        # a span that satisfies a NOT NULL constraint while quoting nothing.
        # `Provenance` refuses it and `RULES.md` §1.1 treats it as no span at
        # all, so it has to be caught here or it becomes an exception later.
        assert link_span("", _SOURCE) is None

    @pytest.mark.parametrize("verbatim", [" ", "   ", "\t", "\n "])
    def test_whitespace_alone_is_not_a_citation(self, verbatim: str) -> None:
        # A single space IS a substring of almost any source, so this reaches
        # the exact path and would return a span quoting one character of
        # whitespace - non-empty, so `Provenance` accepts it, and a citation of
        # nothing. The original version of this test used three spaces, which
        # are *not* a substring here, so it took the fuzzy path and passed for
        # the wrong reason. The I1 property suite found the real case.
        assert link_span(verbatim, _SOURCE) is None


class TestTheContractWithProvenance:
    @pytest.mark.parametrize("verbatim", ["penicillin", "allergic to penicilin"])
    def test_a_returned_match_always_satisfies_provenance(self, verbatim: str) -> None:
        # `Provenance` rejects negative, inverted and zero-width spans. If this
        # function could produce one, every unsourced fact would surface as a
        # ValidationError at candidate construction rather than as a counted
        # drop - the same failure, reported as a crash.
        match = link_span(verbatim, _SOURCE)
        assert match is not None
        provenance = Provenance(
            source_hash="sha256:0",
            source_span=match.span,
            source_tier=SourceTier.VERIFIED_USER,
            verbatim=match.text,
            alignment=match.alignment,
            captured_at=datetime(2026, 3, 12, tzinfo=UTC),
        )
        assert _SOURCE[provenance.source_span[0] : provenance.source_span[1]] == provenance.verbatim
