"""The guards on the SQL builders.  S3.2, S7.2

`memory/vector/queries.py` builds the statements every read shares. The builders
themselves are exercised through the store, in the integration suite, against a
real database - which is the right place for "does this SQL answer correctly".
What that cannot reach is the branch where a builder **refuses**, because the
store never calls it wrongly.

S7.2 asks for exactly those. A guard with no test is a guard that has never been
observed to fire, and this one is load-bearing: `retired_statement` replaces the
live clause with its complement, so a caller that passed an `as_of` would get
rows filtered on two contradictory temporal conditions and an empty result that
reads as "nothing was retired" rather than as a mistake.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from guardmem_core.memory.vector.queries import (
    LIVE_CLAUSE,
    RETIRED_CLAUSE,
    predicates,
    retired_statement,
)
from guardmem_core.types import Namespace

NS = Namespace("patient:7781")
WHEN = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)


class TestRetiredStatementRefusesTheWrongClauses:
    def test_a_point_in_time_clause_set_is_refused(self) -> None:
        """The failure this guard exists for, and the reason it raises rather
        than appending.

        `predicates(..., as_of=...)` selects rows whose world time contains an
        instant. `retired_statement` wants rows whose validity has *ended*.
        Combining them is not a narrower query, it is an unsatisfiable one - and
        an empty result here is indistinguishable from "this namespace has
        retired nothing", which is a real and common answer.
        """
        clauses, _ = predicates(NS, {}, as_of=WHEN)

        with pytest.raises(ValueError, match=r"expected .* among the clauses"):
            retired_statement(clauses, "$2")

    def test_the_message_names_the_call_that_would_have_been_right(self) -> None:
        """A caller who got here passed the wrong `as_of`, and the fix is one
        argument away - so the message says which."""
        clauses, _ = predicates(NS, {}, as_of=WHEN)

        with pytest.raises(ValueError, match=r"predicates\(\.\.\., as_of=None\)"):
            retired_statement(clauses, "$2")

    def test_the_live_clause_set_is_accepted_and_complemented(self) -> None:
        """The control. The guard must not refuse the only call that is correct,
        and the complement is what makes the statement mean "retired"."""
        clauses, _ = predicates(NS, {}, as_of=None)

        statement = retired_statement(clauses, "$2")

        assert RETIRED_CLAUSE in statement
        assert LIVE_CLAUSE not in statement

    def test_it_orders_by_when_the_belief_ended(self) -> None:
        """`ORDER BY valid_to DESC` rather than by distance: `assertion_hnsw` is
        partial on `valid_to IS NULL AND visible`, so nothing retired is in the
        index and ranking these by similarity would mean a sequential scan
        computing a distance against every dead row in the namespace."""
        clauses, _ = predicates(NS, {}, as_of=None)

        assert "ORDER BY valid_to DESC" in retired_statement(clauses, "$2")
