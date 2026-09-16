"""S7.1's DONE WHEN, against a real Neo4j.  BUILD_NOTEBOOK.md S7.1

    "the full integration suite passes against both backends unchanged"

The first half of that is `TestTheContractOnNeo4j`, which inherits every
assertion `tests/unit/test_networkx_graph_store.py` runs and supplies a Neo4j
store instead. *Unchanged* is the load-bearing word: the tests are not rewritten
for this backend, they are the same objects, so a behaviour that differs is a
failure rather than a note in a docstring.

The second half - the rest of the integration suite running against a Neo4j-backed
server - is `test_mcp_stdio.py` and `test_mcp_memory_tools.py` going through
`build_graph`, which they now do by configuration.

**What is here and not in the contract.** Three things only this backend can be
asked about: that it is actually durable (a second store over the same database
sees the first one's edges, which is the entire point of the step), that a read
is scoped to the tenant of the node it starts at, and that a subject collision
is refused. The first is what `networkx` cannot do and the second is what it
does not have to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.assertions import TENANT
from fixtures.graph_contract import OTHER, GraphStoreContract, fact, ids
from guardmem_core.errors import ValidationRejected
from guardmem_core.memory.graph.neo4j_store import Neo4jGraphStore
from guardmem_core.settings import get_settings
from guardmem_core.types import EntityId
from mcp_server.lifespan import lifespan

if TYPE_CHECKING:
    from collections.abc import Iterator

    from neo4j import AsyncDriver

    from guardmem_core.memory.graph.base import GraphStore


class TestTheContractOnNeo4j(GraphStoreContract):
    """Every shared behaviour, against Neo4j.

    Sixteen inherited assertions, none of them written for this backend. That is
    the DONE WHEN: the pipeline holds a `GraphStore` and the two implementations
    answer the same questions the same way, or this class fails.
    """

    @pytest.fixture
    def store(self, neo4j_store: GraphStore) -> GraphStore:
        """The Neo4j backend over a graph wiped for this test."""
        return neo4j_store


class TestItIsActuallyDurable:
    """The property the whole step exists for, and the one NetworkX cannot have."""

    async def test_a_second_store_sees_the_first_ones_edges(
        self, neo4j_driver: AsyncDriver
    ) -> None:
        """`NetworkXGraphStore` holds the graph in the process, so a restart
        loses it and the outbox is what rebuilds it. This is the backend that
        does not need rebuilding, and a second store object over the same
        database is the closest a test gets to a restart.
        """
        first = Neo4jGraphStore(neo4j_driver)
        await first.upsert_assertion(fact(subject="e-1", obj="penicillin"))

        second = Neo4jGraphStore(neo4j_driver)

        assert await second.degree(EntityId("e-1")) == 1

    async def test_the_edge_keeps_every_field_the_protocol_promises(
        self, neo4j_store: GraphStore
    ) -> None:
        """A round trip through Cypher properties is where a field quietly
        becomes the wrong type.

        `valid_from` goes in as a `datetime` and comes back as a
        `neo4j.time.DateTime` unless something converts it, and `object` is
        stored as JSON because a Neo4j property cannot hold a map. Both are
        invisible until a `ConflictReport` compares one against a Postgres row.
        """
        await neo4j_store.upsert_assertion(fact(subject="e-1", predicate="age", obj=42))

        edge = (await neo4j_store.neighbors(EntityId("e-1")))[0]

        assert edge.predicate == "age"
        assert edge.object == 42
        assert edge.valid_from == fact().valid_from
        assert edge.valid_to is None
        assert edge.trace_id == "tr_s34"

    async def test_a_structured_object_survives_the_round_trip(
        self, neo4j_store: GraphStore
    ) -> None:
        """The case the property model cannot store directly.

        Neo4j properties hold scalars and flat lists, never maps - so a
        structured object has to be serialised, and reading the node's *key*
        back instead of the JSON would turn `{"dose": 5}` into the string
        `literal:{"dose": 5}` somewhere inside a conflict check.
        """
        await neo4j_store.upsert_assertion(
            fact(subject="e-1", predicate="dosage", obj={"unit": "mg", "value": 5})
        )

        edge = (await neo4j_store.neighbors(EntityId("e-1")))[0]

        assert edge.object == {"unit": "mg", "value": 5}


class TestTenantScoping:
    """What this backend does instead of refusing a second tenant.

    `NetworkXGraphStore` is single-tenant and enforces it by raising, because
    the protocol's reads take an `EntityId` and no tenant so it could not filter
    even if it wanted to. Neo4j is the multi-tenant one, so it filters - and
    these are the tests that say the filter is real rather than intended.
    """

    async def test_a_shared_object_node_does_not_bridge_two_tenants(
        self, neo4j_store: GraphStore
    ) -> None:
        """The leak this scoping exists to stop.

        Two tenants recording an allergy to penicillin legitimately converge on
        one node - `ARCHITECTURE.md` §5's edge ends at a literal, and a literal
        is shared by construction. Without the filter a walk from one tenant's
        patient passes through it and returns the other's edges, straight into
        `MEMORY_ENGINE.md` §2.2's incumbent set and from there into conflict
        detection: a cross-tenant read with no symptom.
        """
        await neo4j_store.upsert_assertion(
            fact(subject="e-mine", obj="penicillin", tenant=TENANT, assertion_id="a-mine")
        )
        await neo4j_store.upsert_assertion(
            fact(subject="e-theirs", obj="penicillin", tenant=OTHER, assertion_id="a-theirs")
        )

        mine = await neo4j_store.neighbors(EntityId("e-mine"), hops=2)
        theirs = await neo4j_store.neighbors(EntityId("e-theirs"), hops=2)

        assert ids(mine) == {"a-mine"}
        assert ids(theirs) == {"a-theirs"}

    async def test_degree_counts_only_the_starting_tenants_edges(
        self, neo4j_store: GraphStore
    ) -> None:
        """`degree()` feeds the blast-radius score, so another tenant's edges
        inflating it is a wrong decision rather than merely a leak."""
        await neo4j_store.upsert_assertion(
            fact(subject="e-mine", obj="shared", tenant=TENANT, assertion_id="a-1")
        )
        await neo4j_store.upsert_assertion(
            fact(subject="e-mine", obj="other", tenant=TENANT, assertion_id="a-2")
        )
        await neo4j_store.upsert_assertion(
            fact(subject="e-theirs", obj="shared", tenant=OTHER, assertion_id="a-3")
        )

        assert await neo4j_store.degree(EntityId("e-mine")) == 2

    async def test_two_tenants_may_hold_their_own_subjects(self, neo4j_store: GraphStore) -> None:
        """The thing NetworkX refuses and this backend is for. A store that
        raised here would make the Neo4j arm pointless."""
        await neo4j_store.upsert_assertion(fact(subject="e-mine", tenant=TENANT))
        await neo4j_store.upsert_assertion(fact(subject="e-theirs", tenant=OTHER))

        assert await neo4j_store.degree(EntityId("e-mine")) == 1
        assert await neo4j_store.degree(EntityId("e-theirs")) == 1

    async def test_a_subject_claimed_by_two_tenants_is_refused(
        self, neo4j_store: GraphStore
    ) -> None:
        """Not a tenancy limit - a collision.

        ADR-0008 derives an `EntityId` as a `uuid5` over the tenant, so two
        tenants cannot legitimately reach one id. One that did would merge two
        customers' graphs, and every `degree()` afterwards would be wrong in a
        direction nobody would notice.
        """
        await neo4j_store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected) as raised:
            await neo4j_store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

        assert "already held for tenant" in str(raised.value)

    async def test_the_refusal_names_the_proposal_it_came_from(
        self, neo4j_store: GraphStore
    ) -> None:
        """`RULES.md` §6: an error a relay records has to say which write it was."""
        await neo4j_store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected) as raised:
            await neo4j_store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

        assert raised.value.trace_id == "tr_s34"


class TestTheServerRunsOnEitherBackend:
    """S7.1's DONE WHEN, read as a sentence about the *server*.

    The contract class above proves the two stores answer alike. This proves the
    swap is actually wired: the same lifespan, the same `ServerState`, started
    twice with nothing different but `GM_GRAPH_BACKEND` - which is what "by
    config with no code change" has to mean to be worth writing down.
    """

    @pytest.fixture
    def _both_stores(
        self, app_role_dsn: str, neo4j_uri: str, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        """Point the lifespan at both testcontainers.

        `GM_NEO4J_URI` is overridden because a developer's `.env` names the dev
        stack and CI has no `.env` at all - the same reason `_script_env`
        overrides the DSN. A test that inherited either would be testing a
        different server on the two machines.
        """
        monkeypatch.setenv("GM_DATABASE_URL", app_role_dsn)
        monkeypatch.setenv("GM_NEO4J_URI", neo4j_uri)
        monkeypatch.setenv("GM_NEO4J_USER", "neo4j")
        monkeypatch.setenv("GM_NEO4J_PASSWORD", "guardmem123")  # pragma: allowlist secret
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.mark.usefixtures("_both_stores")
    async def test_the_networkx_backend_starts_and_reports_itself_volatile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default. `graph_durable` false is what `memory.get_entity` reports
        so an empty neighbour list reads as "no durable graph" rather than
        "isolated entity"."""
        monkeypatch.setenv("GM_GRAPH_BACKEND", "networkx")
        get_settings.cache_clear()

        async with lifespan() as state:
            assert state.graph_durable is False
            assert await state.graph.degree(EntityId("e-nobody")) == 0

    @pytest.mark.usefixtures("_both_stores")
    async def test_the_neo4j_backend_starts_and_reports_itself_durable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The swap. Same lifespan, same state, one environment variable.

        Asserting on a *write* rather than only on the flag: `build_graph`
        verifies connectivity and applies the schema before yielding, so a store
        that came back at all has already proved more than an `isinstance` would.
        """
        monkeypatch.setenv("GM_GRAPH_BACKEND", "neo4j")
        get_settings.cache_clear()

        async with lifespan() as state:
            assert state.graph_durable is True
            await state.graph.upsert_assertion(fact(subject="e-wired", obj="penicillin"))

            assert await state.graph.degree(EntityId("e-wired")) == 1

    @pytest.mark.usefixtures("_both_stores")
    async def test_the_driver_is_closed_when_the_lifespan_unwinds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Neo4j driver owns a connection pool, and `build_llm` already taught
        this repository what an unclosed one costs: a supervisor restarting a
        crash loop leaves one behind per attempt. The store is unusable
        afterwards, which is what proves the close happened."""
        monkeypatch.setenv("GM_GRAPH_BACKEND", "neo4j")
        get_settings.cache_clear()

        async with lifespan() as state:
            graph = state.graph

        with pytest.raises(Exception, match="closed"):
            await graph.degree(EntityId("e-wired"))
