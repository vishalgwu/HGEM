"""`GraphStore` doubles that break in one specific way each.  S3.3

`FakeGraphStore` is the healthy one and lives in `fakes.py`; these are the
unhealthy ones. Kept apart from it deliberately - `fakes.py` exists to *keep*
the contract, because a fake that permits what a real store forbids makes a
whole suite measure the wrong system, and a module of deliberate contract
violations sitting beside it would blur that.

They are in `fixtures/` rather than in one test module because the fault is the
reusable part. The outbox relay is the first thing that has to survive a graph
outage; `ARCHITECTURE.md` §4 gives that outage a row of its own in the
degradation matrix, and `S7.1` swaps in a Neo4j backend that can actually
suffer it.

Each subclass overrides `upsert_assertion` and nothing else, so a test that
picks one is naming a single failure mode rather than configuring a mock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fixtures.fakes import FakeGraphStore
from guardmem_core.errors import StoreUnavailable

if TYPE_CHECKING:
    from guardmem_core.schemas.entity import Edge, StoredAssertion
    from guardmem_core.types import AssertionId, EntityId

__all__ = [
    "DelegatingGraph",
    "GraphOutage",
    "GraphThatCrashes",
    "GraphThatDiesAfterWriting",
    "SelectiveOutage",
]


class DelegatingGraph:
    """A healthy `GraphStore` that forwards to a real fake.

    The base, and usable on its own as the "nothing is wrong" case. The two read
    methods exist only to satisfy the protocol - `mypy --strict` covers `tests/`
    and `OutboxRelay` takes a `GraphStore`, so a double carrying one method
    would not type-check. Writing them once beats writing them five times.
    """

    def __init__(self) -> None:
        self.inner = FakeGraphStore()

    @property
    def edges(self) -> dict[AssertionId, Edge]:
        """Whatever actually reached the graph, which is the point of most of
        these: what landed before the failure is as interesting as the failure."""
        return dict(self.inner.edges)

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Write the edge."""
        await self.inner.upsert_assertion(a)

    async def neighbors(self, entity: EntityId, hops: int = 1) -> list[Edge]:
        """Forwarded. Protocol conformance; no relay test calls it."""
        return await self.inner.neighbors(entity, hops)

    async def degree(self, entity: EntityId) -> int:
        """Forwarded. Protocol conformance; no relay test calls it."""
        return await self.inner.degree(entity)


class GraphOutage(DelegatingGraph):
    """The backend is unreachable. `ARCHITECTURE.md` §4: retry, never drop."""

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Fail before anything lands."""
        raise StoreUnavailable("graph backend is unreachable")


class GraphThatDiesAfterWriting(DelegatingGraph):
    """The edge landed and nobody was told.

    The only window in which a retry re-applies work that already took, and
    therefore the only interesting place to kill a relay. See
    `test_outbox_relay.py`.
    """

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Write the edge, then lose the connection before returning."""
        await self.inner.upsert_assertion(a)
        raise StoreUnavailable("connection lost after the edge was committed")


class SelectiveOutage(DelegatingGraph):
    """One event cannot be written; the rest are fine.

    A poison message rather than an outage, which is a different question: does
    one bad row stall every write queued behind it?
    """

    poison = "allergy"

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Refuse the poison predicate; write anything else."""
        if a.predicate == self.poison:
            raise StoreUnavailable("this one edge cannot be written")
        await self.inner.upsert_assertion(a)


class GraphThatCrashes(DelegatingGraph):
    """A bug, not an outage. Nothing may mistake one for the other.

    `RULES.md` §2.3 permits a retry only where `retryable` is true, and this
    raises something that is not a `GuardMemError` at all.
    """

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Raise something the retry policy has no opinion about."""
        raise RuntimeError("predicate is not in the ontology")
