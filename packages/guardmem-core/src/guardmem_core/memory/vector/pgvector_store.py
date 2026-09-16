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
from guardmem_core.memory.vector.base import ScoredAssertion
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.memory.vector.queries import (
    SUPERSEDE,
    fetch_citations,
    nearest_statement,
    predicates,
    retired_statement,
)
from guardmem_core.memory.vector.rowmap import (
    INSERT_ASSERTION,
    INSERT_PROVENANCE,
    PreparedWrite,
    assertion_from_row,
    assertion_params,
    embed_batch,
    provenance_params,
)
from guardmem_core.types import AssertionId, Namespace, TenantId

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from guardmem_core.memory.vector.base import Embedder
    from guardmem_core.memory.vector.pool import Conn
    from guardmem_core.schemas.entity import StoredAssertion

__all__ = ["PgVectorStore", "PreparedWrite"]


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

        One transaction is not an optimisation, and it carries two invariants.
        `assertion_requires_provenance` is a DEFERRABLE INITIALLY DEFERRED
        constraint trigger that fires at COMMIT, so an assertion whose citations
        went to a different transaction is rejected - correctly, as an unsourced
        write. And `ARCHITECTURE.md` §2.4 requires the assertion row and its
        outbox event to commit together: an assertion that landed without its
        event would be invisible with nothing left that could ever flip it,
        which is not a partial write but a permanently unreadable one.

        Idempotent by `ON CONFLICT DO NOTHING`, taken over `DO UPDATE`
        deliberately. S3.3 replays this after a relay restart, and by then the
        row may legitimately have been superseded. An overwrite would resurrect
        it - clear `valid_to`, drop `superseded_by` - returning a fact the
        system had retired, through the front door of a *retry*. It is also why
        the outbox id is derived rather than generated: a replay must not
        enqueue a second event for a dispatched write.

        A thin wrapper over `prepare` and `write_in` since ADR-0010, which is
        what keeps this path and the applier's from drifting.
        """
        prepared = await self.prepare(assertions)
        if prepared is None:
            return
        async with self._transaction() as connection:
            await self.write_in(connection, prepared)

    async def prepare(self, assertions: Sequence[StoredAssertion]) -> PreparedWrite | None:
        """Embed a batch and build its rows, outside any transaction.

        Args:
            assertions: What is about to be written.

        Returns:
            The three row sets `write_in` needs, or `None` for an empty batch -
            which is a normal outcome, not an error, when every candidate in a
            proposal was rejected.

        Raises:
            ValueError: an embedding came back with the wrong dimension.
            ProviderUnavailable: propagated from the embedder.

        **Split from `write_in` so the embedding never happens inside a
        transaction.** Embedding is a network call and a transaction holds a
        pooled connection; one inside the other means a slow provider occupies a
        connection another tenant's request is waiting for. `upsert` has always
        embedded first - this names the seam so ADR-0010's applier shares it.
        """
        if not assertions:
            return None
        vectors = await embed_batch(self._embedder, assertions)
        return PreparedWrite(
            assertions=[
                assertion_params(assertion, self._tenant_id, vector)
                for assertion, vector in zip(assertions, vectors, strict=True)
            ],
            citations=[
                params for assertion in assertions for params in provenance_params(assertion)
            ],
            events=[outbox_params(assertion, self._tenant_id) for assertion in assertions],
        )

    async def write_in(self, connection: Conn, prepared: PreparedWrite) -> None:
        """Write a prepared batch inside the caller's transaction.  ADR-0010

        Args:
            connection: A connection with a transaction open and
                `app.tenant_id` set. `pool.tenant_transaction` produces one.
            prepared: What `prepare` built.

        Raises:
            StoreUnavailable: propagated by the caller's transaction.

        On the concrete class and deliberately not on the `VectorStore`
        Protocol - **ADR-0010** owns that argument in full. `upsert` is a thin
        wrapper over `prepare` + this, so the two write paths cannot drift: a
        column added to `INSERT_ASSERTION` reaches both, or neither.
        """
        await connection.executemany(INSERT_ASSERTION, prepared.assertions, timeout=self._timeout_s)
        await connection.executemany(INSERT_PROVENANCE, prepared.citations, timeout=self._timeout_s)
        await connection.executemany(INSERT_OUTBOX, prepared.events, timeout=self._timeout_s)

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
            citations = await fetch_citations(
                connection, [row["id"] for row in rows], timeout_s=self._timeout_s
            )
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
            citations = await fetch_citations(
                connection, [row["id"] for row in rows], timeout_s=self._timeout_s
            )
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
            await self.supersede_in(connection, old_id, new_id, at)

    async def supersede_in(
        self, connection: Conn, old_id: AssertionId, new_id: AssertionId, at: datetime
    ) -> None:
        """Retire `old_id` inside the caller's transaction.  ADR-0010

        Args:
            connection: As `write_in`.
            old_id: The incumbent being retired.
            new_id: What replaces it.
            at: World-time the supersession takes effect.

        Raises:
            ConcurrencyConflict: no live row with that id in this tenant.

        **Raising rolls the caller's transaction back, and that is the point:**
        a lost race must not leave the successor stored beside a still-live
        incumbent, which is two live values for a `ONE` predicate - invariant I2
        broken by a retry. ADR-0010.
        """
        status = await connection.execute(SUPERSEDE, at, new_id, old_id, timeout=self._timeout_s)
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
