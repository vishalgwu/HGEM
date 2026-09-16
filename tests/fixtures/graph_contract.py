"""Every behaviour a `GraphStore` must show, whichever backend it is.  S7.1

S7.1's DONE WHEN is "the full integration suite passes against both backends
unchanged", and this module is what makes that sentence checkable rather than a
claim: the assertions live here once, and each backend's test module subclasses
:class:`GraphStoreContract` and supplies a `store`.

Before S7.1 these lived in `tests/unit/test_networkx_graph_store.py`,
parametrised over `FakeGraphStore` and `NetworkXGraphStore`. Neo4j cannot join
that parametrisation - it needs a container, so it belongs in the integration
suite - and copying sixteen assertions into a second module is how two backends
come to agree until somebody edits one. A base class costs a subclass per
backend and keeps one copy of the contract.

**What is deliberately not here.** The single-tenant guard is NetworkX's answer
to a question Neo4j answers by scoping, so it is a backend fact and lives with
its backend. Same for node-key collisions, which the fake has no node keys to
have. A contract is what every implementation owes, and putting a
backend-specific rule in it would force the other backends to fake compliance.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fixtures.assertions import TENANT, WHEN, stored_assertion
from guardmem_core.types import EntityId, TenantId

if TYPE_CHECKING:
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.entity import Edge, StoredAssertion

__all__ = ["LATER", "OTHER", "GraphStoreContract", "fact", "ids"]

LATER = WHEN + timedelta(days=30)
OTHER = TenantId("22222222-2222-2222-2222-222222222222")


def fact(
    *,
    subject: str = "e-1",
    predicate: str = "allergy",
    obj: ObjectValue = "penicillin",
    valid_to: datetime | None = None,
    tenant: TenantId = TENANT,
    assertion_id: str | None = None,
) -> StoredAssertion:
    """A sourced assertion, with the graph-relevant fields exposed.

    Over `fixtures.assertions.stored_assertion`. The axes here are the ones a
    graph cares about - who the subject is, what the object is, and whether the
    edge is still live - and everything else takes that builder's defaults.
    """
    return stored_assertion(
        assertion_id=assertion_id,
        tenant_id=tenant,
        subject=subject,
        predicate=predicate,
        obj=obj,
        valid_to=valid_to,
        trace_id="tr_s34",
    )


def ids(edges: list[Edge]) -> set[str]:
    """The assertion ids of a `neighbors()` result, unordered.

    Unordered deliberately. The protocol specifies no order within a hop, the
    fake returns insertion order, NetworkX walks adjacency and Cypher promises
    nothing without an `ORDER BY` - so pinning one would pin an order two of the
    three do not have.
    """
    return {edge.assertion_id for edge in edges}


class GraphStoreContract:
    """The shared behaviours. Subclass it and provide a `store` fixture.

    Not named `Test*`, so pytest does not collect it here - it has no `store` to
    run against. Each backend's module names its subclass `Test...`, which is
    what makes these run once per backend.
    """

    # --- degree -----------------------------------------------------------
    async def test_an_unknown_entity_has_degree_zero(self, store: GraphStore) -> None:
        """An unknown subject is a novel one, and §3.3 prices novelty separately."""
        assert await store.degree(EntityId("nobody")) == 0

    async def test_each_live_edge_out_of_an_entity_counts_once(self, store: GraphStore) -> None:
        await store.upsert_assertion(fact(predicate="allergy", obj="penicillin"))
        await store.upsert_assertion(fact(predicate="employer", obj="Northwind"))

        assert await store.degree(EntityId("e-1")) == 2

    async def test_an_entity_that_is_pointed_at_counts_too(self, store: GraphStore) -> None:
        """§3.3 asks how much depends on this node. Forty assertions pointing
        *at* an entity is exactly the blast radius the feature exists to price."""
        await store.upsert_assertion(fact(subject="e-1", predicate="employer", obj="e-2"))
        await store.upsert_assertion(fact(subject="e-3", predicate="employer", obj="e-2"))

        assert await store.degree(EntityId("e-2")) == 2

    async def test_a_retired_edge_does_not_count(self, store: GraphStore) -> None:
        """A retired belief inflating a risk feature is invariant I6 by another
        route: the fact is out of retrieval and would still be pricing writes."""
        await store.upsert_assertion(fact(obj="penicillin"))
        await store.upsert_assertion(fact(obj="latex", valid_to=LATER))

        assert await store.degree(EntityId("e-1")) == 1

    async def test_a_non_string_object_still_gives_the_subject_a_degree(
        self, store: GraphStore
    ) -> None:
        """A literal is still an edge - `ARCHITECTURE.md` §5 ends `ASSERTS` at an
        entity *or* a literal."""
        await store.upsert_assertion(fact(predicate="weight_kg", obj=71.5))

        assert await store.degree(EntityId("e-1")) == 1

    async def test_two_subjects_sharing_a_literal_are_counted_separately(
        self, store: GraphStore
    ) -> None:
        """Sharing a value is not sharing a fact."""
        await store.upsert_assertion(fact(subject="e-1", predicate="weight_kg", obj=71.5))
        await store.upsert_assertion(fact(subject="e-2", predicate="weight_kg", obj=71.5))

        assert await store.degree(EntityId("e-1")) == 1
        assert await store.degree(EntityId("e-2")) == 1

    # --- neighbors --------------------------------------------------------
    async def test_an_unknown_entity_has_no_neighbours(self, store: GraphStore) -> None:
        assert await store.neighbors(EntityId("nobody")) == []

    async def test_one_hop_returns_the_entitys_own_edges(self, store: GraphStore) -> None:
        """`MEMORY_ENGINE.md` §2.2 expands incumbent retrieval one hop out."""
        mine = fact(predicate="allergy", obj="penicillin")
        also_mine = fact(predicate="employer", obj="Northwind")
        await store.upsert_assertion(mine)
        await store.upsert_assertion(also_mine)
        await store.upsert_assertion(fact(subject="e-9", obj="latex"))

        found = await store.neighbors(EntityId("e-1"))

        assert ids(found) == {mine.assertion_id, also_mine.assertion_id}

    async def test_a_retired_edge_is_not_a_neighbour(self, store: GraphStore) -> None:
        """The protocol says live edges only: feeding a superseded belief into
        conflict detection is how a retired fact re-enters the system."""
        live = fact(obj="penicillin")
        await store.upsert_assertion(live)
        await store.upsert_assertion(fact(obj="latex", valid_to=LATER))

        assert ids(await store.neighbors(EntityId("e-1"))) == {live.assertion_id}

    async def test_one_hop_stops_at_one_hop(self, store: GraphStore) -> None:
        """Two hops on a dense entity is a different cost class, so the default
        is the safe one."""
        first = fact(subject="e-1", predicate="employer", obj="e-2")
        await store.upsert_assertion(first)
        await store.upsert_assertion(fact(subject="e-2", predicate="located_in", obj="e-3"))

        assert ids(await store.neighbors(EntityId("e-1"))) == {first.assertion_id}

    async def test_two_hops_follow_an_entity_reference(self, store: GraphStore) -> None:
        """A string object may be an entity id. The ontology decides which, and
        there is no ontology until S3.5 - so both stores follow every string."""
        first = fact(subject="e-1", predicate="employer", obj="e-2")
        second = fact(subject="e-2", predicate="located_in", obj="e-3")
        await store.upsert_assertion(first)
        await store.upsert_assertion(second)

        found = await store.neighbors(EntityId("e-1"), hops=2)

        assert ids(found) == {first.assertion_id, second.assertion_id}

    async def test_a_literal_is_not_followed(self, store: GraphStore) -> None:
        """A number cannot be an entity reference, so a walk ends there."""
        first = fact(subject="e-1", predicate="weight_kg", obj=71.5)
        await store.upsert_assertion(first)
        await store.upsert_assertion(fact(subject="e-2", obj="latex"))

        assert ids(await store.neighbors(EntityId("e-1"), hops=3)) == {first.assertion_id}

    async def test_a_cycle_terminates(self, store: GraphStore) -> None:
        """Each edge is visited once, so a loop is walked rather than looped in."""
        there = fact(subject="e-1", predicate="knows", obj="e-2")
        back = fact(subject="e-2", predicate="knows", obj="e-1")
        await store.upsert_assertion(there)
        await store.upsert_assertion(back)

        found = await store.neighbors(EntityId("e-1"), hops=10)

        assert ids(found) == {there.assertion_id, back.assertion_id}

    async def test_zero_hops_returns_nothing(self, store: GraphStore) -> None:
        """A degenerate argument, answered rather than raised on."""
        await store.upsert_assertion(fact())

        assert await store.neighbors(EntityId("e-1"), hops=0) == []

    # --- replay -----------------------------------------------------------
    async def test_writing_the_same_assertion_twice_leaves_one_edge(
        self, store: GraphStore
    ) -> None:
        """The S3.3 relay retries a dispatch it is not sure completed, so this
        has to be idempotent by `assertion_id` or every restart inflates
        `degree()` - and with it the risk score of every write to that subject."""
        written = fact(assertion_id="a-fixed")

        await store.upsert_assertion(written)
        await store.upsert_assertion(written)

        assert await store.degree(EntityId("e-1")) == 1

    async def test_a_replay_carries_the_current_state_of_the_assertion(
        self, store: GraphStore
    ) -> None:
        """Not just deduplicated - refreshed. A replay after supersession writes
        the retirement through, which is the only way the graph learns about it
        today."""
        await store.upsert_assertion(fact(assertion_id="a-fixed"))

        await store.upsert_assertion(fact(assertion_id="a-fixed", valid_to=LATER))

        assert await store.degree(EntityId("e-1")) == 0
