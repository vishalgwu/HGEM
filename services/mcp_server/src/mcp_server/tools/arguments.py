"""Reading §2.1's arguments, and refusing the ones that are wrong.  S6.2

Split out of `search.py` at the point `RULES.md` §2.4's 400-line cap bit, and
along a seam that was already drawn in that file as a comment divider: **what a
tool call said** is a different job from **what the store is asked**. This module
turns a `dict[str, Any]` that arrived over JSON-RPC into typed, bounded values;
`search.py` and `get_entity.py` then do their work knowing every input is sound.

**Every check here is one the `inputSchema` looks like it already makes, and
does not.** The MCP SDK validates the request *envelope* - that `arguments` is
an object - and leaves the property types to the server. So a client sending
`{"limit": "10"}` reaches this module with a string, and a `{"namespace": 42}`
reaches it with an int. Publishing a schema is a promise to the caller about
what will be accepted; keeping it is this file.

**Refusal rather than coercion, throughout.** `int("10")` would work and would
hide a client's bug until the day it sends `"ten"`. The one exception is
documented where it happens: a `limit` above §2.1's published `maximum` is
clamped, because the schema states the ceiling and a caller that asked for more
has read it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from mcp_server.tools.context import ToolRefusedError

__all__ = [
    "bounded_limit",
    "min_confidence",
    "parse_as_of",
    "require_query",
    "store_filters",
    "token_budget",
]

# §2.1's own defaults, restated as values because the `inputSchema` `default`
# keyword is advisory: JSON Schema does not fill a missing property in, and the
# MCP SDK does not either, so a client that omits `limit` sends nothing at all.
_DEFAULT_MIN_CONFIDENCE: Final = 0.6
_DEFAULT_LIMIT: Final = 10
_MAX_LIMIT: Final = 50
_DEFAULT_TOKEN_BUDGET: Final = 1500


#
# Each of these is a type check the `inputSchema` looks like it already makes.
# It does not: the SDK validates that `arguments` is an object and leaves the
# property types to the server, so every value below arrives as whatever the
# client sent. `ToolRefusedError` rather than a coercion, because a client sending
# `{"limit": "10"}` has a bug and a silent `int("10")` hides it.


def require_query(arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolRefusedError("`query` is required and must be a non-empty string")
    return query


def bounded_limit(arguments: dict[str, Any]) -> int:
    limit = arguments.get("limit", _DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ToolRefusedError(f"`limit` must be an integer, got {type(limit).__name__}")
    if limit < 1:
        raise ToolRefusedError(f"`limit` must be at least 1, got {limit}")
    # §2.1 publishes `maximum: 50`. Clamped rather than refused: the schema
    # states the ceiling, so a client asking for more has read it and wants "as
    # many as you will give me", and failing the call teaches nothing.
    return min(limit, _MAX_LIMIT)


def min_confidence(arguments: dict[str, Any]) -> float:
    value = arguments.get("min_confidence", _DEFAULT_MIN_CONFIDENCE)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolRefusedError(f"`min_confidence` must be a number, got {type(value).__name__}")
    if not 0.0 <= float(value) <= 1.0:
        raise ToolRefusedError(f"`min_confidence` must be in [0, 1], got {value}")
    return float(value)


def token_budget(arguments: dict[str, Any]) -> int:
    value = arguments.get("token_budget", _DEFAULT_TOKEN_BUDGET)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolRefusedError(f"`token_budget` must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ToolRefusedError(f"`token_budget` must be at least 1, got {value}")
    return value


def parse_as_of(arguments: dict[str, Any]) -> datetime | None:
    """§2.1's point-in-time query, and §2.4's.

    Public for the same reason `assertion_view` is: both tools publish an
    `as_of` and both must read it identically, down to refusing a naive one.

    Raises:
        ToolRefusedError: not a string, not ISO-8601, or naive.

    A naive datetime is refused rather than assumed to be UTC. Every timestamp
    in the store is timezone-aware, and comparing a naive one against them
    raises inside asyncpg with a message about types - but only for *some*
    inputs, since the comparison is only reached when rows match. Refusing here
    makes it deterministic.
    """
    raw = arguments.get("as_of")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ToolRefusedError(f"`as_of` must be an ISO-8601 string, got {type(raw).__name__}")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ToolRefusedError(f"`as_of` is not a valid ISO-8601 timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise ToolRefusedError(
            f"`as_of` must carry a timezone offset, got {raw!r}. Every timestamp "
            "in the store is timezone-aware and a naive one has no defined instant."
        )
    return parsed


def store_filters(arguments: dict[str, Any]) -> dict[str, object]:
    """Turn §2.1's `subject` and `predicates` into store filters.

    Raises:
        ToolRefusedError: either is present with the wrong type.

    **`subject` is an entity id, not a surface form**, and the schema's
    "Optional entity filter" is doing more work than it looks like.
    `assertion.subject_id` is a `uuid` column and the store casts the filter to
    one, so "Joan Ellery" cannot be passed here - it has to be resolved first,
    and entity resolution is specified in no document and implemented nowhere.
    A caller that has an id (from `memory.get_entity`, or from the seed) can
    filter; a caller that has a name cannot, and gets told so rather than an
    empty result.
    """
    filters: dict[str, object] = {}
    subject = arguments.get("subject")
    if subject is not None:
        if not isinstance(subject, str):
            raise ToolRefusedError(f"`subject` must be a string, got {type(subject).__name__}")
        _reject_surface_form(subject)
        filters["subject_id"] = subject
    predicates = arguments.get("predicates")
    if predicates is not None:
        if not isinstance(predicates, list) or not all(isinstance(p, str) for p in predicates):
            raise ToolRefusedError("`predicates` must be an array of strings")
        if predicates:
            filters["predicate"] = predicates
    return filters


def _reject_surface_form(subject: str) -> None:
    """Refuse a `subject` that is plainly a name rather than an entity id.

    Raises:
        ToolRefusedError: `subject` is not a UUID.

    Without this the call reaches asyncpg, which raises on the `::uuid` cast
    with a message about invalid input syntax - true, and unhelpful to an agent
    that passed the patient's name because the field is called `subject`.
    """
    try:
        UUID(subject)
    except ValueError as exc:
        raise ToolRefusedError(
            f"`subject` must be a resolved entity id (a UUID), got {subject!r}. "
            "It is an entity filter rather than a name: assertion.subject_id is "
            "a uuid column. Entity resolution - turning a surface form like "
            "'Joan Ellery' into an id - is specified in no document and "
            "implemented nowhere yet, so a name cannot be resolved here. Use "
            "memory.get_entity, or omit `subject` and filter by `predicates`."
        ) from exc
