"""Hypothesis strategies for every schema in the layer.  S1.7

Moved here from `tests/property/test_schemas.py` at S1.7, for two reasons. The
directory is what `PROJECT_TREE.md` reserves for shared test material and this
is the step that creates it; and the property module was at the 400-line cap
`RULES.md` §2.4 sets, with three quarters of it being data generation rather
than the properties themselves.

**Ids are `NewType`-typed here, not bare `str`.** `tests/` is covered by
`make typecheck` as of S1.7, and the first thing that found was fourteen places
where these strategies fed a raw `str` into a field declared `CandidateId` or
`TenantId` - the exact mistake `RULES.md` §2.1 built those types to prevent,
in the suite whose job is to prove the layer holds.

`SCHEMA_STRATEGIES` is the registry; `tests/property/test_schemas.py` fails if
it and the set of models in the package ever disagree, so a schema added without
a strategy cannot go quietly untested.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import strategies as st

from guardmem_core.llm.base import LLMResponse
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
from guardmem_core.types import (
    AssertionId,
    CandidateId,
    EntityId,
    Namespace,
    ReviewerId,
    ReviewTaskId,
    TenantId,
    TraceId,
)

__all__ = ["ANY_SCHEMA", "SCHEMA_STRATEGIES"]

# --- primitives ------------------------------------------------------------

_TEXT = st.text(max_size=32)
_ID = st.text(min_size=1, max_size=24)
_UNIT = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_COSINE = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_ANY_FLOAT = st.floats(allow_nan=False, allow_infinity=False)

_ASSERTION_IDS = _ID.map(AssertionId)
_CANDIDATE_IDS = _ID.map(CandidateId)
_ENTITY_IDS = _ID.map(EntityId)
_NAMESPACES = _ID.map(Namespace)
_REVIEWER_IDS = _ID.map(ReviewerId)
_REVIEW_TASK_IDS = _ID.map(ReviewTaskId)
_TENANT_IDS = _ID.map(TenantId)
_TRACE_IDS = _ID.map(TraceId)

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
    `TypeError` comparing a naive datetime with an aware one, so a mixed pair
    would crash the sort here rather than exercise the validator.
    """
    tz_strategy = draw(st.sampled_from([st.none(), st.just(UTC)]))
    pair = draw(st.lists(st.datetimes(timezones=tz_strategy), min_size=2, max_size=2))
    pair.sort()
    return pair[0], pair[1]


# --- models ----------------------------------------------------------------

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
        candidate_id=draw(_CANDIDATE_IDS),
        tenant_id=draw(_TENANT_IDS),
        namespace=draw(_NAMESPACES),
        subject=draw(_TEXT),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        valid_from=start if draw(st.booleans()) else None,
        valid_to=end if draw(st.booleans()) else None,
        provenance=draw(_PROVENANCE),
        extracted_by=draw(_ID),
        prompt_version=draw(_ID),
        trace_id=draw(_TRACE_IDS),
    )


@st.composite
def _stored_assertions(draw: st.DrawFn) -> StoredAssertion:
    """A stored assertion with at least one provenance, as RULES 1.1 demands."""
    start, end = draw(_ordered_datetimes())
    return StoredAssertion(
        assertion_id=draw(_ASSERTION_IDS),
        tenant_id=draw(_TENANT_IDS),
        namespace=draw(_NAMESPACES),
        subject_id=draw(_ENTITY_IDS),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        confidence=draw(_UNIT),
        risk=draw(_UNIT),
        valid_from=start,
        valid_to=end if draw(st.booleans()) else None,
        recorded_at=draw(_WHEN),
        retracted_at=draw(st.none() | _WHEN),
        superseded_by=draw(st.none() | _ASSERTION_IDS),
        provenance=draw(st.lists(_PROVENANCE, min_size=1, max_size=3)),
        corroboration_count=draw(st.integers(min_value=1, max_value=10)),
        trace_id=draw(_TRACE_IDS),
        visible=draw(st.booleans()),
    )


@st.composite
def _edges(draw: st.DrawFn) -> Edge:
    """A graph edge whose validity interval is not inverted."""
    start, end = draw(_ordered_datetimes())
    return Edge(
        assertion_id=draw(_ASSERTION_IDS),
        subject_id=draw(_ENTITY_IDS),
        predicate=draw(_ID),
        object=draw(_OBJECT_VALUE),
        confidence=draw(_UNIT),
        valid_from=start,
        valid_to=end if draw(st.booleans()) else None,
        trace_id=draw(_TRACE_IDS),
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
    incumbent_assertion_id=st.none() | _ASSERTION_IDS,
    entailment=_UNIT,
    contradiction=_UNIT,
    cosine=_COSINE,
    resolution_hint=st.sampled_from(["merge", "supersede", "coexist", "escalate"]),
)

SCHEMA_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
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
    Entity: st.builds(
        Entity, entity_id=_ENTITY_IDS, tenant_id=_TENANT_IDS, type=_ID, canonical_name=_TEXT
    ),
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
        assertion_id=_ASSERTION_IDS,
        candidate_id=_CANDIDATE_IDS,
        decision=st.sampled_from(Decision),
        confidence=_UNIT,
        risk=_UNIT,
        trace_id=_TRACE_IDS,
        written_at=_WHEN,
        superseded=st.none() | _ASSERTION_IDS,
    ),
    AuditEvent: st.builds(
        AuditEvent,
        seq=st.none() | st.integers(min_value=1, max_value=2**31),
        tenant_id=_TENANT_IDS,
        trace_id=_TRACE_IDS,
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
        task_id=_REVIEW_TASK_IDS,
        tenant_id=_TENANT_IDS,
        trace_id=_TRACE_IDS,
        candidate_id=_CANDIDATE_IDS,
        namespace=_NAMESPACES,
        impact_level=st.sampled_from(ImpactLevel),
        priority=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
        status=st.sampled_from(ReviewStatus),
        created_at=_WHEN,
        sla_due_at=_WHEN,
        assigned_to=st.none() | _REVIEWER_IDS,
        leased_until=st.none() | _WHEN,
    ),
    ReviewDecision: st.builds(
        ReviewDecision,
        task_id=_REVIEW_TASK_IDS,
        action=st.sampled_from(ReviewAction),
        reason_code=_ID,
        reviewer_id=_REVIEWER_IDS,
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
    # S1.7. A `GMModel` outside `guardmem_core.schemas`, and the reason the
    # registry walk now imports the whole package before looking: nothing in the
    # suite imported `llm.base`, so every "every model is covered" guard passed
    # while silently not covering this one.
    LLMResponse: st.builds(
        LLMResponse,
        samples=st.lists(_TEXT, min_size=1, max_size=5),
        model=_ID,
        temperature=st.floats(min_value=0.0, max_value=2.0, allow_nan=False),
        seed=st.none() | st.integers(min_value=0, max_value=2**31),
        tokens_in=st.integers(min_value=0, max_value=100_000),
        tokens_out=st.integers(min_value=0, max_value=100_000),
        cache_hit=st.booleans(),
        latency_ms=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
        cost_usd=st.floats(min_value=0.0, max_value=1e3, allow_nan=False),
    ),
}

ANY_SCHEMA = st.one_of(*SCHEMA_STRATEGIES.values())
