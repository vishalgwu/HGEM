"""The outbox relay: the only thing that may make a write visible.  S3.3

`ARCHITECTURE.md` §2.4 in one sentence - "a relay applies the vector/graph side
effects and marks the outbox row done" - and the step's WHY in another: a
partially-written assertion must never be retrievable. The vector half already
landed, invisibly, in the transaction that enqueued the event (`outbox.py`);
what is left is the graph half and the flip.

**Three transactions, and the boundaries are the design.**

1. *Claim.* One statement takes a bounded batch of undispatched rows and
   increments `attempts`, then commits. Committing here is what makes a crash
   countable: the attempt is recorded before the work starts, so a process that
   dies mid-flight leaves evidence rather than looking like it never ran.
2. *Dispatch.* Read the assertion, write the graph. No transaction spans this,
   deliberately - holding a Postgres transaction open across a call to another
   datastore ties up a pooled connection for the duration of somebody else's
   outage.
3. *Complete.* `visible = true` and `dispatched_at = now` in **one**
   transaction. They are the same fact stated in two tables and must not be
   separable: a flip without a completion is re-dispatched forever, and a
   completion without a flip is a fact that is durable, sourced, and invisible.

**At-least-once delivery, exactly-once effect.** Nothing here prevents the same
event being dispatched twice - a slow relay's claim commits and releases, and a
second pass may pick the row up again. That is not a hole being tolerated; it is
the only honest guarantee a queue with a crashing consumer can offer, and the
design pays for it where it is cheap. `GraphStore.upsert_assertion` is
idempotent by `assertion_id`, `REVEAL_ASSERTION` sets a boolean that is already
true, and `MARK_DISPATCHED` will not move a timestamp it has already written.
Run the relay twice over the same row and the second pass changes nothing, which
is what S3.3's "becomes visible exactly once" actually asks for.

**One pass, no loop, no sleep.** `run_once` is the whole public surface. The
schedule belongs to the worker that calls it - `services/worker` at the step
that builds it - and keeping it out of here is what lets `RULES.md` §5's "no
`sleep` (use fake clocks)" hold in the tests without any clock trickery at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.outbox import (
    CLAIM_OUTBOX,
    MARK_DISPATCHED,
    REVEAL_ASSERTION,
    pending_from_row,
)
from guardmem_core.memory.vector.pool import tenant_transaction, transaction
from guardmem_core.memory.vector.rowmap import (
    SELECT_ASSERTION,
    SELECT_PROVENANCE,
    assertion_from_row,
    provenance_from_row,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.outbox import PendingEvent
    from guardmem_core.memory.vector.pool import Conn
    from guardmem_core.schemas.entity import StoredAssertion

__all__ = ["OutboxRelay", "RelayRun"]

# How many events one pass takes. Bounded because `RULES.md` §2.2 wants bounded
# resources everywhere, and an unbounded claim on a backlog would hold a
# transaction over every pending row in the table at once.
_DEFAULT_BATCH_SIZE: Final = 50

# `RULES.md` §2.3's hard attempt cap. Five, because the failures this retries are
# transient by construction - `GraphStore` raises `StoreUnavailable` and nothing
# else is caught - and a fault that survives five passes is not transient.
_DEFAULT_MAX_ATTEMPTS: Final = 5


def _utc_now() -> datetime:
    """The default clock. Injected so tests can pin the completion timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RelayRun:
    """What one pass did.

    Attributes:
        claimed: Events this pass took off the queue. Each had its `attempts`
            incremented whether or not it went on to succeed.
        dispatched: How many completed - graph written, assertion visible,
            outbox row closed. `claimed - dispatched` is the number left pending
            for the next pass, which is the number worth alerting on when it
            stops falling.
    """

    claimed: int
    dispatched: int


class OutboxRelay:
    """Drains the outbox, one bounded pass at a time.

    Not bound to a tenant, unlike `PgVectorStore`, and the asymmetry is in the
    schema rather than in this class: `outbox` is the one operational table
    `0001_initial` leaves outside `TENANT_SCOPED`, because a background worker
    serves every tenant and an RLS policy would make it serve none. Each event
    carries the tenant it belongs to, and every statement that touches
    `assertion` runs inside `tenant_transaction` with that value applied.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        graph: GraphStore,
        *,
        timeout_s: float,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Bind a pool and a graph backend together.

        Args:
            pool: From `create_pool`. The same process-wide pool the stores use.
            graph: Where the graph half of the dual write goes. A `GraphStore`
                by structure, so `S3.4`'s NetworkX backend and `S7.1`'s Neo4j
                one swap in by configuration, exactly as `ARCHITECTURE.md` §2.4
                requires.
            timeout_s: Per-statement ceiling, from `settings.store_timeout_s`.
                Required and not defaulted, for the reason `PgVectorStore` gives.
            batch_size: Events per pass.
            max_attempts: After this many, an event stops being claimed. See
                `CLAIM_OUTBOX` for why it stays in the table rather than moving
                to a dead-letter queue it would be the only occupant of.
            clock: Reads the completion timestamp. Injected so a test can assert
                on `dispatched_at` without freezing real time.
        """
        self._pool = pool
        self._graph = graph
        self._timeout_s = timeout_s
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._clock = clock

    async def run_once(self) -> RelayRun:
        """Claim a batch and dispatch it. Safe to call again immediately.

        Returns:
            What the pass claimed and what it completed.

        Raises:
            StoreUnavailable: Postgres is unreachable. The graph being
                unreachable is *not* this - that is caught per event and left
                pending, because `ARCHITECTURE.md` §4's rule for a graph outage
                is "outbox retries", never "drop the assertion".

        Sequential rather than concurrent, and that is a decision with an expiry
        date on it. `RULES.md` §2.2 requires fan-out to go through a
        `TaskGroup` bounded by a semaphore; today the graph backend is in-process
        and a batch of fifty costs less than the round trip that claimed it, so
        concurrency would be machinery with nothing to buy. The step that makes
        the graph a network hop - `S7.1`, Neo4j - is the step that should add it.
        """
        events = await self._claim()
        dispatched = 0
        for event in events:
            if await self._dispatch(event):
                dispatched += 1
        return RelayRun(claimed=len(events), dispatched=dispatched)

    # --- internals ----------------------------------------------------------

    async def _claim(self) -> list[PendingEvent]:
        """Take a bounded batch off the queue, recording the attempt.

        Returns:
            The claimed events. Empty when nothing is pending or everything
            pending has exhausted `max_attempts`.

            **In no particular order.** `CLAIM_OUTBOX` orders by `created_at`
            inside the CTE, which selects *which* rows are claimed - the oldest
            pending ones - but the `RETURNING` of the `UPDATE` that follows
            carries no ordering guarantee, and Postgres does not promise to
            preserve the subquery's. This said "oldest first", which is true of
            the selection and not of the result, and the distinction matters
            only to whoever next wants dispatch order to mean something: it will
            need its own `ORDER BY`, not this docstring.

        Raises:
            StoreUnavailable: Postgres is unreachable.

        `transaction` rather than `tenant_transaction`: `outbox` carries no
        tenant column and no RLS policy, which is what lets one relay serve
        every tenant. The tenant arrives with each row and is applied from
        `_dispatch` onwards.
        """
        async with transaction(self._pool, timeout_s=self._timeout_s) as connection:
            rows = await connection.fetch(
                CLAIM_OUTBOX, self._max_attempts, self._batch_size, timeout=self._timeout_s
            )
        return [pending_from_row(row) for row in rows]

    async def _dispatch(self, event: PendingEvent) -> bool:
        """Write the graph side of one event and complete it.

        Args:
            event: A claimed, undispatched write.

        Returns:
            True when the event is finished and its assertion is visible. False
            when it has been left pending for a later pass - which is not an
            error, and is why this returns a bool rather than raising.

        Raises:
            StoreUnavailable: Postgres is unreachable. Note the asymmetry with
                the *graph* being unreachable, which is caught here: a dead
                Postgres means this pass cannot make progress on anything, while
                a dead graph means this event waits and the pass continues.
        """
        async with tenant_transaction(
            self._pool, event.tenant_id, timeout_s=self._timeout_s
        ) as connection:
            assertion = await self._load(connection, event)
        if assertion is None:
            return False
        try:
            await self._graph.upsert_assertion(assertion)
        except StoreUnavailable:
            return False
        await self._complete(event)
        return True

    async def _load(self, connection: Conn, event: PendingEvent) -> StoredAssertion | None:
        """Read the assertion an event refers to, with its provenance.

        Args:
            connection: Inside a transaction with this event's tenant applied.
            event: The claimed write.

        Returns:
            The assertion, or `None` if this tenant cannot see it.

        A foreign key makes the row's *existence* certain, so `None` here means
        one thing only: RLS matched nothing, because the tenant on the event is
        not the tenant on the assertion. That is a corrupted payload rather than
        a missing fact, and the right answer is to leave the event pending and
        let `attempts` climb to the cap, where an operator will find it - not to
        raise and take the rest of the batch down with it.

        Deliberately does not filter on `visible`: this is the code that makes
        rows visible, and a read path's filter here would mean it could only ever
        process events it had already finished.
        """
        row = await connection.fetchrow(
            SELECT_ASSERTION, event.assertion_id, timeout=self._timeout_s
        )
        if row is None:
            return None
        citations = await connection.fetch(
            SELECT_PROVENANCE, [event.assertion_id], timeout=self._timeout_s
        )
        return assertion_from_row(row, [provenance_from_row(citation) for citation in citations])

    async def _complete(self, event: PendingEvent) -> None:
        """Make the assertion visible and close the outbox row, together.

        Args:
            event: The write whose graph side has just landed.

        Raises:
            StoreUnavailable: Postgres is unreachable.

        One transaction, because these two statements are one decision. Split
        them and every crash between them produces a state the system has no
        word for: a visible assertion with a pending event, re-dispatched on
        every pass forever, or a closed event over a fact nobody can read.
        """
        at = self._clock()
        async with tenant_transaction(
            self._pool, event.tenant_id, timeout_s=self._timeout_s
        ) as connection:
            await connection.execute(REVEAL_ASSERTION, event.assertion_id, timeout=self._timeout_s)
            await connection.execute(MARK_DISPATCHED, at, event.outbox_id, timeout=self._timeout_s)
