"""Which graph backend a composition root builds.  BUILD_NOTEBOOK.md S7.1

S7.1's instruction in one line: "Config flag `GM_GRAPH_BACKEND=neo4j|networkx`."
This is that flag, and it is the same shape as `llm/providers/selection.py`
because it answers the same kind of question - `ARCHITECTURE.md` §2.4's "the
backend is an operator decision", applied to the graph.

**An async context manager, and that is a lesson rather than a preference.**
`build_llm` returned a bare adapter over an SDK client nobody could close, and
the transport leaked for eight steps because the `__del__` the SDK installs hid
it. A Neo4j `AsyncDriver` owns a connection pool with exactly the same problem,
and a stdio server restarting in a crash loop would leave one behind per attempt.
Yielding puts the lifetime where `RULES.md` §2.2 wants it: the composition root
writes `async with build_graph(settings) as graph:` and the driver closes on the
way out, cleanly or not.

**The NetworkX arm builds nothing to close**, which is the asymmetry that makes
the context manager worth having rather than an inconvenience: only the branch
that opened something releases it, and the caller does not have to know which
branch it took.

**Durability is reported, not inferred.** `build_graph` yields the store and
whether it survives a restart, because only the thing that chose the backend
knows - and `mcp_server.lifespan.ServerState.graph_durable` exists precisely so
a handler does not have to `isinstance` its way to the answer. `memory.get_entity`
reports it, so an empty neighbour list reads as "this process has no durable
graph" rather than "this entity is isolated", which matters because that list
feeds `MEMORY_ENGINE.md` §3.3's blast-radius score.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from neo4j import AsyncGraphDatabase
from neo4j import exceptions as neo4j_errors

from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.graph.cypher import SCHEMA
from guardmem_core.memory.graph.neo4j_store import Neo4jGraphStore
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from neo4j import AsyncDriver

    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.settings import Settings

__all__ = ["BoundGraph", "build_graph", "ensure_schema"]


@dataclass(frozen=True, slots=True)
class BoundGraph:
    """A graph store and the one fact about it a caller cannot ask it for.

    Attributes:
        store: The backend, typed as the `GraphStore` protocol. Nothing above
            this line may name a concrete class - `ARCHITECTURE.md` §2.4 makes
            the backend configuration, and the `import-linter` contract keeps
            `pipeline/` honest about it.
        durable: Whether the graph survives this process. False for NetworkX,
            which holds it in memory and loses it on restart; true for Neo4j.
            Carried rather than derived, because an `isinstance` check at the
            point of use has to name a concrete class, gets the answer wrong for
            any third implementation, and would quietly report a *test double*
            as durable.
    """

    store: GraphStore
    durable: bool


@asynccontextmanager
async def build_graph(settings: Settings) -> AsyncIterator[BoundGraph]:
    """Open the graph backend `settings.graph_backend` names, and close it after.

    Args:
        settings: The process configuration. `graph_backend` selects; the Neo4j
            arm reads `neo4j_uri`, `neo4j_user` and `neo4j_password`.

    Yields:
        The store and its durability. For Neo4j the driver underneath is closed
        when the block exits; the NetworkX arm has nothing to close.

    Raises:
        StoreUnavailable: the Neo4j arm could not reach the server. Raised on
            entry, after `verify_connectivity`, so a misconfigured graph fails
            at startup rather than on the first write - which for a graph would
            otherwise mean the relay discovering it, one event at a time, with
            `attempts` climbing and no obvious cause.

    **Connectivity is verified before the store is yielded, and the schema is
    applied.** Both are startup work a caller would otherwise have to remember:
    the constraints in `cypher.SCHEMA` are what make `MERGE` correct under
    concurrency rather than merely fast, and a store handed out before they
    exist can create duplicate entity nodes that no later constraint can repair.
    """
    if settings.graph_backend == "neo4j":
        driver = AsyncGraphDatabase.driver(
            str(settings.neo4j_uri),
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        )
        try:
            await _verify(driver)
            await ensure_schema(driver)
            yield BoundGraph(store=Neo4jGraphStore(driver), durable=True)
        finally:
            await driver.close()
        return
    yield BoundGraph(store=NetworkXGraphStore(), durable=False)


async def ensure_schema(driver: AsyncDriver) -> None:
    """Create the constraints and indexes the Neo4j backend depends on.

    Args:
        driver: A connected `AsyncDriver`.

    Raises:
        StoreUnavailable: the server is unreachable.

    Every statement is `IF NOT EXISTS`, so this is safe to run at every startup
    and safe to run from two processes at once - which is the case that actually
    happens, because a worker and a server start together.

    Separate from `build_graph` so a migration or a test fixture can apply the
    schema to a database it opened itself, and because "what this backend needs
    to exist" is a fact about the schema rather than about the composition root.
    """
    for statement in SCHEMA:
        try:
            await driver.execute_query(statement)
        except (
            neo4j_errors.ServiceUnavailable,
            neo4j_errors.SessionExpired,
            neo4j_errors.TransientError,
        ) as exc:
            raise StoreUnavailable(f"neo4j is unreachable applying schema: {exc}") from exc


async def _verify(driver: AsyncDriver) -> None:
    """Fail at startup rather than at the first write.

    Raises:
        StoreUnavailable: the server did not answer, or refused the credentials.

    `AuthError` is translated too, which is the one place this module is
    deliberately less precise than `neo4j_store._run`: a wrong password is a
    configuration error rather than an outage, and treating it as retryable
    would be wrong - but it is raised *here*, at startup, where nothing retries
    and where the message names the variable. `GM_NEO4J_PASSWORD` is the value
    an operator got wrong, and telling them that at boot is worth more than a
    taxonomically pure exception type.
    """
    try:
        await driver.verify_connectivity()
    except neo4j_errors.AuthError as exc:
        raise StoreUnavailable(
            f"neo4j refused the credentials in GM_NEO4J_USER/GM_NEO4J_PASSWORD: {exc}"
        ) from exc
    # Both base classes, in one clause, because they are **disjoint**:
    # `Neo4jError` is what the server sends back and `DriverError` is what the
    # client raises on its own, and they meet only at `GqlError`, which also
    # covers things this should not swallow. Naming one would miss half the
    # ways a connectivity check fails - `ServiceUnavailable` is a `DriverError`,
    # `TransientError` is a `Neo4jError` - and two clauses with byte-identical
    # bodies is a branch no test can distinguish.
    except (neo4j_errors.Neo4jError, neo4j_errors.DriverError) as exc:
        raise StoreUnavailable(f"neo4j is unreachable at GM_NEO4J_URI: {exc}") from exc
