"""Generative properties of the schema layer.  BUILD_NOTEBOOK.md S1.6

S1.6's DONE WHEN asks for one property: "every model round-trips through
`model_dump_json` -> `model_validate_json`". Two more are asserted alongside,
because they are the other half of what `GMModel`'s configuration claims and
neither is checkable by looking at one hand-written example - `extra="forbid"`
must reject an unknown key on *every* model, and `frozen=True` must refuse
assignment on *every* model.

**The registry below is the point of this file, as much as the properties are.**
`_STRATEGIES` maps every schema to a strategy, and
`test_every_model_has_a_strategy` fails if it and `conftest.all_schema_models()`
ever disagree - so a schema added at a later step without one does not quietly
go untested, the same way `tests/unit/test_errors.py` walks the exception
hierarchy rather than listing it.

`RULES.md` §5 requires 500 examples, which is what `_EXAMPLES` sets.
`deadline=None` is deliberate: these properties are about serialisation
identity, not latency, and a per-example wall-clock budget on a shared CI runner
measures the runner's scheduling rather than the code.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import all_schema_models
from guardmem_core.schemas import (
    AuditEvent,
    Cardinality,
    ConfidenceReport,
    ConflictKind,
    ConflictReport,
    Decision,
    DecisionRecord,
    Diff,
    Edge,
    Entity,
    ExtractionResult,
    GMModel,
    ImpactLevel,
    MemoryCandidate,
    Obligation,
    ObligationKind,
    Provenance,
    ReviewAction,
    ReviewDecision,
    ReviewStatus,
    ReviewTask,
    RiskVerdict,
    SourceTier,
    StoredAssertion,
    WriteReceipt,
)

_EXAMPLES = 500

# --- primitive strategies --------------------------------------------------

_TEXT = st.text(max_size=32)
_ID = st.text(min_size=1, max_size=24)
_UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_COSINE = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_ANY_FLOAT = st.floats(allow_nan=False, allow_infinity=False)

# Naive and UTC-aware both, because pydantic serialises them differently ("...Z"
# or not) and both have to come back as what they were. Sub-minute offsets are
# not generated: ISO-8601 cannot represent them and no store here emits one.
_WHEN = st.datetimes(timezones=st.one_of(st.none(), st.just(UTC)))

# Only what survives a JSON round trip. A `datetime` nested inside one would
# validate, serialise to a string and come back a string - see `schemas/base.py`
# and the test pinning it in tests/unit/test_schema_models.py.
_JSON_VALUE = st.recursive(
    st.none() | st.booleans() | st.integers() | _ANY_FLOAT | st.text(max_size=16),
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=5,
)
_JSON_OBJECT = st.dictionaries(st.text(max_size=8), _JSON_VALUE, max_size=3)

# `ObjectValue`: str | float | bool | dict. A list is not a member, by design.
_OBJECT_VALUE = st.one_of(_TEXT, _ANY_FLOAT, st.booleans(), _JSON_OBJECT)

# Non-negative and strictly increasing, as `Provenance` requires.
_SPAN = st.tuples(
    st.integers(min_value=0, max_value=10_000),
    st.integers(min_value=1, max_value=2_000),
).map(lambda pair: (pair[0], pair[0] + pair[1]))


@st.composite
def _ordered_datetimes(draw: st.DrawFn) -> tuple[datetime, datetime]:
    """Two datetimes in order, and comparable with each other.

    Both are drawn with the same tz-awareness on purpose: Python raises
    `TypeError` when a naive datetime is compared with an aware one, so a mixed
    pair would crash the sort here rather than exercise the validator.
    """
    tz_strategy = draw(st.sampled_from([st.none(), st.just(UTC)]))
    pair = draw(st.lists(st.datetimes(timezones=tz_strategy), min_size=2, max_size=2))
    pair.sort()
    return pair[0], pair[1]


# --- model strategies ------------------------------------------------------

_PROVENANCE = st.builds(
    Provenance,
    source_hash=_TEXT,
    source_span=_SPAN,
    source_tier=st.sampled_from(SourceTier),
    verbatim=st.text(max_size=2000),
    captured_at=_WHEN,
)


@st.composite
def _memory_candidates(draw: st.DrawFn) -> MemoryCandidate:
    """A candidate whose validity interval, when present, is not inverted."""
    start, end = draw(_ordered_datetimes())
    return MemoryCandidate(
        candidate_id=draw(_ID),
        tenant_id=draw(_ID),
        namespace=draw(_ID),
        subject=draw(_TEXT),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        valid_from=start if draw(st.booleans()) else None,
        valid_to=end if draw(st.booleans()) else None,
        provenance=draw(_PROVENANCE),
        extracted_by=draw(_ID),
        prompt_version=draw(_ID),
        trace_id=draw(_ID),
    )


@st.composite
def _stored_assertions(draw: st.DrawFn) -> StoredAssertion:
    """A stored assertion with at least one provenance, as RULES 1.1 demands."""
    start, end = draw(_ordered_datetimes())
    return StoredAssertion(
        assertion_id=draw(_ID),
        tenant_id=draw(_ID),
        namespace=draw(_ID),
        subject_id=draw(_ID),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        confidence=draw(_UNIT),
        risk=draw(_UNIT),
        valid_from=start,
        valid_to=end if draw(st.booleans()) else None,
        recorded_at=draw(_WHEN),
        retracted_at=draw(st.none() | _WHEN),
        superseded_by=draw(st.none() | _ID),
        provenance=draw(st.lists(_PROVENANCE, min_size=1, max_size=3)),
        corroboration_count=draw(st.integers(min_value=1, max_value=10)),
        trace_id=draw(_ID),
        visible=draw(st.booleans()),
    )


@st.composite
def _edges(draw: st.DrawFn) -> Edge:
    """A graph edge whose validity interval is not inverted."""
    start, end = draw(_ordered_datetimes())
    return Edge(
        assertion_id=draw(_ID),
        subject_id=draw(_ID),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        confidence=draw(_UNIT),
        valid_from=start,
        valid_to=end if draw(st.booleans()) else None,
        trace_id=draw(_ID),
    )


_CONFIDENCE = st.builds(
    ConfidenceReport,
    semantic_entropy=_UNIT,
    grounding=_UNIT,
    schema_fit=_UNIT,
    corroboration=_UNIT,
    consistency=_UNIT,
    confidence=_UNIT,
    weights_version=_ID,
)

_RISK = st.builds(
    RiskVerdict,
    impact_level=st.sampled_from(ImpactLevel),
    risk=_UNIT,
    features=st.dictionaries(st.text(max_size=16), _ANY_FLOAT, max_size=4),
    obligations=st.lists(st.sampled_from([k.value for k in ObligationKind]), max_size=3),
)

_CONFLICT = st.builds(
    ConflictReport,
    kind=st.sampled_from(ConflictKind),
    incumbent_assertion_id=st.none() | _ID,
    entailment=_UNIT,
    contradiction=_UNIT,
    cosine=_COSINE,
    resolution_hint=st.sampled_from(["merge", "supersede", "coexist", "escalate"]),
)

_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    Provenance: _PROVENANCE,
    MemoryCandidate: _memory_candidates(),
    ExtractionResult: st.builds(
        ExtractionResult,
        candidates=st.lists(_memory_candidates(), max_size=3),
        k_samples=st.integers(min_value=1, max_value=5),
        dropped_noise=st.integers(min_value=0, max_value=100),
        tokens_in=st.integers(min_value=0, max_value=10_000),
        tokens_out=st.integers(min_value=0, max_value=10_000),
        cache_hit=st.booleans(),
    ),
    Entity: st.builds(Entity, entity_id=_ID, tenant_id=_ID, type=_ID, canonical_name=_TEXT),
    StoredAssertion: _stored_assertions(),
    Edge: _edges(),
    ConfidenceReport: _CONFIDENCE,
    RiskVerdict: _RISK,
    ConflictReport: _CONFLICT,
    DecisionRecord: st.builds(
        DecisionRecord,
        decision=st.sampled_from(Decision),
        reason_codes=st.lists(_TEXT, max_size=4),
        confidence=_CONFIDENCE,
        risk=_RISK,
        conflict=_CONFLICT,
        thresholds_version=_ID,
        policy_version=_ID,
        escalated_from=st.none() | st.sampled_from(Decision),
    ),
    Obligation: st.builds(Obligation, kind=st.sampled_from(ObligationKind), reason_code=_ID),
    WriteReceipt: st.builds(
        WriteReceipt,
        assertion_id=_ID,
        candidate_id=_ID,
        decision=st.sampled_from(Decision),
        confidence=_UNIT,
        risk=_UNIT,
        trace_id=_ID,
        written_at=_WHEN,
        superseded=st.none() | _ID,
    ),
    AuditEvent: st.builds(
        AuditEvent,
        seq=st.none() | st.integers(min_value=1, max_value=2**31),
        tenant_id=_ID,
        trace_id=_ID,
        kind=st.sampled_from(
            ["DECISION", "WRITE", "REVIEW", "POLICY_CHANGE", "QUARANTINE", "SUPERSEDE"]
        ),
        payload=_JSON_OBJECT,
        prev_digest=_TEXT,
        digest=_TEXT,
        created_at=_WHEN,
    ),
    ReviewTask: st.builds(
        ReviewTask,
        task_id=_ID,
        tenant_id=_ID,
        trace_id=_ID,
        candidate_id=_ID,
        namespace=_ID,
        impact_level=st.sampled_from(ImpactLevel),
        priority=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
        status=st.sampled_from(ReviewStatus),
        created_at=_WHEN,
        sla_due_at=_WHEN,
        assigned_to=st.none() | _ID,
        leased_until=st.none() | _WHEN,
    ),
    ReviewDecision: st.builds(
        ReviewDecision,
        task_id=_ID,
        action=st.sampled_from(ReviewAction),
        reason_code=_ID,
        reviewer_id=_ID,
        decided_at=_WHEN,
        edited_object=st.none() | _OBJECT_VALUE,
        reviewer_note=st.none() | _TEXT,
    ),
    Diff: st.builds(
        Diff,
        proposed=_memory_candidates(),
        incumbent=st.none() | _stored_assertions(),
        cardinality=st.sampled_from(Cardinality),
    ),
}

_ANY_SCHEMA = st.one_of(*_STRATEGIES.values())


def _over_every_schema(check: Callable[[GMModel], None]) -> None:
    """Run `check` over generated instances of the whole layer, and prove it.

    The obvious shape - `@pytest.mark.parametrize` over the models with a
    `@given` on each - was measured and rejected. hypothesis costs ~0.45 s to
    set a test up whatever the example count, so three properties across sixteen
    models pays that 48 times: 198 s for the file at the 500 examples
    `RULES.md` §5 asks for, against 13 s drawing from a union of all sixteen.

    That trades away the guarantee that every model was exercised, so it is
    bought back explicitly - the run records which types it saw and this
    function fails if any schema was missed. Without that, "500 examples over a
    union" is a claim about probability, and a gate should not be one.

    Args:
        check: The property to assert, called once per generated instance.
    """
    exercised: set[type[GMModel]] = set()

    @settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(instance=_ANY_SCHEMA)
    def run(instance: GMModel) -> None:
        exercised.add(type(instance))
        check(instance)

    run()

    missed = all_schema_models() - exercised
    assert not missed, (
        f"{_EXAMPLES} examples never produced: {sorted(m.__name__ for m in missed)}. "
        "The property held, but not for those - so it proves nothing about them."
    )


def test_every_model_has_a_strategy() -> None:
    """The registry above must cover the layer, or the properties below lie.

    A schema added at a later step with no strategy would sail through a suite
    that looks exhaustive. This is the test that notices.
    """
    models = all_schema_models()
    missing = models - set(_STRATEGIES)
    stale = set(_STRATEGIES) - models
    assert not missing, f"schemas with no strategy: {sorted(m.__name__ for m in missing)}"
    assert not stale, f"strategies for schemas that no longer exist: {sorted(stale)}"


def test_every_model_round_trips_through_json() -> None:
    """S1.6's DONE WHEN: `model_validate_json(model_dump_json(m)) == m`.

    This is what makes the audit log replayable and the MCP surface honest. It
    is also the property `strict=True` most threatens - a strictly-typed
    `datetime` field rejects a string in Python and accepts one from JSON, and
    that asymmetry is the only reason the round trip is possible at all.
    """

    def check(instance: GMModel) -> None:
        model = type(instance)
        assert model.model_validate_json(instance.model_dump_json()) == instance

    _over_every_schema(check)


def test_every_model_rejects_an_unknown_field() -> None:
    """`extra="forbid"`, on every model, through the JSON path.

    `RULES.md` §2.1: "a hallucinated field should raise, not vanish silently."
    The JSON path is the one that matters - that is where structured model
    output enters the system.
    """

    def check(instance: GMModel) -> None:
        payload = instance.model_dump(mode="json")
        payload["field_the_model_hallucinated"] = 1

        with pytest.raises(ValueError, match="field_the_model_hallucinated"):
            type(instance).model_validate(payload)

    _over_every_schema(check)


def test_every_model_is_frozen() -> None:
    """`frozen=True`, on every model.

    `RULES.md` §2.1: domain objects are immutable and transform via
    `.model_copy()`. Asserted across the layer rather than once on the base,
    because a module could override `model_config` and nothing else would
    notice.
    """

    def check(instance: GMModel) -> None:
        field = next(iter(type(instance).model_fields))

        with pytest.raises(ValueError, match="frozen"):
            setattr(instance, field, None)

    _over_every_schema(check)
