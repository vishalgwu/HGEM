"""The adapters between stages.  BUILD_NOTEBOOK.md S5.6

`inputs.py` and `deps.scope_of_namespace` - the pure conversions the
orchestrator does between one layer's output and the next one's input. They have
their own file because they are where the joins actually go wrong, and each is
testable without running a pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from guardmem_core.pipeline.deps import scope_of_namespace
from guardmem_core.pipeline.inputs import claim_text, draws_for, render_content
from guardmem_core.pipeline.l3_score import Scope
from guardmem_core.schemas.candidate import ExtractedFact, MemoryCandidate
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.schemas.turn import Turn, TurnRole
from guardmem_core.types import CandidateId, Namespace, TenantId, TraceId, TurnId

WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)


def turn(text: str) -> Turn:
    return Turn(turn_id=TurnId(text[:8]), role=TurnRole.USER, text=text, captured_at=WHEN)


def candidate(subject: str = "patient:8812", predicate: str = "allergy") -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=CandidateId("c_1"),
        tenant_id=TenantId("11111111-1111-1111-1111-111111111111"),
        namespace=Namespace("patient:8812"),
        subject=subject,
        predicate=predicate,
        object="penicillin",
        provenance=Provenance(
            source_hash="sha256:abc",
            source_span=(0, 10),
            source_tier=SourceTier.VERIFIED_USER,
            verbatim="allergic to penicillin",
            captured_at=WHEN,
        ),
        extracted_by="claude-haiku-4-5",
        prompt_version="extract_memories@v1",
        trace_id=TraceId("tr_1"),
    )


def fact(subject: str, predicate: str, obj: object) -> ExtractedFact:
    return ExtractedFact.model_validate(
        {"subject": subject, "predicate": predicate, "object": obj, "verbatim": "v"}
    )


class TestRenderContent:
    def test_turns_are_newline_joined(self) -> None:
        assert render_content([turn("first"), turn("second")]) == "first\nsecond"

    def test_the_roles_are_not_included(self) -> None:
        """A `"user: "` prefix would be part of the span offsets, so every
        stored span would quote the prefix back at a reviewer."""
        rendered = render_content([turn("hello")])

        assert rendered == "hello"
        assert "user" not in rendered

    def test_an_offset_into_the_rendering_slices_back(self) -> None:
        """The property the whole function exists for: `Provenance.source_span`
        indexes into exactly this string."""
        document = render_content([turn("I am allergic to penicillin"), turn("and latex")])
        start = document.index("penicillin")

        assert document[start : start + len("penicillin")] == "penicillin"

    def test_no_turns_renders_empty(self) -> None:
        assert render_content([]) == ""


class TestDrawsFor:
    def test_it_returns_one_entry_per_draw(self) -> None:
        """Length is §3.1's `K`, always - which is what makes the abstentions
        matter rather than being dropped."""
        samples = [[fact("patient:8812", "allergy", "penicillin")], [], []]

        assert len(draws_for(samples, candidate())) == 3

    def test_a_draw_that_proposed_nothing_is_none(self) -> None:
        samples = [[fact("patient:8812", "allergy", "penicillin")], []]

        assert draws_for(samples, candidate()) == ["penicillin", None]

    def test_a_draw_proposing_a_different_pair_is_also_none(self) -> None:
        """The grouping key is `(subject, predicate)`. Mixing predicates would
        cluster "penicillin" against "CVS Elm Street" and report uncertainty
        that is not there."""
        samples = [[fact("patient:8812", "preferred_pharmacy", "CVS")]]

        assert draws_for(samples, candidate()) == [None]

    def test_a_different_subject_does_not_match_either(self) -> None:
        samples = [[fact("patient:9999", "allergy", "penicillin")]]

        assert draws_for(samples, candidate()) == [None]

    def test_a_draw_proposing_the_pair_twice_contributes_its_first(self) -> None:
        """The one lossy case, and it is real: a model listing two allergies
        emits two facts with the same subject and predicate.

        First rather than last so the answer does not depend on emission order
        within a draw. `MANY`-cardinality predicates are exactly where §3.1's
        "the K samples for a given (subject, predicate)" stops being a
        well-defined set, and the spec does not say which one it means.
        """
        samples = [
            [
                fact("patient:8812", "allergy", "penicillin"),
                fact("patient:8812", "allergy", "latex"),
            ]
        ]

        assert draws_for(samples, candidate()) == ["penicillin"]

    def test_a_structured_object_renders_deterministically(self) -> None:
        """§3.1 compares these for equality of meaning, so two spellings of one
        mapping must not cluster apart on key order alone."""
        samples = [
            [fact("patient:8812", "allergy", {"b": 1, "a": 2})],
            [fact("patient:8812", "allergy", {"a": 2, "b": 1})],
        ]

        drawn = draws_for(samples, candidate())

        assert drawn[0] == drawn[1]


class TestClaimText:
    def test_it_reads_as_a_sentence(self) -> None:
        """One side of §3.2's `S_src` is human-written prose, so the other has
        to look like prose or the entailment score measures the formatting."""
        text = claim_text(candidate(predicate="primary_care_provider"))

        assert "primary care provider" in text
        assert "_" not in text

    def test_it_carries_subject_and_object(self) -> None:
        text = claim_text(candidate())

        assert "patient:8812" in text
        assert "penicillin" in text

    def test_it_is_not_embed_text(self) -> None:
        """`embed_text` renders `predicate: object` for *vector* comparison,
        where both sides go through one function and only consistency matters.
        Pinned so the two are not merged by someone tidying up."""
        from guardmem_core.memory.vector.base import embed_text

        assert claim_text(candidate()) != embed_text(candidate())


class TestScopeOfNamespace:
    @pytest.mark.parametrize(
        ("namespace", "expected"),
        [
            ("org:acme", Scope.ORG),
            ("session:xyz", Scope.SESSION),
            ("patient:8812", Scope.USER),
            ("anything-else", Scope.USER),
        ],
    )
    def test_it_reads_the_documented_prefixes(self, namespace: str, expected: Scope) -> None:
        """`MEMORY_ENGINE.md` line 50's own examples."""
        assert scope_of_namespace(Namespace(namespace)) is expected

    def test_an_unrecognised_shared_prefix_under_prices_the_blast_radius(self) -> None:
        """The case this default gets wrong, pinned rather than hidden.

        `team:eng` is shared like an organisation and scores as one subject -
        0.5 against 1.0 - so a write into it is priced as reaching less than it
        does. That is why this is a shipped *default* and `CandidateClassifier`
        is the seam a deployment answers for itself.
        """
        assert scope_of_namespace(Namespace("team:eng")) is Scope.USER
