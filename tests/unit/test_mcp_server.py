"""The MCP server's protocol surface, without starting a process.  S6.1

S6.1's DONE WHEN is an inspector session, which needs a database and a
subprocess, so it is mechanised in `tests/integration/test_mcp_stdio.py`. What
is here is everything that can be asserted about the server *before* it is run,
and the split is worth stating because it is the reason `build_server` and
`main` are separate functions: constructing the server reads no environment,
opens no socket and allocates nothing, so the handshake it would send is a pure
function of the code.

Three things are pinned:

- **Zero tools.** The step's own acceptance criterion, and the one that changes
  at S6.2, where this file's expectations should be updated rather than deleted.
- **The capability block.** Derived by the SDK from which handlers exist, so it
  is the one place a handler silently going missing shows up. An MCP client
  reads it once, at initialize, and never asks again - a server that stops
  advertising `prompts` mid-build does not fail, it becomes invisible.
- **`resources.subscribe` is absent**, which is correction 1 on S6.1 in
  `server.py`. A test rather than a comment, because the comment would be
  removed by whoever later adds a `subscribe` handler for a different reason,
  and the point is that the capability must arrive with a change feed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import mcp_types as types
import pytest
from pydantic import ValidationError

from guardmem_core.settings import get_settings
from mcp_server.lifespan import ConfigurationError, preflight
from mcp_server.server import (
    EXIT_CONFIG,
    INSTRUCTIONS,
    SERVER_NAME,
    SERVER_VERSION,
    _list_prompts,
    _list_resources,
    _list_tools,
    build_server,
    main,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from mcp.server.context import ServerRequestContext

    from mcp_server.lifespan import ServerState


# The three handlers take a request context and ignore it - nothing in an empty
# listing depends on who asked. Building a real one needs a live `ServerSession`,
# which needs a transport, which is the integration test's job; casting `None`
# is the honest way to say "this argument is unused" in a test that is about the
# return value. If a handler ever starts reading its context this cast fails
# with an `AttributeError`, which is a better failure than a mock that answers.
_NO_CONTEXT = cast("ServerRequestContext[ServerState]", None)


@pytest.fixture
def capabilities() -> types.ServerCapabilities:
    """What the server advertises at initialize, derived as the SDK derives it."""
    return build_server().create_initialization_options().capabilities


class TestTheDoneWhen:
    """S6.1 was "...connects and lists zero tools without error".

    **S6.2 changed the first half and that is the point of this class now.**
    The file that shipped at S6.1 said this was "the one that changes at S6.2,
    where this file's expectations should be updated rather than deleted", so:
    the tools are asserted here as a *count*, and their contents belong to
    `test_mcp_tools.py`. What has not changed - and is the half worth keeping -
    is "without error": listing still answers, resources and prompts still list
    empty, and the capability block still says what the server can honour.
    """

    async def test_it_lists_the_four_core_tools(self) -> None:
        result = await _list_tools(_NO_CONTEXT, None)

        assert [tool.name for tool in result.tools] == [
            "memory.search",
            "memory.propose",
            "memory.commit",
            "memory.get_entity",
        ]

    async def test_it_offers_no_next_page_over_a_complete_listing(self) -> None:
        """A cursor over a finished list is an invitation to ask again for
        nothing. Four tools is one page."""
        result = await _list_tools(_NO_CONTEXT, None)

        assert result.next_cursor is None

    async def test_it_lists_zero_resources(self) -> None:
        """`MCP_INTEGRATION.md` §3's four arrive at S6.4."""
        result = await _list_resources(_NO_CONTEXT, None)

        assert result.resources == []

    async def test_it_lists_zero_prompts(self) -> None:
        """`MCP_INTEGRATION.md` §4's four arrive at S6.4."""
        result = await _list_prompts(_NO_CONTEXT, None)

        assert result.prompts == []


class TestWhatTheHandshakeAdvertises:
    def test_tools_resources_and_prompts_are_all_advertised(
        self, capabilities: types.ServerCapabilities
    ) -> None:
        """S6.1: "advertise capabilities: tools, resources, prompts".

        Each is present because its list handler is registered. Drop one handler
        and the corresponding capability becomes `None` - which a client reads as
        "this server does not do that", silently and permanently for the session.
        """
        assert capabilities.tools is not None
        assert capabilities.resources is not None
        assert capabilities.prompts is not None

    def test_resources_subscribe_is_not_advertised(
        self, capabilities: types.ServerCapabilities
    ) -> None:
        """Correction 1 on S6.1 - see `server.py`.

        `subscribe` promises `notifications/resources/updated`, and nothing in
        this system can send one: no resource exists until S6.4, and no write
        path exists to change the state behind it. A client that subscribed
        would wait for an event that is never coming, and could not tell that
        from "nothing has changed yet".
        """
        assert capabilities.resources is not None
        assert capabilities.resources.subscribe is False

    def test_nothing_claims_a_change_notification_it_cannot_send(
        self, capabilities: types.ServerCapabilities
    ) -> None:
        """The same argument as `subscribe`, for the three `listChanged` flags.

        Zero tools is a *fixed* zero at this step - the list changes when a new
        build ships, not while a session is open - so a client re-listing on
        notification would be doing it for nothing.
        """
        assert capabilities.tools is not None
        assert capabilities.prompts is not None
        assert capabilities.resources is not None
        assert capabilities.tools.list_changed is False
        assert capabilities.prompts.list_changed is False
        assert capabilities.resources.list_changed is False

    def test_no_capability_is_claimed_for_a_method_nothing_serves(
        self, capabilities: types.ServerCapabilities
    ) -> None:
        """Logging and completion have no handlers, so neither is advertised."""
        assert capabilities.logging is None
        assert capabilities.completions is None


class TestServerIdentity:
    def test_it_reports_a_name_and_a_version(self) -> None:
        """The SDK substitutes nothing for an omitted version - it reports an
        empty string - and a client that cannot tell two builds apart cannot
        report a bug against one."""
        options = build_server().create_initialization_options()

        assert options.server_name == SERVER_NAME == "guardmem"
        assert options.server_version == SERVER_VERSION
        assert options.server_version, "an unversioned server is unreportable"

    def test_the_instructions_say_what_is_not_there_yet(self) -> None:
        """Read by the model, not only rendered for the human.

        An agent whose `memory.propose` call is going to be declined should be
        able to see why from the handshake, rather than concluding the server is
        broken and working around it - and, more importantly, should not treat
        anything as remembered on the strength of a tool that exists.
        """
        options = build_server().create_initialization_options()

        assert options.instructions == INSTRUCTIONS
        assert "memory.search" in INSTRUCTIONS
        assert "decline" in INSTRUCTIONS
        assert "Treat nothing as remembered" in INSTRUCTIONS


class TestConstructionIsPure:
    def test_building_a_server_reads_no_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No `Settings`, no pool, no ontology parse until the lifespan runs.

        This is what lets every test above run without a database, and it is
        also the property that keeps a configuration error at *startup* rather
        than at import - `main` is where the environment is read, and a misread
        there fails a process rather than a module.
        """
        monkeypatch.delenv("GM_DATABASE_URL", raising=False)

        assert build_server() is not None

    def test_two_servers_are_independent(self) -> None:
        """`build_server` is a factory rather than a module-level singleton.

        A shared instance would carry one lifespan's state into the next
        session, which for a stdio server - one process, one client - hides the
        bug until the first streamable-HTTP deployment serves two.
        """
        assert build_server() is not build_server()


class TestPreflightRefusesABadEnvironmentBeforeTheTransportOpens:
    """What a misconfigured launch looks like, which for a stdio server is the
    failure most users will actually meet.

    Measured before this existed: running the console script from a directory
    with no `.env` produced **78 lines** of `BaseExceptionGroup`, anyio and
    contextlib frames with `9 validation errors for Settings` in the middle, exit
    code 1, and the transport already open - so the client saw a pipe that
    accepted a connection and died mid-handshake. It is now one line and exit
    code 2, and the pipe is never opened.

    An MCP client reports all of that identically ("server disconnected") and
    buries the log, so the difference between the two is the difference between a
    user fixing their config and a user filing a bug.
    """

    @pytest.fixture
    def _no_configuration(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """A process launched somewhere with no `.env` and no `GM_` variables.

        `chdir` because that is the real mechanism: `Settings` resolves `.env`
        against the working directory, and a client chooses that directory -
        Claude Desktop does not use the repository. `delenv` over every `GM_` key
        because a developer with them exported would otherwise see this test fail
        for a reason that has nothing to do with the code.
        """
        monkeypatch.chdir(tmp_path)
        for key in [name for name in os.environ if name.startswith("GM_")]:
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_it_names_every_field_that_is_missing(self, _no_configuration: None) -> None:
        with pytest.raises(ConfigurationError) as caught:
            preflight()

        message = str(caught.value)
        assert "database_url" in message
        assert "neo4j_uri" in message
        assert "model_fast" in message

    def test_it_names_the_directory_it_looked_in(
        self, _no_configuration: None, tmp_path: Path
    ) -> None:
        """The part that actually diagnoses it. "9 validation errors" sends
        someone to check their variables; naming a directory they did not expect
        tells them what really happened."""
        with pytest.raises(ConfigurationError) as caught:
            preflight()

        message = str(caught.value)
        assert str(tmp_path) in message
        assert "no .env there" in message

    def test_it_explains_that_a_spawned_server_inherits_almost_nothing(
        self, _no_configuration: None
    ) -> None:
        """The second-order cause, and the one nobody guesses.

        The MCP SDK spawns a server with `get_default_environment()`, which
        returns only `DEFAULT_INHERITED_ENV_VARS` - so `GM_DATABASE_URL` exported
        in a shell does **not** reach a server launched by a client. Someone who
        exported it and watched the server fail has no way to reach that fact
        from a pydantic error.
        """
        with pytest.raises(ConfigurationError) as caught:
            preflight()

        assert "`env` block" in str(caught.value)

    def test_it_keeps_the_pydantic_error_as_the_cause(self, _no_configuration: None) -> None:
        """Replaced for the human, preserved for the debugger. The message is
        what a user reads; `__cause__` is what a bug report should carry."""
        with pytest.raises(ConfigurationError) as caught:
            preflight()

        assert isinstance(caught.value.__cause__, ValidationError)

    def test_main_exits_with_the_configuration_code_and_never_serves(
        self, _no_configuration: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point: no transport is opened on a bad environment.

        `serve_stdio` is replaced with something that fails loudly rather than
        asserted about afterwards - a test that only checked the exit code would
        pass against a `main` that opened the pipe, failed, and returned 2 anyway.

        That `EXIT_CONFIG` and `EXIT_OK` differ is not asserted here: both are
        `Final` literals, so `mypy` rejects the comparison as non-overlapping -
        which is the guarantee, checked statically and for every caller rather
        than at runtime in one test.
        """

        def _must_not_run() -> None:  # pragma: no cover - the assertion is that this never runs
            raise AssertionError("main opened the transport despite a bad environment")

        monkeypatch.setattr("mcp_server.server.serve_stdio", _must_not_run)

        assert main() == EXIT_CONFIG


class TestPreflightAcceptsAGoodEnvironment:
    def test_it_returns_the_settings_the_lifespan_will_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One instance, not two reads.

        `get_settings` is `lru_cache`d, so the lifespan's own call a moment later
        returns this same object. If that ever stopped being true the server
        could validate one configuration and run on another.
        """
        monkeypatch.setenv("GM_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
        monkeypatch.setenv("GM_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("GM_NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("GM_NEO4J_USER", "neo4j")
        monkeypatch.setenv("GM_NEO4J_PASSWORD", "password")
        monkeypatch.setenv("GM_MODEL_FAST", "claude-haiku-4-5")
        monkeypatch.setenv("GM_MODEL_BALANCED", "claude-sonnet-5")
        monkeypatch.setenv("GM_MODEL_FRONTIER", "claude-opus-5")
        monkeypatch.setenv("GM_EMBED_MODEL", "text-embedding-3-large")
        get_settings.cache_clear()
        try:
            assert preflight() is get_settings()
        finally:
            get_settings.cache_clear()
