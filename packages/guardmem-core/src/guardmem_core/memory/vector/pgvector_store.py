"""The pgvector-backed `VectorStore`.  BUILD_NOTEBOOK.md S3.2, S3.3

The default backend named by `ARCHITECTURE.md` §2.4 and `PRD.md` FR-4.1:
Postgres with `pgvector`, up to roughly 10M vectors, after which the same
protocol gets a Qdrant implementation and nothing above it changes.

Three properties are worth reading before the code, because each is an invariant
that a plausible-looking query would quietly break.

**Writes are invisible, and they enqueue their own release.** `upsert` inserts
`visible = false` as a literal and has no path that sets it true; in the same
transaction it enqueues the outbox event that `memory/relay.py` will act on once
the graph side has landed (`ARCHITECTURE.md` §2.4). The pair is what makes a
half-finished dual write unretrievable rather than briefly wrong - and what
stops an invisible row being stranded with nothing left to release it. Every
read here filters on it.

**Nothing is deleted.** `RULES.md` non-negotiable #2 revokes `DELETE` on
`assertion` and `provenance` from `guardmem_app` at the role level, so this
module could not delete a fact if it tried - but it must not *want* to either,
which is why re-writing a known id is `ON CONFLICT DO NOTHING` rather than an
overwrite. `upsert` says why that distinction has teeth.

**The tenant is structural, not a filter.** A store is bound to one `TenantId`
at construction, and every statement runs inside a transaction that has
`SET LOCAL app.tenant_id` applied - the value the RLS policies from
`0001_initial` read. `RULES.md` §4 asks for defence in depth; this is the layer
that makes the database's half work at all, because RLS with that setting unset
returns zero rows rather than every row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import asyncpg

from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.memory.outbox import INSERT_OUTBOX, outbox_params
from guardmem_core.memory.vector.base import ScoredAssertion, embed_text
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.memory.vector.queries import (
    SUPERSEDE,
    nearest_statement,
    predicates,
    retired_statement,
)
from guardmem_core.memory.vector.rowmap import (
    EMBEDDING_DIM,
    INSERT_ASSERTION,
    INSERT_PROVENANCE,
    SELECT_PROVENANCE,
    assertion_from_row,
    assertion_params,
    provenance_from_row,
    provenance_params,
)
from guardmem_core.types import AssertionId, Namespace, TenantId

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from guardmem_core.memory.vector.base import Embedder
    from guardmem_core.memory.vector.pool import Conn
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.schemas.receipt import Provenance

__all__ = ["PgVectorStore"]


class PgVectorStore:
    """A `VectorStore` over one tenant's rows in Postgres.

    Structurally a `VectorStore` and not a subclass of one: S1.7 made the
    contract a `typing.Protocol` precisely so an implementation satisfies it by
    shape and imports nothing to do so.

    One store per tenant, not a `tenant_id` argument on each method: that leaves
    the protocol's signatures untouched, and it makes "which tenant is this?"
    answerable at construction, where the answer comes from an authenticated
    request, rather than at every call, where it comes from whatever the caller
    happened to be holding.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Embedder,
        *,
        tenant_id: TenantId,
        timeout_s: float,
    ) -> None:
        """Bind a pool, an embedder and a tenant together.

        Args:
            pool: From `create_pool`. Injected rather than created here, so the
                process opens one pool and the request scope opens many stores.
            embedder: Write-side embedding. See `memory/vector/base.py` for why
                `upsert` embeds and `search` does not.
            tenant_id: The tenant this instance speaks for.
            timeout_s: Per-statement ceiling, from `settings.store_timeout_s`.
                Required and not defaulted: `RULES.md` §2.2 says every outbound
                call has an explicit timeout, and a default is how a call ends
                up with one nobody chose.
        """
        self._pool = pool
        self._embedder = embedder
        self._tenant_id = tenant_id
        self._timeout_s = timeout_s

    async def upsert(self, assertions: Sequence[StoredAssertion]) -> None:
        """Write assertions, their citations and their outbox events, atomically.

        Args:
            assertions: What to persist. `visible` is a literal `false` in the
                statement rather than a bound parameter, so there is no value a
                caller could pass that would make a row retrievable - that
                decision belongs to the outbox relay and to nothing else.

        Raises:
            StoreUnavailable: Postgres is unreachable.
            ValueError: an embedding came back with the wrong dimension.

        One transaction is not an optimisation, and it carries two separate
        invariants. `assertion_requires_provenance` is a DEFERRABLE INITIALLY
        DEFERRED constraint trigger that fires at COMMIT, so an assertion and
        its provenance written in separate transactions are rejected -
        correctly, as an unsourced write. And `ARCHITECTURE.md` §2.4 requires
        the assertion row and its outbox event to commit together: an assertion
        that landed without its event would be invisible with nothing left in
        the system that could ever flip it, which is not a partial write but a
        permanently unreadable one.

        **The outbox row is written here rather than by the router**, and that
        is the one place this class's Postgres-specificity is load-bearing
        rather than incidental. Atomicity between two tables is a property of a
        Postgres transaction, and this method owns the only one in the write
        path. A `VectorStore` that cannot make that guarantee - Qdrant, above
        `PRD.md` FR-4.1's threshold - needs its own coordination story, which is
        exactly what §2.4 means by the backend being an operator decision.

        Idempotent by `ON CONFLICT DO NOTHING`, which is a stronger choice than
        it looks and is taken over `DO UPDATE` deliberately. S3.3 replays this
        call after a relay restart, and by then the row may legitimately have
        been superseded by a later proposal. An overwrite would resurrect it -
        clear `valid_to`, drop `superseded_by`, and return a fact the system had
        already retired, through the front door of a *retry*. Doing nothing is
        the only replay semantics that cannot undo a decision, and it is why the
        outbox id is derived from the assertion id rather than generated: a
        replay must not enqueue a second event for a write already dispatched.
        """
        if not assertions:
            return
        vectors = await self._embed(assertions)
        rows = [
            assertion_params(assertion, self._tenant_id, vector)
            for assertion, vector in zip(assertions, vectors, strict=True)
        ]
        citations = [params for assertion in assertions for params in provenance_params(assertion)]
        events = [outbox_params(assertion, self._tenant_id) for assertion in assertions]
        async with self._transaction() as connection:
            await connection.executemany(INSERT_ASSERTION, rows, timeout=self._timeout_s)
            await connection.executemany(INSERT_PROVENANCE, citations, timeout=self._timeout_s)
            await connection.executemany(INSERT_OUTBOX, events, timeout=self._timeout_s)

    async def search(
        self,
        *,
        namespace: Namespace,
        embedding: list[float],
        k: int,
        filters: dict[str, object],
        as_of: datetime | None = None,
    ) -> list[ScoredAssertion]:
        """Return the `k` nearest assertions: live now, or valid at `as_of`.

        Args:
            namespace: Isolation scope, and a required argument for the reason
                the protocol gives.
            embedding: The query vector, already computed by the read path.
            k: How many to return.
            filters: Keys drawn from `_FILTER_COLUMNS`. Anything else raises.
            as_of: `None` means live rows. A datetime selects rows whose world
                time contains it, which is what makes a superseded assertion
                recoverable - and the DONE WHEN of this step.

        Returns:
            Up to `k` scored assertions, nearest first, each hydrated with its
            full provenance list and carrying the cosine similarity the index
            ordered it by.

        Raises:
            StoreUnavailable: Postgres is unreachable.
            KeyError: `filters` named something outside the vocabulary.

        `as_of` deliberately cannot use `assertion_hnsw`: that index is partial
        on `valid_to IS NULL AND visible`, so a point-in-time query falls back to
        a sequential scan with an exact distance. That is the right trade - the
        index exists to make the hot path fast, and an audit-shaped question
        about what was true last March is neither hot nor improved by an
        approximate answer.
        """
        clauses, params = predicates(namespace, filters, as_of)
        params.append(embedding)
        vector_param = f"${len(params)}"
        params.append(k)
        statement = nearest_statement(clauses, vector_param, f"${len(params)}")
        async with self._transaction() as connection:
            rows = await connection.fetch(statement, *params, timeout=self._timeout_s)
            citations = await self._citations(connection, [row["id"] for row in rows])
        # `<=>` is cosine *distance*; the protocol publishes similarity. Converted
        # here, at the one place that knows which direction the operator runs in.
        return [
            ScoredAssertion(
                assertion=assertion_from_row(row, citations[row["id"]]),
                cosine=1.0 - float(row["distance"]),
            )
            for row in rows
        ]

    async def retired(
        self,
        *,
        namespace: Namespace,
        filters: dict[str, object],
        limit: int,
    ) -> list[StoredAssertion]:
        """Return what this namespace has stopped believing, newest first.

        Args:
            namespace: Isolation scope.
            filters: Keys drawn from `_FILTER_COLUMNS`, as `search`. Anything
                else raises.
            limit: How many.

        Returns:
            Retired assertions, each hydrated with its full provenance.

        Raises:
            StoreUnavailable: Postgres is unreachable.
            KeyError: `filters` named something outside the vocabulary.

        `_predicates` builds the shared clauses and is reused rather than
        copied - the tenant is applied by `SET LOCAL` on the transaction and the
        namespace and filter binding are identical, so a second hand-written
        WHERE here would be a second place for the isolation rules to drift.
        What differs is the temporal predicate, and it is the exact complement of
        the live one: `valid_to IS NOT NULL`.

        `ORDER BY valid_to DESC` rather than by distance, for the reason the
        protocol gives - and for a physical one. `assertion_hnsw` is partial on
        `valid_to IS NULL AND visible`, so no retired row is in it; ranking these
        by similarity would mean a sequential scan computing a distance against
        every dead row in the namespace, which is the wrong cost for a
        secondary field on a read.

        Deliberately not filtered on `visible`. A row can be superseded between
        its write and its relay pass, and such a row is retired and was never
        readable - which is exactly the kind of thing somebody asking "what
        happened to that fact?" needs to see. It cannot leak into retrieval,
        because retrieval is `search`.
        """
        clauses, params = predicates(namespace, filters, None)
        params.append(limit)
        statement = retired_statement(clauses, f"${len(params)}")
        async with self._transaction() as connection:
            rows = await connection.fetch(statement, *params, timeout=self._timeout_s)
            citations = await self._citations(connection, [row["id"] for row in rows])
        return [assertion_from_row(row, citations[row["id"]]) for row in rows]

    async def supersede(self, old_id: AssertionId, new_id: AssertionId, at: datetime) -> None:
        """Retire `old_id` in favour of `new_id`. Never deletes.

        Args:
            old_id: The incumbent being retired.
            new_id: What replaces it.
            at: World-time the supersession takes effect; becomes
                `old.valid_to`, abutting the successor's `valid_from` so a
                point-in-time query has no gap and no overlap.

        Raises:
            StoreUnavailable: Postgres is unreachable.
            ConcurrencyConflict: no live row with that id in this tenant - it
                was already retired, or it never existed here.

        `WHERE valid_to IS NULL` is the concurrency control. Two proposals racing
        to supersede the same incumbent both see it live; the first UPDATE wins,
        the second matches zero rows, and the loser is told to re-read rather
        than allowed to overwrite a `superseded_by` that now points at someone
        else's assertion. That is also why zero rows is an error and not a silent
        success: it is the only place a transposed `supersede(new, old)` can
        surface, since both arguments are `AssertionId` and nothing static
        distinguishes them.
        """
        async with self._transaction() as connection:
            status = await connection.execute(
                SUPERSEDE, at, new_id, old_id, timeout=self._timeout_s
            )
        if status.rsplit(" ", maxsplit=1)[-1] == "0":
            raise ConcurrencyConflict(
                f"assertion {old_id} is not live in tenant {self._tenant_id}: it was "
                "already superseded, it belongs to another tenant, or the arguments "
                "are transposed. Re-read with search() and re-propose."
            )

    # --- internals ----------------------------------------------------------

    def _transaction(self) -> AbstractAsyncContextManager[Conn]:
        """This store's tenant-scoped unit of work.

        Returns:
            A context manager yielding a connection with `app.tenant_id`
            applied. `SET LOCAL`, and `pool.py` explains at length why the
            `LOCAL` is the load-bearing word.

        A one-line forward rather than an import used directly at the three call
        sites: the tenant is a property of *this store*, and spelling it out
        three times is three chances to pass someone else's.
        """
        return tenant_transaction(self._pool, self._tenant_id, timeout_s=self._timeout_s)

    async def _embed(self, assertions: Sequence[StoredAssertion]) -> list[list[float]]:
        """Embed a batch, checking the shape the column declares.

        Args:
            assertions: The batch being written.

        Returns:
            One vector per assertion, in order.

        Raises:
            ValueError: the embedder returned the wrong count or dimension.
        """
        vectors = await self._embedder.embed([embed_text(a) for a in assertions])
        if len(vectors) != len(assertions):
            raise ValueError(
                f"embedder returned {len(vectors)} vectors for {len(assertions)} assertions"
            )
        for vector in vectors:
            if len(vector) != EMBEDDING_DIM:
                raise ValueError(
                    f"embedder returned a {len(vector)}-dimension vector; "
                    f"assertion.embedding is VECTOR({EMBEDDING_DIM}). Check GM_EMBED_MODEL."
                )
        return vectors

    async def _citations(
        self, connection: Conn, ids: Sequence[object]
    ) -> dict[object, list[Provenance]]:
        """Fetch every citation for `ids`, grouped by assertion.

        One query for the whole page rather than one per row. `StoredAssertion`
        requires `min_length=1` provenance, so this is not enrichment that could
        be skipped when it gets expensive - it is part of constructing the
        object, and N+1 here would be paid on every recall.
        """
        grouped: dict[object, list[Provenance]] = {id_: [] for id_ in ids}
        if not ids:
            return grouped
        rows = await connection.fetch(SELECT_PROVENANCE, list(ids), timeout=self._timeout_s)
        for row in rows:
            grouped[row["assertion_id"]].append(provenance_from_row(row))
        return grouped
