"""The write half of the dual write: what `upsert` leaves behind.  S3.3

Split from `test_outbox_relay.py` along the seam the code has. These are
assertions about `PgVectorStore.upsert` and `memory/outbox.py` - one
transaction, two tables, a derived id - and none of them run a relay. The drain
half is the other module's subject.

The seam is worth keeping because the two halves fail differently. A broken
enqueue is a fact that is durable and can never be released; a broken relay is a
queue that stops draining. The first is silent and permanent, which is why its
tests read the row directly as the owner rather than inferring it from what the
relay managed to do with it.
"""

from __future__ import annotations

import asyncpg
import pytest

from fixtures.pgvector import assertion
from guardmem_core.memory.vector.pgvector_store import PgVectorStore


class TestTheEnqueue:
    async def test_a_write_leaves_exactly_one_pending_event(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """`ARCHITECTURE.md` §2.4: the assertion row and its event commit together."""
        written = assertion(tenancy)

        await store.upsert([written])

        row = await owner.fetchrow(
            "SELECT attempts, dispatched_at FROM outbox WHERE assertion_id = $1::uuid",
            written.assertion_id,
        )
        assert row is not None
        assert row["attempts"] == 0
        assert row["dispatched_at"] is None

    async def test_the_event_and_the_assertion_land_in_the_same_transaction(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """Not "both exist" but "neither can exist alone".

        Checked through the constraint that actually enforces it: writing an
        assertion with no provenance fails the deferred trigger at COMMIT, and
        if the enqueue were a separate transaction its row would survive that
        rollback. It does not, so they are genuinely one unit of work.
        """
        unsourced = assertion(tenancy).model_copy(update={"provenance": []})

        with pytest.raises(asyncpg.PostgresError):
            await store.upsert([unsourced])

        assert (
            await owner.fetchval(
                "SELECT count(*) FROM outbox WHERE assertion_id = $1::uuid",
                unsourced.assertion_id,
            )
            == 0
        )

    async def test_a_replayed_write_does_not_enqueue_a_second_event(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """Why the outbox id is derived from the assertion id rather than random.

        A `uuid4` would deduplicate nothing: the assertion would collide on its
        primary key and do nothing while its event was enqueued again - so a
        relay restart would dispatch the same write twice, which is the one
        thing S3.3's "exactly once" rules out. The same argument fixed the
        provenance ids one step earlier.
        """
        written = assertion(tenancy)
        await store.upsert([written])

        await store.upsert([written])

        assert (
            await owner.fetchval(
                "SELECT count(*) FROM outbox WHERE assertion_id = $1::uuid",
                written.assertion_id,
            )
            == 1
        )

    async def test_the_event_carries_the_tenant_the_relay_cannot_work_without(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """`outbox` has no tenant column and no RLS policy, so the payload is the
        only thing that can tell the relay which `app.tenant_id` to set before
        touching a table that has both."""
        written = assertion(tenancy)

        await store.upsert([written])

        event = await owner.fetchval(
            "SELECT event FROM outbox WHERE assertion_id = $1::uuid", written.assertion_id
        )
        assert f'"tenant_id": "{tenancy["tenant"]}"' in event
        assert f'"trace_id": "{written.trace_id}"' in event
        assert f'"namespace": "{written.namespace}"' in event
