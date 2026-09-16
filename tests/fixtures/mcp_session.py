"""A live MCP session over the real server, for the integration suite.  S6.4

Extracted from `tests/integration/test_mcp_memory_tools.py` when S6.4's resource
tests pushed that module past `RULES.md` §2.4's 400-line cap. The cap's failure
message says "split it along a real seam rather than shaving it", and the seam
was here: the *tools* and the *resources* are different subjects asked of the
same session, and the session was the only thing they shared.

Registered as a plugin from `tests/conftest.py` rather than as a second
`conftest.py`, for the reason `fixtures/postgres.py` gives: two files named
`conftest` in a tree with no `__init__.py` are two modules with one name, and
`mypy` refuses the pair outright - so the suite `make typecheck` covers would
stop being checkable the moment one existed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Final

import mcp_types as types
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from guardmem_core.settings import get_settings
from mcp_server.server import build_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = [
    "SEEDED_PREDICATE",
    "TIMEOUT_S",
    "call",
    "connected",
    "demo_server_env",
    "seeded_namespace",
    "text_block",
]

# Generous relative to the work, tight relative to a hang. Every request is a
# round trip over an in-memory stream; what this bounds is a session that never
# initializes, which without it is a suite that stops rather than one that fails.
TIMEOUT_S: Final = 30.0

# A predicate `scripts/demo_tenant_data.py` is known to write. Used to find the
# seeded namespace by reading it back rather than hard-coding it twice.
SEEDED_PREDICATE: Final = "allergy"


@pytest.fixture
def demo_server_env(
    app_role_dsn: str, demo: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Configure the server for the seeded demo tenant, and yield its id.

    The tenant id comes from the database rather than from a constant: the seed
    derives it as a `uuid5` of its own slug, and a test that recomputed that
    derivation would be a second implementation of it - free to drift, and
    silently, since a wrong tenant reads as an empty namespace rather than an
    error.

    **`GM_MCP_DEFAULT_NAMESPACE` is pinned empty on purpose.** It is not needed -
    every call and every URI below names its namespace - and a developer's `.env`
    supplies one while CI has no `.env` at all, so anything asserting on
    `resources/list` would pass on one machine and fail on the other. That is the
    exact CI-only divergence `test_dependency_consistency.py` exists because of.
    """
    _connection, tenant = demo
    monkeypatch.setenv("GM_DATABASE_URL", app_role_dsn)
    monkeypatch.setenv("GM_MCP_TENANT_ID", tenant)
    monkeypatch.setenv("GM_MCP_DEFAULT_NAMESPACE", "")
    # Pin the provider rather than inheriting one. Ollama is the only adapter
    # that constructs without a credential, so it is the only choice that makes
    # `memory.propose` reach the *pipeline* deterministically - on a developer's
    # machine, on a runner, and with or without a key in `.env`. The port is
    # deliberately one nothing listens on: the call must fail at the model and
    # nowhere earlier, which is what proves every step before it was wired.
    monkeypatch.setenv("GM_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GM_OLLAMA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GM_OLLAMA_TIMEOUT_S", "5.0")
    get_settings.cache_clear()
    yield tenant
    get_settings.cache_clear()


@pytest.fixture
async def seeded_namespace(demo: tuple[Any, str]) -> str:
    """The namespace the seed wrote into, read back from it."""
    connection, _tenant = demo
    found = await connection.fetchval(
        "SELECT namespace FROM assertion WHERE predicate = $1 LIMIT 1", SEEDED_PREDICATE
    )
    assert found is not None, "the seed wrote no allergy; the fixture is out of step with it"
    return str(found)


@asynccontextmanager
async def connected() -> AsyncIterator[ClientSession]:
    """An initialized client over a fully started server.

    A helper entered inside each test body rather than a fixture, for the reason
    `test_mcp_stdio.py` spells out: `ClientSession` opens an anyio task group and
    anyio refuses to close a cancel scope from a different task, which is what an
    async-generator fixture can produce on teardown.

    The server runs as a task rather than being awaited: `Server.run` serves
    until the read side closes, so awaiting it would deadlock before the client
    ever sent `initialize`. Cancelling it on the way out is what runs the
    lifespan's unwind, and therefore what closes the pool.
    """
    server = build_server()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server_read, server_write = server_streams
        task = asyncio.create_task(
            server.run(
                server_read,
                server_write,
                server.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        client_read, client_write = client_streams
        try:
            async with ClientSession(client_read, client_write) as client:
                await asyncio.wait_for(client.initialize(), timeout=TIMEOUT_S)
                yield client
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    """One tool call, bounded."""
    return await asyncio.wait_for(session.call_tool(name, arguments), timeout=TIMEOUT_S)


def text_block(result: types.ReadResourceResult) -> types.TextResourceContents:
    """The single text block a resource read returns.

    Narrowed rather than cast: `ReadResourceResult.contents` is a union with
    `BlobResourceContents`, and a reader that started returning a blob is a
    failure this should name rather than crash on. All three of S6.4's resources
    are text by `MCP_INTEGRATION.md` §3's table.
    """
    assert result.contents, "a read must return at least one block"
    block = result.contents[0]
    assert isinstance(block, types.TextResourceContents)
    return block
