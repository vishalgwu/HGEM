"""`PgVectorStore` against a real pgvector Postgres.  BUILD_NOTEBOOK.md S3.2

S3.2's DONE WHEN is one sentence - "write, search, supersede; the superseded row
is absent from search results and present in a point-in-time query with
`as_of`" - and `test_a_superseded_row_leaves_search_and_stays_recoverable` is
exactly that, held against the schema rather than against the fake.

Everything else here exists because the fake cannot check it. `FakeVectorStore`
enforces the same three behavioural guarantees, but it enforces them in Python:
it cannot tell you that the deferred trigger accepts the write, that RLS scopes
the SELECT, that `ON CONFLICT DO NOTHING` beats a replay, or that a span
survives `INT4RANGE`. Those are properties of the database, and this is the only
suite that can observe them.

**The store connects as `guardmem_app`, never as the owner.** `RULES.md`
non-negotiable #2 is a statement about what that role cannot do, and a test that
ran as the owner - who is exempt from its own grants, and would have been exempt
from RLS too without `FORCE ROW LEVEL SECURITY` - would be evidence of nothing.
The owner connection appears only to seed rows `guardmem_app` may not create, to
read back what the store wrote, and to stand in for the S3.3 outbox relay when a
row has to become visible.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from fixtures.pgvector import (
    LATER,
    NS,
    TIMEOUT_S,
    assertion,
    search,
    write_and_reveal,
)
from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.rowmap import EMBEDDING_DIM
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.types import AssertionId, TenantId


class _TruncatingEmbedder:
    """An `Embedder` that returns one vector however many texts it is given.

    A real provider failure mode: a batch endpoint that drops an input on a
    partial error and returns a shorter list with a 200.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return exactly one vector, whatever was asked for."""
        return [[0.0] * EMBEDDING_DIM]


class TestWritesAreInvisibleUntilTheRelaySaysSo:
    async def test_upsert_writes_the_row_with_visible_false_and_an_embedding(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """`ARCHITECTURE.md` §2.4: a partial dual write must be unretrievable."""
        written = assertion(tenancy)

        await store.upsert([written])

        row = await owner.fetchrow(
            "SELECT visible, embedding IS NOT NULL AS embedded FROM assertion WHERE id = $1::uuid",
            written.assertion_id,
        )
        assert row is not None
        assert row["visible"] is False
        assert row["embedded"] is True
        # The *assertion* is what gets embedded - not its id, not its verbatim.
        assert embedder.texts == ["allergy: penicillin"]

    async def test_an_invisible_row_is_absent_from_search(
        self, store: PgVectorStore, embedder: HashEmbedder, tenancy: dict[str, str]
    ) -> None:
        """Invariant I6, at the layer an application bug could otherwise skip."""
        await store.upsert([assertion(tenancy)])

        assert await search(store, embedder, "allergy: penicillin") == []

    async def test_a_revealed_row_is_found_with_its_provenance_intact(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """The round trip, including the parts `INT4RANGE` and `REAL` could lose."""
        written = assertion(tenancy)
        await write_and_reveal(store, owner, written)

        vector = (await embedder.embed(["allergy: penicillin"]))[0]
        found = await store.search(namespace=NS, embedding=vector, k=10, filters={})

        assert [a.assertion_id for a in found] == [written.assertion_id]
        citation = found[0].provenance[0]
        assert citation.source_span == (13, 35)
        assert citation.source_tier is SourceTier.VERIFIED_USER
        assert citation.alignment == pytest.approx(0.97)
        assert found[0].object == "penicillin"
        assert found[0].tenant_id == tenancy["tenant"]


class TestSearch:
    async def test_results_are_ordered_by_distance(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """`ORDER BY embedding <=> $n`, not insertion order - which is the one
        thing `FakeVectorStore` deliberately gets wrong."""
        first = assertion(tenancy, predicate="allergy", obj="penicillin")
        second = assertion(tenancy, predicate="employer", obj="Northwind Health")
        await write_and_reveal(store, owner, first, second)

        # Written first, queried second: insertion order would invert this.
        assert await search(store, embedder, "employer: Northwind Health") == [
            second.assertion_id,
            first.assertion_id,
        ]

    async def test_k_bounds_the_result_set(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """`MEMORY_ENGINE.md` §2.2 asks for the top 10 incumbents, not all of them."""
        written = [assertion(tenancy, obj=f"allergen-{index}") for index in range(5)]
        await write_and_reveal(store, owner, *written)

        assert len(await search(store, embedder, "allergy: allergen-0", k=2)) == 2

    async def test_a_filter_narrows_to_one_predicate(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """The incumbent lookup: same subject, one predicate."""
        allergy = assertion(tenancy, predicate="allergy")
        employer = assertion(tenancy, predicate="employer", obj="Northwind Health")
        await write_and_reveal(store, owner, allergy, employer)

        found = await search(
            store,
            embedder,
            "allergy: penicillin",
            filters={"predicate": "allergy", "subject_id": tenancy["entity"]},
        )

        assert found == [allergy.assertion_id]

    async def test_an_unknown_filter_key_is_refused(self, store: PgVectorStore) -> None:
        """`RULES.md` §4 forbids string-built SQL, and a caller-supplied dict key
        reaching a query as a column name is how that happens by accident."""
        with pytest.raises(KeyError, match="not a searchable column"):
            await store.search(
                namespace=NS,
                embedding=[0.0] * EMBEDDING_DIM,
                k=1,
                filters={"visible": True},
            )

    async def test_another_tenants_rows_are_invisible(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """RLS, reached through the store rather than through raw SQL.

        The second store differs from the first in one field, which is the whole
        argument for binding the tenant at construction: there is no call here
        that could have forgotten to pass it.
        """
        await write_and_reveal(store, owner, assertion(tenancy))
        intruder = PgVectorStore(
            pool, embedder, tenant_id=TenantId(tenancy["other"]), timeout_s=TIMEOUT_S
        )

        assert await search(intruder, embedder, "allergy: penicillin") == []


class TestSupersession:
    async def test_a_superseded_row_leaves_search_and_stays_recoverable(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        embedder: HashEmbedder,
        tenancy: dict[str, str],
    ) -> None:
        """S3.2's DONE WHEN, in full.

        Write, search, supersede; the superseded row is absent from search and
        present in a point-in-time query. The last assertion is the half that is
        easy to lose: at exactly `valid_to` the fact is already retired, because
        `MEMORY_ENGINE.md` §2.3 abuts the intervals and `[valid_from, valid_to)`
        is half-open.
        """
        incumbent = assertion(tenancy)
        successor = assertion(tenancy, obj="latex", valid_from=LATER)
        await write_and_reveal(store, owner, incumbent, successor)

        await store.supersede(incumbent.assertion_id, successor.assertion_id, LATER)

        live = await search(store, embedder, "allergy: penicillin")
        assert incumbent.assertion_id not in live
        recovered = await search(
            store, embedder, "allergy: penicillin", as_of=LATER - timedelta(days=1)
        )
        assert incumbent.assertion_id in recovered
        assert incumbent.assertion_id not in await search(
            store, embedder, "allergy: penicillin", as_of=LATER
        )

    async def test_supersession_is_an_update_and_never_a_delete(
        self,
        store: PgVectorStore,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """`RULES.md` non-negotiable #2. The row is still there, with a tombstone."""
        incumbent = assertion(tenancy)
        successor = assertion(tenancy, obj="latex", valid_from=LATER)
        await write_and_reveal(store, owner, incumbent, successor)

        await store.supersede(incumbent.assertion_id, successor.assertion_id, LATER)

        row = await owner.fetchrow(
            "SELECT valid_to, superseded_by FROM assertion WHERE id = $1::uuid",
            incumbent.assertion_id,
        )
        assert row is not None
        assert row["valid_to"] == LATER
        assert str(row["superseded_by"]) == successor.assertion_id

    async def test_superseding_twice_raises_rather_than_passing_quietly(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """A zero-row UPDATE is the only place a transposed call can surface."""
        incumbent = assertion(tenancy)
        successor = assertion(tenancy, obj="latex", valid_from=LATER)
        await write_and_reveal(store, owner, incumbent, successor)
        await store.supersede(incumbent.assertion_id, successor.assertion_id, LATER)

        with pytest.raises(ConcurrencyConflict, match="not live"):
            await store.supersede(incumbent.assertion_id, successor.assertion_id, LATER)

    async def test_superseding_another_tenants_assertion_conflicts(
        self,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        embedder: HashEmbedder,
        owner: asyncpg.Connection,
        tenancy: dict[str, str],
    ) -> None:
        """RLS makes a cross-tenant UPDATE match nothing, and nothing raises.

        Worth its own test because the failure mode without it is silence: the
        UPDATE would report success having changed no rows, and a caller would
        believe it had retired a fact it cannot even see.
        """
        incumbent = assertion(tenancy)
        await write_and_reveal(store, owner, incumbent)
        intruder = PgVectorStore(
            pool, embedder, tenant_id=TenantId(tenancy["other"]), timeout_s=TIMEOUT_S
        )

        with pytest.raises(ConcurrencyConflict):
            await intruder.supersede(incumbent.assertion_id, AssertionId(str(uuid4())), LATER)


class TestReplay:
    async def test_upsert_is_idempotent_in_rows_and_in_citations(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """S3.3 replays this call after a relay restart.

        The provenance half is the part a `uuid4` id would break silently: the
        assertion would deduplicate on its primary key while its citations
        multiplied, and `corroboration_count` would start disagreeing with the
        evidence it summarises.
        """
        written = assertion(tenancy)
        await store.upsert([written])

        await store.upsert([written])

        assert (
            await owner.fetchval(
                "SELECT count(*) FROM provenance WHERE assertion_id = $1::uuid",
                written.assertion_id,
            )
            == 1
        )

    async def test_a_replayed_write_does_not_resurrect_a_superseded_row(
        self, store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """Why `ON CONFLICT DO NOTHING` and not `DO UPDATE`.

        An overwrite would clear `valid_to` and drop `superseded_by`, returning
        a fact the system had already retired - through the front door of a
        retry. Doing nothing is the only replay semantics that cannot undo a
        decision.
        """
        incumbent = assertion(tenancy)
        successor = assertion(tenancy, obj="latex", valid_from=LATER)
        await write_and_reveal(store, owner, incumbent, successor)
        await store.supersede(incumbent.assertion_id, successor.assertion_id, LATER)

        await store.upsert([incumbent])

        row = await owner.fetchrow(
            "SELECT valid_to, superseded_by FROM assertion WHERE id = $1::uuid",
            incumbent.assertion_id,
        )
        assert row is not None
        assert row["valid_to"] == LATER
        assert row["superseded_by"] is not None


class TestTheEmbeddingContract:
    async def test_a_wrong_dimension_vector_is_refused_before_it_reaches_postgres(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`assertion.embedding` is `VECTOR(1024)`.

        Caught in the store because the driver's error names a column, while the
        actual fault is `GM_EMBED_MODEL` pointing at a model of another size -
        which is a configuration problem and reads nothing like a column
        mismatch at three in the morning.
        """
        store = PgVectorStore(
            pool,
            HashEmbedder(dim=768),
            tenant_id=TenantId(tenancy["tenant"]),
            timeout_s=TIMEOUT_S,
        )

        with pytest.raises(ValueError, match="768-dimension"):
            await store.upsert([assertion(tenancy)])

    async def test_a_short_batch_of_vectors_is_refused(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """A provider that silently drops one input must not silently drop a fact.

        `zip(..., strict=True)` would catch this too, but with a `ValueError`
        about zip, which tells the person reading the page nothing about which
        assertion went missing or why.
        """
        store = PgVectorStore(
            pool, _TruncatingEmbedder(), tenant_id=TenantId(tenancy["tenant"]), timeout_s=TIMEOUT_S
        )

        with pytest.raises(ValueError, match="1 vectors for 2 assertions"):
            await store.upsert([assertion(tenancy), assertion(tenancy, obj="latex")])

    async def test_an_empty_batch_touches_nothing(
        self, store: PgVectorStore, embedder: HashEmbedder
    ) -> None:
        """No embedder call, no transaction, no round trip."""
        await store.upsert([])

        assert embedder.texts == []
