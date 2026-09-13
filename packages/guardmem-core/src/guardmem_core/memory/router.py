"""The write entry point: where an approved assertion becomes durable.  S3.3

`ARCHITECTURE.md` §2.4 gives this module two jobs. It "decides destination per
assertion", and it "coordinates the dual write via a Postgres outbox". Only the
second is real today, and this file says so rather than pretending otherwise -
see `route` for why the first cannot be built yet and what will build it.

**What the router is actually for, at this step.** It is the app-layer half of
`RULES.md` §4's defence in depth: "RLS *and* namespace prefixing *and* an
app-layer check". The other two exist - `0001_initial` puts an RLS policy on
every tenant-scoped table, and `Namespace` is on every assertion and every
search. The check was the missing one, and there is a specific hole it closes.
`rowmap.assertion_params` takes the *store's* tenant as authoritative and
ignores the one on the model, which is right (the store's came from an
authenticated request) and silent (a batch carrying another tenant's assertion
is re-tenanted rather than refused). Postgres cannot catch that: by the time RLS
sees the row it has already been relabelled, and the insert is perfectly legal.
This is the only layer that can, so it does.

**What it deliberately is not.** Not a `supersede` coordinator: retiring an
incumbent as part of writing its successor is `MEMORY_ENGINE.md` §2.3, and the
conflict detection that decides *whether* to is Layer 2, which does not exist.
Not a visibility authority either - nothing here can make a row readable, and
that is the point of the step. The router's write returns with the facts
durable, sourced, committed, and invisible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core.errors import ValidationRejected

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.memory.vector.base import VectorStore
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.types import TenantId

__all__ = ["GRAPH", "VECTOR", "Destination", "StoreRouter"]

# Where an assertion's state is materialised. A frozenset rather than an enum
# because §2.4's answer is a *set* - "most facts -> both" - and the interesting
# cases are the combinations, not the members.
type Destination = frozenset[str]

VECTOR: Final = "vector"
GRAPH: Final = "graph"

# Every fact goes to both, today. See `StoreRouter.route`.
_BOTH: Final[Destination] = frozenset({VECTOR, GRAPH})


class StoreRouter:
    """The one call a write path makes to persist approved assertions.

    Bound to a tenant, like `PgVectorStore` and for the same reason: the answer
    to "which tenant is this?" should come from an authenticated request once,
    at construction, rather than from whatever the caller was holding at each
    call. Here it buys something extra - a value to check every assertion in a
    batch against.
    """

    def __init__(self, vector: VectorStore, *, tenant_id: TenantId) -> None:
        """Bind a vector store and the tenant it is allowed to write for.

        Args:
            vector: Where the durable write goes. A `VectorStore` by structure,
                so a backend swap stays configuration. On the Postgres backend
                this single call also enqueues the outbox event, atomically -
                see `PgVectorStore.upsert` for why that belongs there and not
                here.
            tenant_id: The tenant this router speaks for. Must match the
                `tenant_id` on every assertion it is asked to write.

        The graph store is deliberately absent. The graph half of the dual write
        is applied by `memory/relay.py`, after the commit, which is what makes
        it retryable on a graph outage - `ARCHITECTURE.md` §4 requires that
        outage to degrade the write rather than drop the assertion, and a
        synchronous graph call here would drop it.
        """
        self._vector = vector
        self._tenant_id = tenant_id

    def route(self, assertion: StoredAssertion) -> Destination:
        """Decide where an assertion's state belongs.

        Args:
            assertion: The fact being written.

        Returns:
            Both stores, always, at this step.

        `ARCHITECTURE.md` §2.4 splits this three ways - "semantic/episodic
        content -> vector; typed relational predicates -> graph; most facts ->
        both" - and the distinguishing property is the predicate's declared
        type, which lives in the ontology. `MEMORY_ENGINE.md` §2.1 owns that
        ontology and `S3.5` builds its loader, so until then there is no
        information in the system with which to return anything else.

        Returning the "most facts" answer is the right stand-in rather than a
        placeholder. Guessing the other two would be a guess in the direction
        that loses data: an assertion wrongly routed vector-only writes no edge,
        so `degree()` under-reports and `MEMORY_ENGINE.md` §3.3's blast-radius
        feature silently prices a change as safer than it is. Writing an edge
        that later turns out to be unnecessary costs a row.
        """
        return _BOTH

    async def write(self, assertions: Sequence[StoredAssertion]) -> None:
        """Persist approved assertions, durably and invisibly.

        Args:
            assertions: Facts that have already been decided. An empty sequence
                is a no-op, not an error - a proposal whose candidates were all
                rejected is a normal outcome and should not make its caller
                branch.

        Raises:
            ValidationRejected: an assertion belongs to another tenant, or
                arrives already visible. Both are caller bugs rather than data
                problems, and both are checked before anything is written, so a
                bad batch writes nothing at all.
            StoreUnavailable: Postgres is unreachable. Retryable.

        Returns when the rows are committed and **not yet readable**. That is
        the contract, and a caller that searches for what it just wrote will
        correctly find nothing until the relay has run. Anything else would make
        the invisible write pointless.
        """
        self._reject_foreign_tenants(assertions)
        self._reject_presumed_visibility(assertions)
        await self._vector.upsert(assertions)

    # --- internals ----------------------------------------------------------

    def _reject_foreign_tenants(self, assertions: Sequence[StoredAssertion]) -> None:
        """Refuse a batch carrying an assertion from another tenant.

        Args:
            assertions: The batch.

        Raises:
            ValidationRejected: naming the first offending assertion.

        The module docstring has the argument for why this cannot be left to the
        database. In short: the store relabels rather than refuses, so by the
        time RLS sees the row it is telling the truth.
        """
        foreign = [a for a in assertions if a.tenant_id != self._tenant_id]
        if foreign:
            raise ValidationRejected(
                f"{len(foreign)} of {len(assertions)} assertions belong to another "
                f"tenant; this router writes for {self._tenant_id}. The store would "
                "relabel them rather than refuse, so the batch is rejected here.",
                trace_id=foreign[0].trace_id,
                candidate_id=foreign[0].assertion_id,
            )

    def _reject_presumed_visibility(self, assertions: Sequence[StoredAssertion]) -> None:
        """Refuse a batch that has decided its own visibility.

        Args:
            assertions: The batch.

        Raises:
            ValidationRejected: naming the first offending assertion.

        `visible` is ignored by `INSERT_ASSERTION`, which writes the literal
        `false`, so a `True` here changes nothing and that is exactly the
        problem: a caller that set it believes it has published a fact and has
        not. The value is the relay's to set (`ARCHITECTURE.md` §2.4), and a
        silent no-op is a worse answer than a loud refusal.
        """
        presumed = [a for a in assertions if a.visible]
        if presumed:
            raise ValidationRejected(
                f"{len(presumed)} of {len(assertions)} assertions arrived with "
                "visible=true. Visibility is set by the outbox relay once the graph "
                "side lands, never by a writer; the store would ignore the flag.",
                trace_id=presumed[0].trace_id,
                candidate_id=presumed[0].assertion_id,
            )
