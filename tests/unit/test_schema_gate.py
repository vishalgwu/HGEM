"""The ontology schema gate.  BUILD_NOTEBOOK.md S4.1

S4.1's DONE WHEN has two halves: "unit tests cover all three paths" and "the
quarantine path never reaches the store router". `TestTheThreePaths` is the
first. The second is `TestQuarantineIsIsolated`, and it is checked twice on
purpose - once as a grouping (`admitted` simply does not contain it) and once as
a namespace rewrite (even a caller that ignored the grouping would write it
somewhere a primary read never looks). `RULES.md` §4 calls that defence in
depth, and a single check here would be the flag-instead-of-namespace mistake
the module docstring argues against.

**The gate runs against the shipped clinical pack, not a fixture ontology.**
That is the whole point of S3.5 existing first, and the S3.6 audit found what
the alternative costs: a hand-written ontology in the tests drifts from the one
production loads, and nothing compares them. So `allergy` is coded RxNorm here
because `ontology/clinical.yaml` says so, and if that changes, this fails.

The coercion cases are chosen for the refusals rather than the acceptances. A
gate that coerces too eagerly is worse than one that rejects too readily:
rejection is visible in the funnel, while a wrong coercion is a stored fact that
reads as if somebody meant it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from guardmem_core.pipeline.l2_validate import GateOutcome, SchemaGateResult, gate
from guardmem_core.schemas import (
    CodedObject,
    MemoryCandidate,
    Provenance,
    SourceTier,
    load_ontology,
)
from guardmem_core.types import CandidateId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue

CLINICAL: Final = load_ontology("clinical")
TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")
NS: Final = Namespace("patient:7781")
WHEN: Final = datetime(2026, 4, 8, 10, 15, tzinfo=UTC)


def candidate(
    *,
    predicate: str = "allergy",
    obj: ObjectValue = "penicillin",
    namespace: Namespace = NS,
    candidate_id: str = "c_1",
) -> MemoryCandidate:
    """A well-formed candidate varying only what the gate reads.

    Local rather than `fixtures.assertions.stored_assertion`: that builds a
    `StoredAssertion`, which is what a candidate *becomes* after governance.
    This layer runs before that, on the model that still carries a surface-form
    subject and no visibility.
    """
    return MemoryCandidate(
        candidate_id=CandidateId(candidate_id),
        tenant_id=TENANT,
        namespace=namespace,
        subject="Joan Ellery",
        predicate=predicate,
        object=obj,
        valid_from=WHEN,
        provenance=Provenance(
            source_hash="sha256:abc",
            source_span=(0, 10),
            source_tier=SourceTier.VERIFIED_USER,
            verbatim="allergic to penicillin",
            captured_at=WHEN,
        ),
        extracted_by="claude-haiku-4-5",
        prompt_version="extract_memories@v1",
        trace_id=TraceId("tr_s41"),
    )


def one(**kwargs: object) -> SchemaGateResult:
    """Gate a single candidate against the clinical pack."""
    return gate([candidate(**kwargs)], CLINICAL)  # type: ignore[arg-type]


class TestTheThreePaths:
    def test_an_exact_fit_passes_at_schema_fit_one(self) -> None:
        """§3.2: "1.0 exact ontology fit". `allergy` is `{type: coded}` and a
        code is a string, so nothing is rewritten."""
        result = one(predicate="allergy", obj="penicillin")

        assert len(result.admitted) == 1
        verdict = result.admitted[0]
        assert verdict.outcome is GateOutcome.PASS
        assert verdict.schema_fit == pytest.approx(1.0)
        assert verdict.reason_code == "SCHEMA_OK"
        assert verdict.candidate.object == "penicillin"

    def test_a_coercible_value_is_rewritten_at_schema_fit_zero_point_seven(self) -> None:
        """The number the step names outright. `weight_kg` is `{type: number}`
        and a model that emitted the figure as text still meant the figure."""
        result = one(predicate="weight_kg", obj="78.5")

        verdict = result.admitted[0]
        assert verdict.outcome is GateOutcome.COERCED
        assert verdict.schema_fit == pytest.approx(0.7)
        assert verdict.reason_code == "SCHEMA_COERCED"
        assert verdict.candidate.object == pytest.approx(78.5)

    def test_an_unknown_predicate_is_quarantined_at_schema_fit_zero_point_four(self) -> None:
        """§3.2's "0.4 unknown-but-plausible". Not rejected: §2.1 keeps it
        retrievable and flagged, because a predicate the ontology has not
        learned yet is a gap in the ontology as often as a bad extraction."""
        result = one(predicate="favourite_colour", obj="blue")

        assert result.admitted == []
        verdict = result.quarantined[0]
        assert verdict.outcome is GateOutcome.QUARANTINED
        assert verdict.schema_fit == pytest.approx(0.4)
        assert verdict.reason_code == "SCHEMA_UNKNOWN_PREDICATE"

    def test_a_value_the_type_cannot_hold_is_rejected_at_schema_fit_zero(self) -> None:
        """§2.1: `REJECT(reason=SCHEMA)`. §3.2 puts it at zero and says it
        never reaches scoring, which is why `rejected` is its own list."""
        result = one(predicate="weight_kg", obj="heavy")

        assert result.admitted == []
        verdict = result.rejected[0]
        assert verdict.outcome is GateOutcome.REJECTED
        assert verdict.schema_fit == pytest.approx(0.0)
        assert verdict.reason_code == "SCHEMA_TYPE"

    def test_the_candidate_that_arrived_is_returned_unchanged_when_rejected(self) -> None:
        """A rejection records what was claimed, not a repaired version of it.
        The review UI shows the reviewer what the model actually said."""
        assert one(predicate="weight_kg", obj="heavy").rejected[0].candidate.object == "heavy"


class TestQuarantineIsIsolated:
    def test_a_quarantined_candidate_is_not_admitted(self) -> None:
        """S4.1's DONE WHEN, first reading: what the caller forwards toward the
        store router is `admitted`, and this is not in it."""
        result = gate([candidate(predicate="favourite_colour")], CLINICAL)

        assert result.admitted == []
        assert len(result.quarantined) == 1

    def test_its_namespace_is_rewritten_to_its_tenants_quarantine(self) -> None:
        """Second reading, and the one that holds if a caller ignores the first.

        A flag would have to be checked by every read path. A namespace is
        simply not the one a primary read asks for, which is the same reason
        the store binds its tenant at construction rather than filtering.
        """
        result = gate([candidate(predicate="favourite_colour")], CLINICAL)

        assert result.quarantined[0].candidate.namespace == f"quarantine:{TENANT}"

    def test_an_admitted_candidate_keeps_its_namespace(self) -> None:
        """The rewrite is not a blanket one - a passing candidate that lost its
        namespace would be quarantined by accident."""
        assert one(predicate="allergy").admitted[0].candidate.namespace == NS

    def test_one_unknown_predicate_does_not_hold_up_the_batch(self) -> None:
        """Per candidate, not per proposal. A batch of ten in which the third is
        unknown must still admit the other nine, or one gap in the ontology
        stalls every fact extracted alongside it."""
        result = gate(
            [
                candidate(predicate="allergy", candidate_id="c_1"),
                candidate(predicate="favourite_colour", candidate_id="c_2"),
                candidate(predicate="preferred_language", obj="English", candidate_id="c_3"),
            ],
            CLINICAL,
        )

        assert [v.candidate.candidate_id for v in result.admitted] == ["c_1", "c_3"]
        assert [v.candidate.candidate_id for v in result.quarantined] == ["c_2"]


class TestCoercionNeverInventsMeaning:
    @pytest.mark.parametrize(
        ("predicate", "obj", "expected"),
        [
            ("preferred_language", 7.0, "7.0"),
            ("preferred_language", True, "True"),
            ("weight_kg", "78.5", 78.5),
            ("consent_flag", "yes", True),
            ("consent_flag", "NO", False),
            ("consent_flag", " true ", True),
        ],
    )
    def test_an_unambiguous_value_is_coerced(
        self, predicate: str, obj: ObjectValue, expected: ObjectValue
    ) -> None:
        """Text takes any scalar; a number takes a numeric string; a boolean
        takes a small, closed vocabulary."""
        verdict = one(predicate=predicate, obj=obj).admitted[0]

        assert verdict.outcome is GateOutcome.COERCED
        assert verdict.candidate.object == expected

    @pytest.mark.parametrize(
        ("predicate", "obj"),
        [
            ("allergy", "penicillin"),
            ("primary_care_provider", "provider-okafor"),
            ("preferred_language", "English"),
            ("weight_kg", 78.5),
            ("consent_flag", True),
            ("consent_flag", False),
        ],
    )
    def test_a_value_already_of_the_declared_type_is_not_touched(
        self, predicate: str, obj: ObjectValue
    ) -> None:
        """One per declared type, because "already the right type" is the case a
        coercion table gets wrong by being too keen - a `boolean` that rewrote
        `False` through its own vocabulary would return `False` and report
        `COERCED`, costing 0.3 of confidence for doing nothing."""
        verdict = one(predicate=predicate, obj=obj).admitted[0]

        assert verdict.outcome is GateOutcome.PASS
        assert verdict.candidate.object == obj

    @pytest.mark.parametrize(
        ("predicate", "obj", "why"),
        [
            ("allergy", 71.5, "stringifying a number into an RxNorm slot invents a code"),
            ("allergy", True, "nor does a boolean name a drug"),
            ("primary_care_provider", 12.0, "an entity ref must already be an id"),
            ("weight_kg", True, "True is not 1.0 in any clinical sense"),
            ("weight_kg", "heavy", "not a number in any reading"),
            ("consent_flag", 1.0, "1.0 is not consent"),
            ("consent_flag", "maybe", "outside the closed vocabulary"),
            ("preferred_language", {"code": "en"}, "a structured object is not text"),
            ("weight_kg", {"kg": 78.5}, "nor is it a number, however numeric it looks inside"),
        ],
    )
    def test_an_ambiguous_value_is_rejected(
        self, predicate: str, obj: ObjectValue, why: str
    ) -> None:
        """Each of these has an obvious-looking coercion, and every one of them
        would store something nobody claimed.

        `bool("no")` being `True` is the reason the boolean vocabulary is a
        closed set rather than a truthiness test, and `True == 1` is the reason
        `number` refuses a bool - both are accidents of Python that would read
        as decisions once they were in a database.
        """
        assert one(predicate=predicate, obj=obj).rejected[0].reason_code == "SCHEMA_TYPE", why


class TestAgainstTheShippedPack:
    def test_the_pack_still_declares_what_these_tests_assume(self) -> None:
        """Pins the two predicates the cases above lean on.

        Without this, a change to `clinical.yaml` turns these tests from "the
        gate coerces correctly" into "the gate coerces something", and the
        failure would point at the gate rather than at the pack.
        """
        allergy = CLINICAL.predicate("allergy")
        weight = CLINICAL.predicate("weight_kg")

        assert allergy is not None
        assert allergy.object == CodedObject(type="coded", system="RxNorm")
        assert weight is not None
        assert weight.object.type == "number"

    def test_an_empty_batch_is_three_empty_lists(self) -> None:
        """A proposal whose candidates were all dropped by Layer 1 is a normal
        outcome and should not make its caller branch."""
        result = gate([], CLINICAL)

        assert (result.admitted, result.quarantined, result.rejected) == ([], [], [])
