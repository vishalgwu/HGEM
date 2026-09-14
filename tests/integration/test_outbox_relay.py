"""`OutboxRelay` against a real Postgres.  BUILD_NOTEBOOK.md S3.3

S3.3's DONE WHEN is one sentence - "test kills the relay mid-flight; the
assertion is invisible to search; after the relay restarts, it becomes visible
exactly once" - and `TestTheRelayKilledMidFlight` is exactly that, held against
the schema rather than against a double.

**Where the relay is killed is the whole test.** A relay that dies *before* the
graph write is uninteresting: nothing happened, and a retry repeats nothing. The
dangerous window is between the graph side landing and the flip, because that is
the only point at which a retry re-applies an effect that already took. So
`GraphThatDiesAfterWriting` writes its edge and then fails, and the restart has
to leave one edge and one visible assertion rather than two of either.

The enqueue this drains is `test_outbox_enqueue.py`'s subject; the faults are
`fixtures/graph_faults.py`'s. What is left here is the drain, and every case
needs the database for a reason a fake cannot supply: the claim is `FOR UPDATE
SKIP LOCKED` over a partial index, the completion is two statements that have to
share one transaction, and the tenant-mismatch case turns entirely on an RLS
policy.
"""

from __future__ import annotations

import asyncpg
import pytest

from fixtures.fakes import FakeGraphStore
from fixtures.graph_faults import (
    DelegatingGraph,
    GraphOutage,
    GraphThatCrashes,
    GraphThatDiesAfterWriting,
    SelectiveOutage,
)
from fixtures.outbox import (
    RELAYED_AT,
    attempts,
    dispatched_at,
    is_visible,
    relay_over,
    second_tenancy,
)
from fixtures.pgvector import TIMEOUT_S, assertion, search
from guardmem_core.memory.relay import OutboxRelay
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.types import TenantId


class TestADispatchedWrite:
    async def test_the_assertion_becomes_visible_and_searchable(
        self,
        store: PgVectorStore,
        relay: OutboxRelay,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """The loop S3.2 left open, closed: written invisible, relayed, found."""
        written = assertion(tenancy)
        await store.upsert([written])
        assert await search(store, embedder, "allergy: penicillin") == []

        run = await relay.run_once()

        assert (run.claimed, run.dispatched) == (1, 1)
        assert await search(store, embedder, "allergy: penicillin") == [written.assertion_id]

    async def test_the_graph_gets_one_edge_carrying_the_assertion(
        self,
        store: PgVectorStore,
        relay: OutboxRelay,
        graph: FakeGraphStore,
        tenancy: dict[str, str],
    ) -> None:
        """The half of the dual write this step exists to apply."""
        written = assertion(tenancy)
        await store.upsert([written])

        await relay.run_once()

        edge = graph.edges[written.assertion_id]
        assert edge.subject_id == tenancy["entity"]
        assert edge.predicate == "allergy"
        assert edge.object == "penicillin"

    async def test_the_outbox_row_records_when_the_graph_side_landed(
        self,
        store: PgVectorStore,
        relay: OutboxRelay,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """System time, from the injected clock - `RULES.md` §5 forbids sleeping
        for one and this is the axis `dispatched_at` is on."""
        written = assertion(tenancy)
        await store.upsert([written])

        await relay.run_once()

        assert await dispatched_at(owner, written.assertion_id) == RELAYED_AT

    async def test_a_second_pass_claims_nothing(
        self,
        store: PgVectorStore,
        relay: OutboxRelay,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """`dispatched_at IS NULL` in the claim is what makes the queue drain
        rather than churn."""
        written = assertion(tenancy)
        await store.upsert([written])
        await relay.run_once()

        second = await relay.run_once()

        assert (second.claimed, second.dispatched) == (0, 0)
        assert await attempts(owner, written.assertion_id) == 1


class TestTheRelayKilledMidFlight:
    """S3.3's DONE WHEN, in full."""

    async def test_a_kill_after_the_graph_write_leaves_the_assertion_invisible(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """The graph side landed and the flip did not. Nothing is retrievable.

        This is the state the whole pattern exists to make safe: a dual write
        that is genuinely half-applied. `visible` is what keeps it out of
        retrieval, and `dispatched_at IS NULL` is what guarantees somebody comes
        back for it.
        """
        written = assertion(tenancy)
        await store.upsert([written])
        dying = GraphThatDiesAfterWriting()

        run = await relay_over(pool, dying).run_once()

        assert (run.claimed, run.dispatched) == (1, 0)
        assert list(dying.edges) == [written.assertion_id]
        assert await is_visible(owner, written.assertion_id) is False
        assert await search(store, embedder, "allergy: penicillin") == []
        assert await dispatched_at(owner, written.assertion_id) is None
        assert await attempts(owner, written.assertion_id) == 1

    async def test_a_restart_makes_it_visible_exactly_once(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """Restart, replay, and count. One edge, one flip, one dispatch.

        "Exactly once" is a statement about *effects*, not deliveries - the
        second relay genuinely re-writes the edge the first one already wrote.
        It is true here because `upsert_assertion` is idempotent by
        `assertion_id` and `MARK_DISPATCHED` will not move a timestamp twice,
        and the third pass claiming nothing is what proves the queue is drained
        rather than merely quiet.
        """
        written = assertion(tenancy)
        await store.upsert([written])
        await relay_over(pool, GraphThatDiesAfterWriting()).run_once()
        restarted = DelegatingGraph()

        second = await relay_over(pool, restarted).run_once()
        third = await relay_over(pool, restarted).run_once()

        assert (second.claimed, second.dispatched) == (1, 1)
        assert (third.claimed, third.dispatched) == (0, 0)
        assert list(restarted.edges) == [written.assertion_id]
        assert await is_visible(owner, written.assertion_id) is True
        assert await search(store, embedder, "allergy: penicillin") == [written.assertion_id]
        assert await dispatched_at(owner, written.assertion_id) == RELAYED_AT


class TestAGraphOutage:
    async def test_the_event_stays_pending_and_the_attempt_is_counted(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """`ARCHITECTURE.md` §4's rule for a graph outage is "outbox retries",
        and its Never column is "drop the assertion"."""
        written = assertion(tenancy)
        await store.upsert([written])

        run = await relay_over(pool, GraphOutage()).run_once()

        assert (run.claimed, run.dispatched) == (1, 0)
        assert await dispatched_at(owner, written.assertion_id) is None
        assert await attempts(owner, written.assertion_id) == 1

    async def test_one_poison_event_does_not_block_the_batch_behind_it(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """Per event, not per pass. A batch of fifty in which the third fails
        must still dispatch the other forty-nine, or one bad row stalls every
        write queued behind it."""
        poison = assertion(tenancy, predicate=SelectiveOutage.poison)
        healthy = assertion(tenancy, predicate="employer", obj="Northwind Health")
        await store.upsert([poison, healthy])

        run = await relay_over(pool, SelectiveOutage()).run_once()

        assert (run.claimed, run.dispatched) == (2, 1)
        assert await is_visible(owner, healthy.assertion_id) is True
        assert await is_visible(owner, poison.assertion_id) is False

    async def test_an_event_that_exhausts_its_attempts_stops_being_claimed(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """`RULES.md` §2.3's hard attempt cap.

        The row stays pending rather than moving anywhere: its assertion is
        invisible and therefore harmless, and leaving it in the table is what
        lets an operator find it. Retrying it forever would starve the queue.
        """
        written = assertion(tenancy)
        await store.upsert([written])
        stuck = relay_over(pool, GraphOutage(), max_attempts=1)
        await stuck.run_once()

        again = await stuck.run_once()

        assert (again.claimed, again.dispatched) == (0, 0)
        assert await attempts(owner, written.assertion_id) == 1
        assert await is_visible(owner, written.assertion_id) is False


class TestAnUnexpectedError:
    async def test_it_propagates_rather_than_being_filed_as_a_retry(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """Only `StoreUnavailable` is caught, and that is deliberate.

        `RULES.md` §2.3 permits a retry only where `retryable` is true. A bug in
        the graph backend retried five times is a bug reported five passes late
        and attributed to a queue; raising puts it in front of whoever deployed
        it.
        """
        await store.upsert([assertion(tenancy)])

        with pytest.raises(RuntimeError, match="not in the ontology"):
            await relay_over(pool, GraphThatCrashes()).run_once()


class TestTenancy:
    async def test_one_relay_serves_every_tenant_in_one_pass(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """Why `outbox` is the one operational table outside `TENANT_SCOPED`.

        A background worker serves every tenant, and an RLS policy on this table
        would mean it served whichever one happened to be set - which is to say
        none of them, since a worker has no request to read a tenant from.
        """
        second = await second_tenancy(owner, tenancy)
        mine = assertion(tenancy)
        theirs = assertion(second, obj="latex")
        other_store = PgVectorStore(
            pool, embedder, tenant_id=TenantId(second["tenant"]), timeout_s=TIMEOUT_S
        )
        await store.upsert([mine])
        await other_store.upsert([theirs])

        run = await relay_over(pool, DelegatingGraph()).run_once()

        assert (run.claimed, run.dispatched) == (2, 2)
        assert await is_visible(owner, mine.assertion_id) is True
        assert await is_visible(owner, theirs.assertion_id) is True

    async def test_an_event_naming_the_wrong_tenant_is_left_pending(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """A corrupted payload must stall one event, not the relay.

        With the wrong tenant set, RLS makes the assertion invisible to the
        `SELECT` that loads it - so the relay sees no row where a foreign key
        guarantees one exists. It leaves the event pending and moves on, which
        lets `attempts` climb to the cap and put the row where an operator looks.
        """
        written = assertion(tenancy)
        await store.upsert([written])
        await owner.execute(
            "UPDATE outbox SET event = jsonb_set(event, '{tenant_id}', to_jsonb($1::text))"
            " WHERE assertion_id = $2::uuid",
            tenancy["other"],
            written.assertion_id,
        )
        graph = DelegatingGraph()

        run = await relay_over(pool, graph).run_once()

        assert (run.claimed, run.dispatched) == (1, 0)
        assert graph.edges == {}
        assert await is_visible(owner, written.assertion_id) is False
        assert await dispatched_at(owner, written.assertion_id) is None
