"""The anti-hallucination rule, at its narrowest.  S2.2

`MEMORY_ENGINE.md` §1.3: "No span → `REJECT(reason=UNSOURCED)`. This single rule
kills most confabulated facts before any scoring happens."

It works because it is not a judgement. A model that invents a fact must also
invent the sentence it came from, and an invented sentence is not in the source.
So the tests that matter here are the ones that pin how *literal* the match is:
every near-miss below is a case where a looser matcher would attach a real span
to text the source does not contain, and the reviewer would then be shown a
highlight that does not say what the candidate claims.

The fuzzy fallback (rapidfuzz, ratio ≥ 92) is S2.3. Until it lands this
under-matches, which is the safe direction.
"""

from __future__ import annotations

import pytest

from guardmem_core.pipeline.l1_extract.span_linker import link_span
from guardmem_core.schemas import Provenance, SourceTier

_SOURCE = (
    "Patient: I'm allergic to penicillin - it gives me hives. "
    "I use the CVS on Elm Street now, not the one on Main."
)


class TestWhatItFinds:
    def test_a_span_slices_back_to_the_text_it_was_asked_about(self) -> None:
        # The only property that really matters. Everything else is a corollary.
        for verbatim in ("allergic to penicillin", "it gives me hives", "CVS on Elm Street"):
            span = link_span(verbatim, _SOURCE)
            assert span is not None
            assert _SOURCE[span[0] : span[1]] == verbatim

    def test_the_span_is_half_open_and_the_length_matches(self) -> None:
        span = link_span("penicillin", _SOURCE)
        assert span is not None
        assert span[1] - span[0] == len("penicillin")

    def test_the_first_occurrence_wins_when_the_text_repeats(self) -> None:
        # Any occurrence is a true citation; preferring the first keeps the
        # function deterministic, which replay depends on.
        source = "hives. later: hives."
        assert link_span("hives", source) == (0, 5)

    def test_the_whole_source_is_a_valid_span(self) -> None:
        assert link_span(_SOURCE, _SOURCE) == (0, len(_SOURCE))

    def test_offsets_are_characters_not_bytes(self) -> None:
        # Postgres INT4RANGE and the reviewer's highlight both index characters.
        # A byte offset would drift on any non-ASCII source - an accented name,
        # a typographic dash - and drift silently, pointing a few characters off.
        source = "Señora Alvarez — the patient's PCP since 2019."
        span = link_span("the patient's PCP", source)
        assert span is not None
        assert source[span[0] : span[1]] == "the patient's PCP"


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        ("verbatim", "why"),
        [
            ("allergic to sulfa", "a fact the source never contains - the confabulation case"),
            ("Allergic To Penicillin", "case differs; the highlight would not match the quote"),
            ("allergic  to  penicillin", "whitespace differs"),
            ("allergic to penicillin.", "punctuation the model added"),
            ("allergic to penicillin and hives", "two spans joined into one claim"),
            ("", "quotes nothing at all"),
        ],
    )
    def test_a_near_miss_is_unsourced(self, verbatim: str, why: str) -> None:
        assert link_span(verbatim, _SOURCE) is None, why

    def test_an_empty_source_sources_nothing(self) -> None:
        assert link_span("anything", "") is None

    def test_an_empty_verbatim_is_not_a_zero_width_span(self) -> None:
        # `"" in source` is trivially true, so a naive `find` hands back (0, 0):
        # a span that satisfies a NOT NULL constraint while quoting nothing.
        # `Provenance` refuses it and `RULES.md` §1.1 treats it as no span at
        # all, so it has to be caught here or it becomes an exception later.
        assert link_span("", _SOURCE) is None


class TestTheContractWithProvenance:
    def test_a_returned_span_always_satisfies_provenance(self) -> None:
        # `Provenance` rejects negative, inverted and zero-width spans. If this
        # function could produce one, every unsourced fact would surface as a
        # ValidationError at candidate construction rather than as a counted
        # drop - the same failure, reported as a crash.
        from datetime import UTC, datetime

        span = link_span("penicillin", _SOURCE)
        assert span is not None
        provenance = Provenance(
            source_hash="sha256:0",
            source_span=span,
            source_tier=SourceTier.VERIFIED_USER,
            verbatim="penicillin",
            captured_at=datetime(2026, 3, 12, tzinfo=UTC),
        )
        assert provenance.source_span == span
