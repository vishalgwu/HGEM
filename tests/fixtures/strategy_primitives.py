"""Shared generative primitives.  S2.2

Split out of `strategies.py` at S2.2, which is when that module reached the
400-line cap `RULES.md` §2.4 sets - flagged one step earlier as the thing the
next schema would break. The seam is deliberate rather than arbitrary: what
lives here is the *vocabulary* every model strategy draws from - bounded floats,
`NewType`-mapped ids, JSON-safe values, well-formed spans - and `strategies.py`
keeps the per-model builders and the registry the property suite walks.

Private by convention (a leading underscore on every name) because nothing
outside `fixtures` should build a `GMModel` from these directly; the registry is
the public surface.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import strategies as st

from guardmem_core.types import (
    AssertionId,
    CandidateId,
    EntityId,
    Namespace,
    ReviewerId,
    ReviewTaskId,
    TenantId,
    TraceId,
    TurnId,
)

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
_TURN_IDS = _ID.map(TurnId)

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
