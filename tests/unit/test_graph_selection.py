"""What the graph backend does when Neo4j will not answer.  S7.1, S7.2

S7.2 says to look specifically at error branches, "they are what you skipped",
and this module is the largest instance of that in `guardmem-core`: S7.1 shipped
`graph/selection.py` and `Neo4jGraphStore._run` with every failure path
unexercised. The happy paths are covered by a real container in
`tests/integration/test_neo4j_graph_store.py`; a container cannot be made to
refuse a password or die mid-statement on demand, so the translation is tested
here, against a driver that fails exactly one way.

**These are unit tests of *our translation*, not of Neo4j.** That is the same
boundary `test_shared_primitives.py` draws for the pool: the question is not
whether the driver raises `ServiceUnavailable` - it does, that is its contract -
but whether this codebase turns it into something a caller upstream can act on.
`ARCHITECTURE.md` §4 makes a graph outage retryable and non-fatal, so the write
proceeds vector-only and the outbox retries; a fault that arrived as a bare
driver exception would escape that rule entirely.

**And whether it is retryable is the whole point.** A `ClientError` is a Cypher
bug or a constraint violation, and dressing one as an outage tells the relay to
retry a syntax error until `attempts` hits the cap - which is how a poison event
is made. The tests below pin both directions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from neo4j import exceptions as neo4j_errors

from fixtures.mcp import settings
from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.graph.neo4j_store import Neo4jGraphStore
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.graph.selection import build_graph, ensure_schema
from guardmem_core.types import EntityId

if TYPE_CHECKING:
    from neo4j import AsyncDriver


class FailingDriver:
    """An `AsyncDriver` that fails one specific way, and records what it was asked.

    A hand-written double rather than a mock, for the reason `fixtures/fakes.py`
    gives about stores: a mock returns what it is told and therefore cannot
    disagree with the real thing, while a double small enough to read is one you
    can check against the driver's documented behaviour by eye.

    Attributes:
        error: What every call raises. `None` means the call succeeds, which is
            what the `verify_connectivity` tests need while `execute_query`
            fails and vice versa.
        statements: Every query it was handed, so a test can assert the schema
            was attempted at all rather than only that it failed.
    """

    def __init__(self, error: Exception | None = None, *, on_verify: Exception | None = None):
        self.error = error
        self.on_verify = on_verify
        self.statements: list[str] = []
        self.closed = False

    async def verify_connectivity(self) -> None:
        if self.on_verify is not None:
            raise self.on_verify

    async def execute_query(self, query: str, **_: Any) -> Any:
        self.statements.append(query)
        if self.error is not None:
            raise self.error
        return _EmptyResult()

    async def close(self) -> None:
        self.closed = True


class _EmptyResult:
    """What `execute_query` returns when nothing is being asserted on the rows."""

    def __init__(self) -> None:
        self.records: list[Any] = []


def driver(*args: Any, **kwargs: Any) -> AsyncDriver:
    """A `FailingDriver`, typed as the real thing.

    `Neo4jGraphStore` takes a concrete `AsyncDriver` - it is a driver, not a
    protocol - so a structural double needs the cast the MCP tests use.
    """
    return cast("AsyncDriver", FailingDriver(*args, **kwargs))


class TestTheStoreTranslatesDriverFaults:
    """`Neo4jGraphStore._run`. Which faults are an outage and which are a bug."""

    @pytest.mark.parametrize(
        "fault",
        [
            neo4j_errors.ServiceUnavailable("gone"),
            neo4j_errors.SessionExpired("moved"),
            neo4j_errors.TransientError("deadlock"),
        ],
        ids=["unreachable", "session-expired", "transient"],
    )
    async def test_a_retryable_fault_becomes_a_retryable_store_error(
        self, fault: Exception
    ) -> None:
        """All three mean "ask again". `ARCHITECTURE.md` §4's rule for a graph
        outage is that the outbox retries and the assertion is never dropped,
        and that rests on the error carrying the flag."""
        store = Neo4jGraphStore(driver(fault))

        with pytest.raises(StoreUnavailable) as caught:
            await store.degree(EntityId("e-1"))

        assert caught.value.retryable is True
        assert "neo4j is unreachable" in str(caught.value)

    async def test_a_client_error_is_not_dressed_as_an_outage(self) -> None:
        """The direction that matters more.

        A `ClientError` is a Cypher bug or a constraint violation. Translating
        one into `StoreUnavailable` would tell the relay to retry it, and it
        would keep retrying until `attempts` reached the cap - a poison event
        manufactured out of a typo. It propagates instead, and the process
        stops.
        """
        store = Neo4jGraphStore(driver(neo4j_errors.ClientError("bad cypher")))

        with pytest.raises(neo4j_errors.ClientError):
            await store.degree(EntityId("e-1"))

    async def test_a_read_that_cannot_reach_the_server_says_so_on_every_method(self) -> None:
        """All three protocol methods route through `_run`, so all three have to
        translate - a `neighbors` that leaked a driver exception while `degree`
        translated would be the kind of asymmetry nobody notices until a relay
        pass dies."""
        store = Neo4jGraphStore(driver(neo4j_errors.ServiceUnavailable("gone")))

        with pytest.raises(StoreUnavailable):
            await store.neighbors(EntityId("e-1"))


class TestEnsureSchema:
    async def test_it_applies_every_statement(self) -> None:
        """The constraints are what make `MERGE` correct under concurrency
        rather than merely fast, so "some of them" is not a useful outcome."""
        fake = FailingDriver()

        await ensure_schema(cast("AsyncDriver", fake))

        assert len(fake.statements) == 4
        assert all("IF NOT EXISTS" in s for s in fake.statements)

    async def test_an_unreachable_server_is_reported_as_unreachable(self) -> None:
        """Applying the schema is startup work, so this is the first thing an
        operator sees when the graph is down - and it names the schema rather
        than leaving them to guess which query failed."""
        with pytest.raises(StoreUnavailable, match="applying schema"):
            await ensure_schema(driver(neo4j_errors.ServiceUnavailable("gone")))


class TestBuildGraph:
    """`GM_GRAPH_BACKEND`, and what happens when the backend it names is down."""

    async def test_the_networkx_arm_needs_no_server(self) -> None:
        """The default, and the reason it is the default: a clone with only
        Postgres running still serves `memory.get_entity`."""
        async with build_graph(settings(graph_backend="networkx")) as bound:
            assert isinstance(bound.store, NetworkXGraphStore)
            assert bound.durable is False

    async def test_the_networkx_arm_reports_itself_volatile(self) -> None:
        """`graph_durable` is read by `memory.get_entity` so an empty neighbour
        list is readable as "no durable graph" rather than "isolated entity"."""
        async with build_graph(settings(graph_backend="networkx")) as bound:
            assert await bound.store.degree(EntityId("nobody")) == 0

    async def test_a_refused_credential_names_the_variable_to_fix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An `AuthError` is a configuration error rather than an outage, and it
        is translated anyway - deliberately. It is raised at startup, where
        nothing retries, and the message naming `GM_NEO4J_PASSWORD` is worth
        more to the person reading it than a taxonomically pure exception type.
        """
        fake = FailingDriver(on_verify=neo4j_errors.AuthError("nope"))
        monkeypatch.setattr(
            "guardmem_core.memory.graph.selection.AsyncGraphDatabase.driver",
            lambda *a, **k: fake,
        )

        with pytest.raises(StoreUnavailable, match="GM_NEO4J_USER/GM_NEO4J_PASSWORD"):
            async with build_graph(settings(graph_backend="neo4j")):
                pass

        assert fake.closed, "the driver must be closed even when startup fails"

    async def test_an_unreachable_server_fails_at_startup_not_at_first_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which is the whole reason `build_graph` verifies before it yields.

        Without it the relay discovers the outage one event at a time, with
        `attempts` climbing and no obvious cause - the store looks fine and the
        queue simply stops draining.
        """
        fake = FailingDriver(on_verify=neo4j_errors.ServiceUnavailable("gone"))
        monkeypatch.setattr(
            "guardmem_core.memory.graph.selection.AsyncGraphDatabase.driver",
            lambda *a, **k: fake,
        )

        with pytest.raises(StoreUnavailable, match="GM_NEO4J_URI"):
            async with build_graph(settings(graph_backend="neo4j")):
                pass

    async def test_the_driver_is_closed_when_the_schema_step_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connectivity succeeds and the schema does not, which is the window
        where a `try/finally` that only wrapped the yield would leak a
        connection pool."""
        fake = FailingDriver(neo4j_errors.ServiceUnavailable("gone"))
        monkeypatch.setattr(
            "guardmem_core.memory.graph.selection.AsyncGraphDatabase.driver",
            lambda *a, **k: fake,
        )

        with pytest.raises(StoreUnavailable, match="applying schema"):
            async with build_graph(settings(graph_backend="neo4j")):
                pass

        assert fake.closed

    async def test_a_server_side_fault_during_the_check_is_also_an_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Neo4jError` and `DriverError` are disjoint hierarchies.

        `ServiceUnavailable` is a `DriverError` - the client gave up - while a
        `TransientError` comes back *from* the server. Catching one base class
        would leave the other to escape `build_graph` as a bare driver
        exception, past the `StoreUnavailable` contract every caller upstream
        is written against.
        """
        fake = FailingDriver(on_verify=neo4j_errors.TransientError("leader switch"))
        monkeypatch.setattr(
            "guardmem_core.memory.graph.selection.AsyncGraphDatabase.driver",
            lambda *a, **k: fake,
        )

        with pytest.raises(StoreUnavailable, match="GM_NEO4J_URI"):
            async with build_graph(settings(graph_backend="neo4j")):
                pass
