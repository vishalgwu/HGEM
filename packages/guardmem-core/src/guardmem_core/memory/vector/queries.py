"""How a read query over `assertion` is assembled.  S3.2, widened at S6.2

The third seam in this directory, and the one `rowmap.py` predicted when it
said the statements encoding *shape* live beside the column constant while the
statements encoding *behaviour* stay in the store. That split held for three
steps. S6.2 broke it by adding a second read - `retired`, for
`MCP_INTEGRATION.md` §2.1's `excluded` array - and the store went past
`RULES.md` §2.4's 400-line cap.

Shaving it would have been the wrong repair. The seam that was already there is
this: **composing a query is a different job from running one.** `PgVectorStore`
opens a transaction with a tenant applied, hands parameters to asyncpg and maps
rows back; this module decides which columns a caller is allowed to mean, which
predicates a temporal question implies, and how the placeholders are numbered.
They fail differently, too - a bug here is a wrong or unsafe *query*, a bug
there is a wrong *connection*.

**This is the module where `RULES.md` §4 could be broken**, which is the real
argument for it having a name. The rule is that SQL is parameterised, and the
way it gets broken by accident is a caller's dict key reaching an f-string as a
column name. Every f-string below interpolates one of three things and nothing
else: a module constant, a column name looked up in `_FILTER_COLUMNS`, or a
`$n` placeholder *number*. Every caller-supplied **value** is bound. Concentrating
that in one small module means the rule can be checked by reading forty lines
rather than four hundred, and it can be tested without a database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core.memory.vector.rowmap import ASSERTION_COLUMNS

if TYPE_CHECKING:
    from datetime import datetime

    from guardmem_core.types import Namespace

__all__ = [
    "SUPERSEDE",
    "nearest_statement",
    "predicates",
    "retired_statement",
]

# `filters` on the protocol is `dict[str, object]`, which is to say it arrives
# from a caller. `RULES.md` §4 forbids string-built SQL, and the way that rule
# gets broken by accident is a dict key reaching an f-string as a column name.
# This maps the vocabulary a caller may use onto the columns it is allowed to
# mean; anything else is a programming error and is raised as one.
#
# `subject_id` and `predicate` are what `MEMORY_ENGINE.md` §2.2's incumbent
# lookup needs, and together with `namespace` they are exactly what
# `assertion_live_idx` covers. Widening this set is a deliberate act that should
# arrive with the index to support it.
_FILTER_COLUMNS: Final[dict[str, str]] = {"subject_id": "subject_id", "predicate": "predicate"}
_UUID_FILTERS: Final = frozenset({"subject_id"})

# Applied by every read: the namespace, the two conditions that make a row
# readable at all, and the one that makes it rankable. `visible` is
# `ARCHITECTURE.md` §2.4's half-written-write guard and `retracted_at` is
# `ADR-0002`'s tombstone; neither moves with a temporal question, because a
# partial write was never true at any time and neither was a retracted one.
_ALWAYS: Final = ("namespace = $1", "visible", "retracted_at IS NULL", "embedding IS NOT NULL")

# What `search` adds when asked about now. Named because `retired_statement`
# replaces exactly this clause with its complement, and a literal repeated in
# two modules is how the two reads drift apart.
LIVE_CLAUSE: Final = "valid_to IS NULL"
RETIRED_CLAUSE: Final = "valid_to IS NOT NULL"

# The S3.2 statement, verbatim from the notebook apart from the casts asyncpg
# needs. `WHERE valid_to IS NULL` is the concurrency control - see
# `PgVectorStore.supersede`.
SUPERSEDE: Final = """
    UPDATE assertion
    SET valid_to = $1, superseded_by = $2::uuid
    WHERE id = $3::uuid AND valid_to IS NULL
"""


def predicates(
    namespace: Namespace, filters: dict[str, object], as_of: datetime | None
) -> tuple[list[str], list[object]]:
    """Build the WHERE clauses shared by every read, and the values they bind.

    Args:
        namespace: Isolation scope. Always `$1`.
        filters: Caller vocabulary, checked against `_FILTER_COLUMNS`.
        as_of: `None` selects live rows. A datetime selects rows whose world
            time contains it.

    Returns:
        The clause fragments and their positional parameters. Every fragment is
        a literal from this module; every *value* is bound.

    Raises:
        KeyError: a filter key is outside `_FILTER_COLUMNS`.
    """
    params: list[object] = [namespace]
    clauses = [*_ALWAYS]
    if as_of is None:
        clauses.append(LIVE_CLAUSE)
    else:
        params.append(as_of)
        # Half-open [valid_from, valid_to), the same convention `Provenance`
        # uses for spans - so the intervals `supersede` leaves abutting have no
        # gap and no overlap at the instant they meet.
        position = len(params)
        clauses.append(
            f"valid_from <= ${position} AND (valid_to IS NULL OR valid_to > ${position})"
        )
    for key, value in filters.items():
        if key not in _FILTER_COLUMNS:
            raise KeyError(
                f"{key!r} is not a searchable column; allowed: {sorted(_FILTER_COLUMNS)}. "
                "Widen _FILTER_COLUMNS together with the index that supports it, "
                "never by interpolating the key."
            )
        params.append(value)
        cast = "::uuid" if key in _UUID_FILTERS else ""
        clauses.append(f"{_FILTER_COLUMNS[key]} = ${len(params)}{cast}")
    return clauses, params


def nearest_statement(clauses: list[str], vector_param: str, limit_param: str) -> str:
    """The k-nearest SELECT, ordered by cosine distance.

    Args:
        clauses: From `predicates`.
        vector_param: The `$n` the query embedding is bound at.
        limit_param: The `$n` `k` is bound at.

    Returns:
        The statement. `<=>` is cosine *distance*; the store converts it to
        similarity at the edge, which is the one place that knows which
        direction the operator runs in.
    """
    return (
        f"SELECT {ASSERTION_COLUMNS}, embedding <=> {vector_param} AS distance "  # noqa: S608
        f"FROM assertion WHERE {' AND '.join(clauses)} "
        f"ORDER BY distance LIMIT {limit_param}"
    )


def retired_statement(clauses: list[str], limit_param: str) -> str:
    """The retired-rows SELECT, newest retirement first.

    Args:
        clauses: From `predicates`, called with `as_of=None` - this swaps that
            call's live clause for its complement rather than taking a second
            temporal argument, so the two reads cannot drift into disagreeing
            about what "readable" means.
        limit_param: The `$n` the row limit is bound at.

    Returns:
        The statement.

    Raises:
        ValueError: `clauses` does not carry the live clause, which means it did
            not come from `predicates(..., as_of=None)`. Raised rather than
            silently appending, because a caller that passed an `as_of` here
            would get rows filtered on *two* contradictory temporal conditions
            and an empty result that looks like "nothing was retired".

    `ORDER BY valid_to DESC` rather than by distance: `assertion_hnsw` is partial
    on `valid_to IS NULL AND visible`, so nothing retired is in it, and ranking
    these by similarity would mean a sequential scan computing a distance
    against every dead row in the namespace.
    """
    if LIVE_CLAUSE not in clauses:
        raise ValueError(
            f"expected {LIVE_CLAUSE!r} among the clauses; retired_statement replaces it "
            "with its complement and needs predicates(..., as_of=None)"
        )
    complemented = [RETIRED_CLAUSE if clause == LIVE_CLAUSE else clause for clause in clauses]
    return (
        f"SELECT {ASSERTION_COLUMNS} FROM assertion "  # noqa: S608
        f"WHERE {' AND '.join(complemented)} "
        f"ORDER BY valid_to DESC LIMIT {limit_param}"
    )
