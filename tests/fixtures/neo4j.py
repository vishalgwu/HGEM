"""A Neo4j for the integration suite.  BUILD_NOTEBOOK.md S7.1

The same shape as `fixtures/postgres.py` and for the same reasons, so read that
module for the arguments this one only restates: the image is pinned to the tag
`infra/docker/docker-compose.dev.yml` uses, `GM_TEST_NEO4J_URI` short-circuits
the container for iterating against `make dev`, and the fixture is synchronous
because a session-scoped *async* fixture would outlive the function-scoped event
loop `pytest-asyncio` gives each test.

**Why a container rather than the dev stack.** S7.1's DONE WHEN is that the
integration suite passes against both backends, and a graph somebody has been
poking at by hand is not evidence of that - `degree()` counts edges, so one
leftover assertion from a manual session changes an answer this suite asserts on
exactly.

**The graph is wiped between tests, not between runs.** `postgres.py` leaves
cleanup to each test because rows are tenant-scoped and cheap to scope around.
Graph reads are *not* scoped that way - `degree(entity)` takes no tenant, which
is the whole seam `Neo4jGraphStore` documents - so one test's leftover edges are
another's wrong count. `neo4j_store` wipes rather than asks tests to remember.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import TYPE_CHECKING, Final

import pytest
from neo4j import AsyncGraphDatabase
from neo4j import exceptions as neo4j_errors

from guardmem_core.memory.graph.selection import ensure_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from neo4j import AsyncDriver

__all__ = ["neo4j_store", "neo4j_uri"]

# The same image and tag as the dev stack. Pinned for the reason `RULES.md` §3
# pins model ids: a floating tag makes a green run unreproducible, and a Neo4j
# major carries Cypher changes - `cypher.py`'s statements are written against 5.
_IMAGE: Final = "neo4j:5.26.30-community"
_USER: Final = "neo4j"
_PASSWORD: Final = "guardmem123"  # pragma: allowlist secret
_BOLT_PORT: Final = 7687
_STARTUP_TIMEOUT_S: Final = 120.0


def _docker_available() -> bool:
    """Can we talk to a Docker daemon at all?"""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _wait_until_accepting(uri: str) -> None:
    """Block until the server answers an authenticated query, or give up.

    A completed `verify_connectivity` is the signal rather than a line in the
    log: Neo4j reports "Started" before authentication is usable, and the first
    query against that window fails with an `AuthError` that looks exactly like
    a wrong password.

    Raises:
        TimeoutError: the container never became usable.
    """

    async def _probe() -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        last: Exception | None = None
        while time.monotonic() < deadline:
            driver = AsyncGraphDatabase.driver(uri, auth=(_USER, _PASSWORD))
            try:
                await driver.verify_connectivity()
            except (neo4j_errors.Neo4jError, neo4j_errors.DriverError, OSError) as exc:
                last = exc
                await asyncio.sleep(1.0)
                continue
            finally:
                await driver.close()
            return
        raise TimeoutError(f"neo4j did not accept connections in {_STARTUP_TIMEOUT_S}s: {last}")

    asyncio.run(_probe())


@pytest.fixture(scope="session")
def neo4j_uri() -> Iterator[str]:
    """A bolt URI for a Neo4j with this repo's schema applied.

    Session-scoped: starting Neo4j takes tens of seconds - it installs the APOC
    plugin on first boot - and every test in this suite wants the same server.

    Synchronous on purpose, for the reason `postgres.py` gives. The one piece
    that needs a loop gets its own via `asyncio.run`.
    """
    external = os.environ.get("GM_TEST_NEO4J_URI")
    if external:
        yield external
        return
    if not _docker_available():
        pytest.skip("no Docker daemon; set GM_TEST_NEO4J_URI to use an existing Neo4j")

    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(_IMAGE)
        .with_env("NEO4J_AUTH", f"{_USER}/{_PASSWORD}")
        # No APOC. The dev stack installs it because later steps will want it;
        # nothing in `cypher.py` does, and the plugin download is most of a cold
        # container's startup time.
        .with_env("NEO4J_server_memory_pagecache_size", "128M")
        .with_exposed_ports(_BOLT_PORT)
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_BOLT_PORT)
        uri = f"bolt://{host}:{port}"
        _wait_until_accepting(uri)
        yield uri
    finally:
        container.stop()


@pytest.fixture
async def neo4j_driver(neo4j_uri: str) -> AsyncIterator[AsyncDriver]:
    """A connected driver with the schema applied, wiped before each test.

    The wipe is the part worth reading. `degree(entity)` and `neighbors(entity)`
    take no tenant, so they see every edge touching a node - which means one
    test's leftovers are the next test's wrong answer, and the failure would
    land on whichever test happened to run second. `DETACH DELETE` over the
    whole graph is crude and correct; the database exists for one test run.
    """
    driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(_USER, _PASSWORD))
    try:
        await ensure_schema(driver)
        await driver.execute_query("MATCH (n) DETACH DELETE n")
        yield driver
    finally:
        await driver.close()


@pytest.fixture
async def neo4j_store(neo4j_driver: AsyncDriver) -> AsyncIterator[object]:
    """A `Neo4jGraphStore` over a clean graph.

    Typed loosely on purpose: the contract suite holds it as a `GraphStore`, and
    naming the concrete class in the annotation would put a concrete store in
    the signature of something every backend shares.
    """
    from guardmem_core.memory.graph.neo4j_store import Neo4jGraphStore

    yield Neo4jGraphStore(neo4j_driver)
