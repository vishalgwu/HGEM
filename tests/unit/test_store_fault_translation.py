"""The last two error branches in `guardmem-core`.  S7.2

S7.2's sweep left exactly two uncovered statements in the package, and both are
the same shape as the ones `test_graph_selection.py` closed for Neo4j: a driver
fault becoming a domain error a caller upstream can act on.

**`NamespaceEntityResolver._ensure` writes the row every assertion hangs off.**
ADR-0008 derives an `EntityId` before any I/O and this is what makes it exist;
if the insert fails, the candidate has no subject to attach to. The failure has
to arrive as `StoreUnavailable` - retryable - because `ARCHITECTURE.md` §4's
answer to a store fault is that the outbox retries, and a bare
`asyncpg.PostgresError` reaching the relay would not carry that flag.

**Unit tests of the translation, not of asyncpg.** The integration suite covers
whether the insert is correct, against a real database. A real database cannot
be asked to fail one statement on demand, so the branch where it does is tested
against a pool that fails exactly one way - the boundary
`test_shared_primitives.py` draws for the connection pool, and
`test_graph_selection.py` for the graph driver.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import asyncpg
import pytest

from fixtures.assertions import ENTITY, TENANT, stored_assertion
from fixtures.decisions import conflict
from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.vector.base import ScoredAssertion
from guardmem_core.pipeline.l2_validate.incumbents import IncumbentSet
from guardmem_core.pipeline.per_candidate import _incumbent_of
from guardmem_core.types import CandidateId

if TYPE_CHECKING:
    from types import TracebackType

_TIMEOUT_S = 5.0


class _FailingConnection:
    """A pooled connection whose every statement raises.

    `tenant_transaction` runs `set_config` before handing the connection over,
    so the fault has to be raised by `execute` rather than by `acquire` - which
    is also the realistic shape: the pool is healthy and the *statement* fails.
    """

    def __init__(self, error: Exception, *, fail_after: int) -> None:
        self.error = error
        self.fail_after = fail_after
        self.calls = 0

    async def execute(self, *_: Any, **__: Any) -> str:
        self.calls += 1
        if self.calls > self.fail_after:
            raise self.error
        return "SET"

    def transaction(self) -> _Noop:
        return _Noop()


class _Noop:
    """An async context manager that does nothing, standing in for a transaction."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> bool:
        return False


class _FailingPool:
    """A pool that hands out one `_FailingConnection`."""

    def __init__(self, connection: _FailingConnection) -> None:
        self.connection = connection

    def acquire(self, **_: Any) -> _Acquire:
        return _Acquire(self.connection)


class _Acquire:
    def __init__(self, connection: _FailingConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FailingConnection:
        return self.connection

    async def __aexit__(
        self, _t: type[BaseException] | None, _e: BaseException | None, _tb: TracebackType | None
    ) -> bool:
        return False


def resolver(error: Exception) -> NamespaceEntityResolver:
    """A resolver whose entity insert fails with `error`.

    `fail_after=1` lets `set_config` succeed - `tenant_transaction` runs it to
    apply row-level security before the caller's statement - so the fault lands
    on the insert, which is the statement this test is about.
    """
    pool = _FailingPool(_FailingConnection(error, fail_after=1))
    return NamespaceEntityResolver(cast("asyncpg.Pool", pool), timeout_s=_TIMEOUT_S)


class TestTheEntityInsertTranslatesStoreFaults:
    async def test_a_postgres_error_becomes_a_retryable_store_error(self) -> None:
        """`ARCHITECTURE.md` §4 makes a store fault retryable and never fatal:
        the write waits and the outbox tries again. A bare `PostgresError`
        reaching the relay carries no such flag, so it would be handled as an
        unknown failure rather than as the transient one it is.
        """
        with pytest.raises(StoreUnavailable) as caught:
            await resolver(asyncpg.PostgresError("disk is full"))._ensure(
                ENTITY, TENANT, "Patient", "Joan Ellery"
            )

        assert caught.value.retryable is True

    async def test_the_message_names_the_entity_that_could_not_be_written(self) -> None:
        """Every assertion for this subject is blocked behind this row, so the
        id is what tells an operator which namespace stopped."""
        with pytest.raises(StoreUnavailable, match=str(ENTITY)):
            await resolver(asyncpg.PostgresError("disk is full"))._ensure(
                ENTITY, TENANT, "Patient", "Joan Ellery"
            )


class TestTheIncumbentIsTakenFromWhatRetrievalReturned:
    """`per_candidate._incumbent_of`, the last uncovered branch in the package.

    ADR-0010's applier needs the incumbent a decision was taken *against* - to
    supersede it, or to raise its `corroboration_count` on a merge. It is looked
    up in what retrieval already returned rather than re-read from the store,
    and that is a correctness decision rather than an optimisation: the decision
    was taken against *this* version of the row, and a second read could return
    one that has since been superseded, so the applier would merge into a fact
    the scorer never saw.
    """

    def test_a_conflicting_candidate_finds_the_row_it_conflicted_with(self) -> None:
        incumbent = stored_assertion(assertion_id="a-incumbent", obj="penicillin", visible=True)
        retrieved = IncumbentSet(
            candidate_id=CandidateId("c-1"),
            nearest=[ScoredAssertion(assertion=incumbent, cosine=0.91)],
            neighbours=[],
        )

        found = _incumbent_of(conflict(incumbent_assertion_id="a-incumbent"), retrieved)

        assert found is not None
        assert found.assertion_id == "a-incumbent"

    def test_a_novel_candidate_has_no_incumbent(self) -> None:
        """The common case, since most facts are novel - and the one that must
        not be confused with "retrieval lost it", below."""
        assert (
            _incumbent_of(
                conflict(), IncumbentSet(candidate_id=CandidateId("c-1"), nearest=[], neighbours=[])
            )
            is None
        )

    def test_an_incumbent_retrieval_no_longer_holds_is_none_rather_than_a_crash(self) -> None:
        """The report names an id and the set does not contain it.

        Reachable if retrieval and adjudication ever drift apart, and the honest
        answer is `None`: the applier then writes without superseding, which is
        the conservative outcome. Raising here would lose a decision that was
        correctly made.
        """
        other = stored_assertion(assertion_id="a-other", visible=True)
        retrieved = IncumbentSet(
            candidate_id=CandidateId("c-1"),
            nearest=[ScoredAssertion(assertion=other, cosine=0.5)],
            neighbours=[],
        )

        assert _incumbent_of(conflict(incumbent_assertion_id="a-missing"), retrieved) is None
