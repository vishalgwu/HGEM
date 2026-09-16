"""S6.1's DONE WHEN, mechanised.  BUILD_NOTEBOOK.md S6.1

    npx @modelcontextprotocol/inspector uv run guardmem-mcp

"connects and lists zero tools without error". The inspector is a human driving
a browser, which is the right way to *see* it working and the wrong way to keep
it working - so this is the same claim asked by a real MCP client over a real
session, in CI, on every commit.

**S6.2 made it four tools rather than zero**, and this module deliberately kept
the rest: what it tests is that a client *connects* - `Settings` validated, the
DSN reachable, the ontology parsed, the handshake completed - which is the part
an operator gets wrong and the part no unit test can reach. What the tools then
do is `test_mcp_memory_tools.py`.

**Why this is an integration test.** The server's lifespan opens a Postgres pool
(`lifespan.py` explains why a tool-less server does), so "it connects" is a
statement about configuration as much as about the protocol: `Settings`
validated, `GM_DATABASE_URL` reachable, the ontology pack parsed. Every one of
those is what an operator gets wrong first, and none of them can be exercised
without a database. `tests/unit/test_mcp_server.py` holds everything that can be
asserted before the process starts.

**What is deliberately not driven here: a subprocess.** The transport is the
in-memory stream pair the SDK ships for exactly this, not `stdio_server` over a
spawned `guardmem-mcp`. The two differ in one thing only - whether the bytes go
through a pipe - and that one thing is the SDK's code rather than this repo's.
What a subprocess would add is a Windows/POSIX process-spawning difference in
the middle of a test about JSON-RPC, and CI runs on Linux while development is
on Windows (open item 9), so it would be the platform least likely to be
noticed. The `[project.scripts]` entry point is covered where it is a fact -
`test_the_console_script_is_installed_under_the_published_name`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from importlib.metadata import entry_points
from typing import Final

import mcp_types as types
import pytest
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams

from guardmem_core.settings import get_settings
from mcp_server.lifespan import DEFAULT_ONTOLOGY, lifespan
from mcp_server.server import SERVER_NAME, build_server

# Generous relative to the work, tight relative to a hang. Every request below
# is a round trip over an in-memory stream against a handler that returns a
# constant; what this bounds is a session that never initializes, which without
# it is a suite that stops rather than a suite that fails.
_SESSION_TIMEOUT_S: Final = 30.0


@pytest.fixture
def _server_env(app_role_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the server's lifespan at the testcontainer.

    Same shape as `test_replay_trace.py::_script_env`, and for the same reason:
    the process reads its DSN from `Settings` rather than taking one, which is
    right for a deployable and awkward for a test. Clearing the cache on the way
    in *and* out is what stops one test's DSN leaking into the next.
    """
    monkeypatch.setenv("GM_DATABASE_URL", app_role_dsn)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@asynccontextmanager
async def connected() -> AsyncIterator[ClientSession]:
    """An initialized MCP client, connected to a fully started server.

    A helper rather than a fixture, and that is not a style choice.
    `ClientSession.__aenter__` opens an anyio task group, and anyio refuses to
    close a cancel scope from a task other than the one that opened it - which
    is exactly what an async-generator *fixture* does, because pytest-asyncio
    may run setup and teardown in different tasks. The symptom is
    `RuntimeError: Attempted to exit cancel scope in a different task`, raised
    during teardown, after the assertions have already passed. Entering and
    leaving inside one test body keeps both halves in one task.

    The server runs as a task rather than being awaited: `Server.run` serves
    until the read side closes, so awaiting it would deadlock before the client
    ever sent `initialize`. Cancelling it on the way out is what runs the
    lifespan's `finally`, and therefore what closes the pool - leaving the task
    running would leak a pool per test and exhaust `max_connections` somewhere
    in the middle of the suite, with the failure landing on whichever test
    happened to be running then.
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
                await asyncio.wait_for(client.initialize(), timeout=_SESSION_TIMEOUT_S)
                yield client
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.usefixtures("_server_env")
class TestTheDoneWhen:
    """ "connects and lists zero tools without error"."""

    async def test_a_client_connects(self) -> None:
        """The whole startup path, end to end: settings, pool, ontology, handshake.

        `initialize` returning at all is the assertion - it is the first thing
        that fails when `GM_DATABASE_URL` is wrong, because the lifespan runs
        before the first response is written.
        """
        async with connected() as session:
            initialized = await session.initialize()

            assert initialized.server_info.name == SERVER_NAME

    async def test_it_lists_its_tools_without_error(self) -> None:
        """S6.1's sentence was "lists zero tools"; S6.2 put four there.

        What survives from S6.1 - and is the half this module is for - is
        "without error": the whole startup path ran, the handler answered over
        the wire, and the listing is what a client will actually see. The tools'
        *contents* belong to `test_mcp_memory_tools.py`, which asks them to do
        something.
        """
        async with connected() as session:
            result = await asyncio.wait_for(session.list_tools(), timeout=_SESSION_TIMEOUT_S)

            assert [tool.name for tool in result.tools] == [
                "memory.search",
                "memory.propose",
                "memory.commit",
                "memory.get_entity",
            ]

    async def test_listing_resources_and_prompts_also_succeeds(self) -> None:
        """Every advertised capability must answer.

        A capability advertised over a method that errors is worse than an
        unadvertised one: the client has already decided the server supports it,
        so the failure surfaces as a broken server rather than as a missing
        feature.

        S6.4 filled all three lists. What is asserted is the two that are a fact
        about the *server* - three templates and four prompts. `resources/list`
        is a fact about its *configuration*: it is keyed by
        `GM_MCP_DEFAULT_NAMESPACE`, which `_server_env` does not set, so its
        length depends on whether a developer's `.env` happens to supply one.
        Asserting it were empty here would pass in CI and fail on the machine
        this was written on, which is the exact CI-only divergence
        `test_dependency_consistency.py` exists because of. That it *answers* is
        the claim this test is making.
        """
        async with connected() as session:
            resources = await asyncio.wait_for(session.list_resources(), timeout=_SESSION_TIMEOUT_S)
            templates = await asyncio.wait_for(
                session.list_resource_templates(), timeout=_SESSION_TIMEOUT_S
            )
            prompts = await asyncio.wait_for(session.list_prompts(), timeout=_SESSION_TIMEOUT_S)

            assert resources.resources is not None
            assert len(templates.resource_templates) == 3
            assert len(prompts.prompts) == 4

    async def test_a_prompt_renders_the_pipeline_s_own_file(self) -> None:
        """§4's claim for `extract_memories` is that it is *the same prompt*.

        Over the protocol, because the canary has to survive `_meta` on the way
        out - it is the only thing a client can check an echo against, and a
        server that dropped it would still return a perfectly good prompt.
        """
        async with connected() as session:
            result = await asyncio.wait_for(
                session.get_prompt(
                    "guardmem/extract_memories", {"content": "Allergic to penicillin."}
                ),
                timeout=_SESSION_TIMEOUT_S,
            )

            canary = str((result.meta or {})["canary"])
            block = result.messages[0].content
            # Narrowed rather than cast: `PromptMessage.content` is a union of
            # five block types, and a prompt that started returning an image is
            # a failure this assertion should name rather than crash on.
            assert isinstance(block, types.TextContent)
            assert canary
            assert canary in block.text
            assert (result.meta or {})["prompt_version"] == "extract_memories@v1"

    async def test_a_prompt_that_cannot_be_served_fails_the_request(self) -> None:
        """A refusal rendered as a *message* would enter the transcript as
        though a model had said it, and a later reader could not tell it from
        content. Failing the request leaves the transcript clean."""
        async with connected() as session:
            with pytest.raises(MCPError):
                await asyncio.wait_for(
                    session.get_prompt("guardmem/review_brief", {"task_id": "rt_1"}),
                    timeout=_SESSION_TIMEOUT_S,
                )


class TestTheLifespanOwnsTheProcessResources:
    async def test_it_yields_a_state_carrying_a_usable_pool(self, _server_env: None) -> None:
        """The pool is real, not merely constructed.

        `create_pool` can return before any connection is attempted, so a test
        that only checked the object existed would pass against an unreachable
        database - which is the exact failure the lifespan exists to surface at
        startup.
        """
        async with lifespan() as state:
            async with state.pool.acquire() as connection:
                assert await connection.fetchval("SELECT 1") == 1

            assert state.ontology.predicates, "the clinical pack loaded"
            assert state.settings.database_url is not None

    async def test_it_closes_the_pool_on_the_way_out(self, _server_env: None) -> None:
        """A stdio client disconnecting is a normal end to a session.

        A pool that is dropped rather than closed leaves its connections for
        Postgres to reap on a timeout. One orphan per restart is invisible; a
        supervisor restarting a crash loop reaches `max_connections` and takes
        every other tenant down with it.
        """
        async with lifespan() as state:
            pool = state.pool

        assert pool.is_closing()

    async def test_it_closes_the_pool_even_when_the_body_raises(self, _server_env: None) -> None:
        """Which is the case that actually happens: a server dies mid-session,
        and the `finally` is the only thing between that and a leaked pool."""
        with pytest.raises(RuntimeError, match="deliberate"):
            async with lifespan() as state:
                pool = state.pool
                raise RuntimeError("deliberate: the session failed mid-flight")

        assert pool.is_closing()

    async def test_the_ontology_is_the_one_the_module_names(self, _server_env: None) -> None:
        """`DEFAULT_ONTOLOGY` is the only pack that ships, and the state's
        ontology has to be that one rather than whatever a later default drifts
        to - `PROJECT_TREE.md` names legal and fintech packs that do not exist."""
        async with lifespan() as state:
            assert state.ontology.name == DEFAULT_ONTOLOGY


def test_the_console_script_is_installed_under_the_published_name() -> None:
    """`guardmem-mcp`, which S6.1's DONE WHEN and `MCP_INTEGRATION.md` §1 both
    spell out - the first as `uv run guardmem-mcp`, the second as the `command`
    in a client's config. Renaming it is a breaking change for every installed
    client, so the name is asserted rather than assumed."""
    scripts = {script.name: script.value for script in entry_points(group="console_scripts")}

    assert scripts.get("guardmem-mcp") == "mcp_server.server:main"
