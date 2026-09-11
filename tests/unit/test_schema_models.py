"""Example-based tests for the schema layer.  BUILD_NOTEBOOK.md S1.6

The generative half lives in `tests/property/test_schemas.py`: round-tripping,
`extra="forbid"` and `frozen=True`, asserted over every model. This file holds
what a strategy cannot express - the named cases S1.6 asks for, every validator
branch, and the handful of pydantic behaviours that are surprising enough to be
pinned so the next person does not rediscover them at a bad moment.

The name is `test_schema_models.py`, not `test_schemas.py`, and it has to be.
`tests/` has no `__init__.py` anywhere in it, so pytest imports every test
module under its bare basename; two files called `test_schemas.py` in different
suites collide at collection with "import file mismatch" and the whole run
fails, not just the pair. S1.6 names `tests/property/test_schemas.py`
explicitly, so that one keeps the name and this one takes a distinct basename.

Three of those pins deserve a word, because each one looks like a bug and is
not:

1. **`object=7` becomes `7.0`.** `strict=True` still accepts an `int` where a
   `float` is declared. `MEMORY_ENGINE.md` §0 declares the value type as
   `float`, so this is the spec being honoured rather than bypassed - but it is
   a silent conversion inside a model configured specifically to forbid silent
   conversions.
2. **`hash()` raises on some models and not others.** `frozen=True` makes a
   model hashable, and a field holding a `list` or `dict` makes it unhashable
   again. So `Provenance` can go in a set and `ExtractionResult` cannot.
3. **`AuditEvent.payload` can hold a value that does not survive its own
   serialisation.** The field is `dict[str, object]` per the step, and a
   `datetime` nested inside validates fine, dumps to a string, and comes back a
   string. The audit chain digests canonical JSON (S5.5), so this is the one
   field where that asymmetry would break invariant I5 for every later link.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from conftest import all_schema_models
from guardmem_core import schemas
from guardmem_core.schemas import (
    AuditEvent,
    Cardinality,
    ExtractionResult,
    ImpactLevel,
    MemoryCandidate,
    ObligationKind,
    Provenance,
    RiskVerdict,
    SourceTier,
    StoredAssertion,
)
from guardmem_core.types import TenantId, TraceId

_WHEN = datetime(2026, 3, 12, 14, 31, 2, tzinfo=UTC)
_LATER = _WHEN + timedelta(days=30)

_PROVENANCE = Provenance(
    source_hash="sha256:9c1",
    source_span=(212, 271),
    source_tier=SourceTier.VERIFIED_USER,
    verbatim="...penicillin - it gives me hives...",
    captured_at=_WHEN,
)

_CANDIDATE_FIELDS: dict[str, object] = {
    "candidate_id": "c_1",
    "tenant_id": "t_acme",
    "namespace": "patient:8812",
    "subject": "patient:8812",
    "predicate": "allergy",
    "object": "penicillin",
    "provenance": _PROVENANCE,
    "extracted_by": "claude-haiku-4-5",
    "prompt_version": "extract_memories/v1",
    "trace_id": "tr_9f2a3c",
}

_ASSERTION_FIELDS: dict[str, object] = {
    "assertion_id": "a_9d33",
    "tenant_id": "t_acme",
    "namespace": "patient:8812",
    "subject_id": "e_8812",
    "predicate": "allergy",
    "object": "penicillin",
    "confidence": 0.94,
    "risk": 0.21,
    "valid_from": _WHEN,
    "recorded_at": _WHEN,
    "provenance": [_PROVENANCE],
    "trace_id": "tr_9f2a3c",
}


def _candidate(**overrides: object) -> MemoryCandidate:
    """A valid candidate with the named fields replaced."""
    return MemoryCandidate(**{**_CANDIDATE_FIELDS, **overrides})  # type: ignore[arg-type]


def _assertion(**overrides: object) -> StoredAssertion:
    """A valid stored assertion with the named fields replaced."""
    return StoredAssertion(**{**_ASSERTION_FIELDS, **overrides})  # type: ignore[arg-type]


# --- the layer's public surface -------------------------------------------


def test_every_schema_is_exported_from_the_package() -> None:
    """A model that exists but is not exported is invisible to every consumer.

    `guardmem_core.schemas` is the one import site for this layer, so a schema
    left out of `__all__` is one that downstream code reaches for through a
    submodule path instead - and the layer quietly grows two ways in.

    Scoped to models *defined under* `guardmem_core.schemas`, because from S1.7
    a `GMModel` may legitimately live elsewhere: `LLMResponse` belongs with the
    protocol that returns it, in `llm/base.py`. The rule is "no schema module
    hides a model", not "every model lives in schemas".
    """
    unexported = {
        model.__name__
        for model in all_schema_models()
        if model.__module__.startswith("guardmem_core.schemas")
        and model.__name__ not in schemas.__all__
    }

    assert not unexported, (
        f"schemas missing from guardmem_core.schemas.__all__: {sorted(unexported)}"
    )


def test_every_exported_name_resolves() -> None:
    """`__all__` must not promise a name the package does not have."""
    missing = [name for name in schemas.__all__ if not hasattr(schemas, name)]

    assert not missing, f"__all__ names nothing resolves to: {missing}"


# --- S1.6's DONE WHEN ------------------------------------------------------


def test_memory_candidate_rejects_an_unknown_field() -> None:
    """S1.6's DONE WHEN, literally.

    This is the LLM structured-output case `RULES.md` §2.1 cares about: a
    hallucinated field must raise rather than vanish.
    """
    with pytest.raises(ValidationError, match="bogus"):
        MemoryCandidate(**{**_CANDIDATE_FIELDS, "bogus": 1})  # type: ignore[arg-type]


# --- strict-mode behaviour worth pinning ----------------------------------


def test_an_enum_value_is_rejected_from_python_and_accepted_from_json() -> None:
    """The asymmetry that makes strict models round-trippable at all.

    In Python, a bare `"verified_user"` where a `SourceTier` belongs is a type
    error - that is `strict=True` doing its job. From JSON it is accepted,
    because JSON has no way to carry an enum member, and without that relaxation
    nothing in this layer could be deserialised.
    """
    with pytest.raises(ValidationError):
        Provenance(**{**_PROVENANCE.model_dump(), "source_tier": "verified_user"})

    from_json = Provenance.model_validate_json(_PROVENANCE.model_dump_json())

    assert from_json.source_tier is SourceTier.VERIFIED_USER


def test_an_integer_object_is_silently_widened_to_float() -> None:
    """Pinned because it is surprising, not because it is wanted.

    `strict=True` forbids `"1"` -> `1`, but pydantic still accepts an `int`
    where a `float` is declared. `MEMORY_ENGINE.md` §0 types the object value as
    `float`, so a dosage proposed as `500` is stored as `500.0`. It round-trips
    stably and it is what the spec asks for - but anyone reading `strict=True`
    and expecting no conversions at all should find this test first.
    """
    candidate = _candidate(object=500)

    assert candidate.object == 500.0
    assert isinstance(candidate.object, float)


def test_a_list_is_not_a_valid_object_value() -> None:
    """`MEMORY_ENGINE.md` §0 lists four value shapes and an array is not one.

    A multi-valued fact is several assertions under a `MANY`-cardinality
    predicate, which is what makes each one separately sourced, scored and
    retractable. Collapsing them into one list-valued assertion would give the
    whole group a single confidence and a single provenance.
    """
    with pytest.raises(ValidationError):
        _candidate(object=["penicillin", "sulfa"])


def test_a_boolean_object_stays_a_boolean() -> None:
    """Union order matters: `True` must not arrive as `1.0`."""
    assert _candidate(object=True).object is True


def test_freezing_makes_a_model_hashable_only_if_its_fields_are() -> None:
    """The limit of `frozen=True`, pinned so it is not discovered in a set().

    `Provenance` holds only scalars and hashes. `ExtractionResult` holds a list,
    and pydantic's frozen `__hash__` hashes the field values, so it raises.
    """
    assert isinstance(hash(_PROVENANCE), int)

    empty_extraction = ExtractionResult(
        candidates=[], k_samples=1, dropped_noise=0, tokens_in=0, tokens_out=0, cache_hit=False
    )
    with pytest.raises(TypeError, match="unhashable"):
        hash(empty_extraction)


def test_an_audit_payload_can_hold_a_value_that_does_not_round_trip() -> None:
    """The one place `dict[str, object]` is genuinely dangerous.

    S5.5 digests canonical JSON of the payload, so a value that changes across
    serialisation changes the digest and breaks invariant I5 for every later
    link. Not forbidden here - the step declares the field as `dict[str,
    object]` and a validator would cost a serialisation on every construction -
    but recorded, because S5.5 is where it has to be handled.
    """
    event = AuditEvent(
        tenant_id=TenantId("t_acme"),
        trace_id=TraceId("tr_9f2a3c"),
        kind="DECISION",
        payload={"decided_at": _WHEN},
        prev_digest="0" * 64,
        digest="a" * 64,
        created_at=_WHEN,
    )

    assert AuditEvent.model_validate_json(event.model_dump_json()) != event


# --- validators ------------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ((-1, 10), "non-negative"),
        ((10, 10), "half-open"),
        ((271, 212), "half-open"),
    ],
    ids=["negative-offset", "zero-width", "inverted"],
)
def test_a_span_that_points_at_nothing_is_rejected(span: tuple[int, int], expected: str) -> None:
    """`RULES.md` §1.1: no unsourced write, and a span that quotes nothing is
    unsourced in every way that matters.

    Postgres stores this as `INT4RANGE` at S3.1 and would reject it there - on
    the far side of scoring, long after the span linker that produced it.
    """
    with pytest.raises(ValidationError, match=expected):
        Provenance(**{**_PROVENANCE.model_dump(), "source_span": span})


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    [(None, None), (_WHEN, None), (None, _LATER), (_WHEN, _LATER), (_WHEN, _WHEN)],
    ids=["neither", "open-ended", "no-start", "ordered", "instantaneous"],
)
def test_a_coherent_validity_interval_is_accepted(
    valid_from: datetime | None, valid_to: datetime | None
) -> None:
    """Every shape a real fact takes, including "still true" and "never had a
    recorded start"."""
    candidate = _candidate(valid_from=valid_from, valid_to=valid_to)

    assert candidate.valid_from == valid_from
    assert candidate.valid_to == valid_to


def test_a_candidate_cannot_stop_being_true_before_it_starts() -> None:
    """An inverted interval intersects nothing, so `MEMORY_ENGINE.md` §2.2(c)
    silently fails to fire and two contradictory facts coexist."""
    with pytest.raises(ValidationError, match="cannot run backwards"):
        _candidate(valid_from=_LATER, valid_to=_WHEN)


def test_a_stored_assertion_cannot_stop_being_true_before_it_starts() -> None:
    """Same rule on the durable side, where supersession writes
    `prior.valid_to = candidate.valid_from`."""
    with pytest.raises(ValidationError, match="cannot run backwards"):
        _assertion(valid_to=_WHEN - timedelta(days=1))


def test_a_stored_assertion_requires_at_least_one_provenance() -> None:
    """`RULES.md` non-negotiable #1: a persisted assertion with no source is a
    P0 bug. Enforced here, by a `NOT NULL` constraint at S3.1, and by the
    property suite."""
    with pytest.raises(ValidationError, match="at least 1 item"):
        _assertion(provenance=[])


def test_a_stored_assertion_defaults_to_one_source_and_invisible() -> None:
    """The two defaults that are load-bearing rather than convenient.

    `visible=False` is what `ARCHITECTURE.md` §2.4 relies on: the row exists
    before the graph side lands and must not be retrievable until it does.
    `corroboration_count=1` is the honest starting value - one source, `S_cor`
    of 0.0.
    """
    assertion = _assertion()

    assert assertion.visible is False
    assert assertion.corroboration_count == 1


@pytest.mark.parametrize(
    "obligations",
    [[], [ObligationKind.REQUIRE_REVIEW.value], [k.value for k in ObligationKind]],
    ids=["none", "one", "all"],
)
def test_known_obligations_are_accepted(obligations: list[str]) -> None:
    """The vocabulary `ObligationKind` defines, in every quantity."""
    verdict = RiskVerdict(
        impact_level=ImpactLevel.LOW, risk=0.1, features={}, obligations=obligations
    )

    assert verdict.obligations == obligations


def test_an_unknown_obligation_is_rejected() -> None:
    """A typo here is silently inert - S5.4 composes what it recognises and
    ignores the rest, leaving the auto-write path open with nothing in the
    record to show for it."""
    with pytest.raises(ValidationError, match="unknown obligations"):
        RiskVerdict(
            impact_level=ImpactLevel.CRITICAL,
            risk=0.9,
            features={},
            obligations=["require_reviewi"],
        )


# --- bounds ----------------------------------------------------------------


@pytest.mark.parametrize("value", [-0.01, 1.01], ids=["below", "above"])
def test_a_probability_outside_the_unit_interval_is_rejected(value: float) -> None:
    """Every score in this layer is declared on [0,1] by `MEMORY_ENGINE.md` §3,
    and a confidence of 1.4 would walk straight through the decision matrix."""
    with pytest.raises(ValidationError):
        _assertion(confidence=value)


@pytest.mark.parametrize("value", [-1.0, 0.0, 1.0], ids=["opposed", "orthogonal", "identical"])
def test_cosine_may_be_negative(value: float) -> None:
    """Bounded at -1, not 0. Cosine over unnormalised embeddings is genuinely
    negative sometimes, and clamping would hide it from `S_con` and the tuner."""
    report = schemas.ConflictReport(
        kind=schemas.ConflictKind.NONE,
        incumbent_assertion_id=None,
        entailment=0.0,
        contradiction=0.0,
        cosine=value,
        resolution_hint="coexist",
    )

    assert report.cosine == value


def test_cardinality_one_is_the_value_invariant_i2_is_written_against() -> None:
    """Pinned because the string is a database value and an ontology YAML key.

    `MEMORY_ENGINE.md` §2.1's starter pack writes `cardinality: one`, and
    invariant I2 is stated in terms of it. Renaming the member would break the
    ontology files silently.
    """
    assert Cardinality.ONE.value == "one"
    assert Cardinality.ONE_PER_TIME.value == "one_per_time"
