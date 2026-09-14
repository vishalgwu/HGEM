"""`NetworkXGraphStore`, and the fake it has to agree with.  BUILD_NOTEBOOK.md S3.4

S3.4's DONE WHEN has two clauses. "`degree()` and `neighbors()` return correct
values in a unit test" is most of this module; "the pipeline never imports the
concrete class" is an `import-linter` contract in `pyproject.toml`, because
`RULES.md` §0 wants rules checked by a linter where one can do it - and
`TestTheDoneWhenIsEnforced` below pins the contract itself against quiet
deletion.

**Every shared behaviour runs against both implementations.** The `store`
fixture is parametrised over `FakeGraphStore` and `NetworkXGraphStore`, so each
test below runs twice. That is not thoroughness for its own sake: `fakes.py`
says in as many words that a fake permitting what a real store forbids makes the
whole week-1 unit suite a measurement of the wrong system, and until today there
was no real `GraphStore` to check that claim against. Now there is, and the two
answer the same questions the same way or this file fails.

What is **not** shared has its own class. The single-tenant guard is specific to
this backend, and so is the question of what a non-string object is stored as -
the fake has no node keys to collide.

`degree()` is the input to `MEMORY_ENGINE.md` §3.3's `graph_fanout` feature,
`min(1, log(1+deg)/log(1+50))`. The formula is not tested here because
`l3_score/impact.py` does not exist yet; what is tested is that the number it
will be handed counts live edges, in both directions, exactly once.
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from conftest import REPO_ROOT
from fixtures.assertions import TENANT, WHEN, stored_assertion
from fixtures.fakes import FakeGraphStore
from guardmem_core.errors import ValidationRejected
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.schemas.base import ObjectValue
from guardmem_core.schemas.entity import Edge, StoredAssertion
from guardmem_core.types import EntityId, TenantId

if TYPE_CHECKING:
    from guardmem_core.memory.graph.base import GraphStore

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


@pytest.fixture(params=["fake", "networkx"])
def store(request: pytest.FixtureRequest) -> GraphStore:
    """Both implementations, so every shared assertion below runs twice."""
    if request.param == "fake":
        return FakeGraphStore()
    return NetworkXGraphStore()


def ids(edges: list[Edge]) -> set[str]:
    """The assertion ids of a `neighbors()` result, unordered.

    Unordered deliberately. The protocol specifies no order within a hop, the
    fake returns insertion order and the real store walks adjacency, and pinning
    either would pin one Neo4j will not reproduce either.
    """
    return {edge.assertion_id for edge in edges}


class TestDegree:
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


class TestNeighbors:
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


class TestReplay:
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


class TestTheSingleTenantGuard:
    """Specific to this backend; `FakeGraphStore` has no such guard."""

    async def test_a_subject_held_for_another_tenant_is_refused(self) -> None:
        """The protocol gives `degree()` no tenant to filter on, so a graph
        holding two tenants would price one's blast radius with the other's
        edges. Entity ids are database-wide UUIDs so the exposure is nil, and
        `RULES.md` §4 still wants the check rather than the coincidence."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected, match="single-tenant"):
            await store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

    async def test_the_owning_tenant_may_keep_writing(self) -> None:
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", predicate="allergy"))

        await store.upsert_assertion(fact(subject="e-1", predicate="employer", obj="Northwind"))

        assert await store.degree(EntityId("e-1")) == 2

    async def test_two_tenants_may_share_a_literal(self) -> None:
        """Guarded on the subject only. Two tenants recording an allergy to
        penicillin legitimately share the node `"penicillin"`, and refusing that
        would refuse a correct write."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", obj="penicillin", tenant=TENANT))

        await store.upsert_assertion(fact(subject="e-2", obj="penicillin", tenant=OTHER))

        assert await store.degree(EntityId("e-2")) == 1

    async def test_the_refusal_names_the_proposal_it_came_from(self) -> None:
        """`RULES.md` §2.3: every raise inside the pipeline carries `trace_id`
        and `candidate_id`."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected) as raised:
            await store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

        assert raised.value.trace_id == "tr_s34"
        assert raised.value.candidate_id is not None


class TestTheDoneWhenIsEnforced:
    def test_an_import_linter_contract_forbids_the_pipeline_a_concrete_store(
        self,
    ) -> None:
        """S3.4's second clause: "the pipeline never imports the concrete class".

        Pinned here because the enforcement lives in configuration, and a
        contract deleted from `pyproject.toml` fails nothing - `lint-imports`
        would simply report one fewer contract kept, in green.
        """
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        contracts = config["tool"]["importlinter"]["contracts"]
        forbidding = [c for c in contracts if c["source_modules"] == ["guardmem_core.pipeline"]]

        assert forbidding, "no contract stops the pipeline importing a concrete store"
        forbidden = set(forbidding[0]["forbidden_modules"])
        assert "guardmem_core.memory.graph.networkx_store" in forbidden
        assert "guardmem_core.memory.vector.pgvector_store" in forbidden
