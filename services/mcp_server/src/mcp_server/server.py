"""The MCP protocol surface, and the process entry point.  BUILD_NOTEBOOK.md S6.1

S6.1's DONE WHEN, verbatim: `npx @modelcontextprotocol/inspector uv run
guardmem-mcp` "connects and lists zero tools without error". That sentence is
the specification for this module, and both halves of it are load-bearing.

**S6.2 replaced the empty tool list with the four core tools.** They are
`MCP_INTEGRATION.md` §2.1-§2.4, copied exactly - see `tools/schemas.py`, which
owns the wording, because a tool description is read by a model rather than by a
person and changing one changes behaviour. Resources and prompts still list
empty: they are S6.4, and a list handler that exists returning nothing is what
makes the capability advertised and honest at the same time.

**Two of the four do work and two refuse, on purpose.** `memory.search` and
`memory.get_entity` read governed memory and are complete. `memory.propose` and
`memory.commit` validate everything they can and then decline, naming each
missing dependency - `tools/pipeline.py` has the list and the argument for why a
plausible-looking invented decision would be the worst thing this repository
could ship.

**1 correction to this step, found by building it.**

1. *`resources.subscribe` is NOT advertised, against the step's instruction.*
   S6.1 says "advertise capabilities: tools, resources, prompts, and
   `resources.subscribe`". The first three are claims about what can be listed
   and are true the moment a handler exists. The fourth is a claim about what
   the server will *send*: a client that subscribes to a resource is promised
   `notifications/resources/updated` when the underlying state changes, and
   nothing in this system can produce that notification. The write path does not
   exist (`pipeline/orchestrator.py` returns decisions and applies none), so
   there is no change to observe, and no resource exists to subscribe to until
   S6.4. Advertising it would make every client wait forever for an event that
   is never coming, and "subscribed and silent" is indistinguishable from
   "nothing has changed" - a failure with no symptom, which is the class of bug
   this repository spends its comments on. It belongs at the step that has both
   a resource and a change feed, and the natural home is S6.4 alongside
   `guardmem://memory/{namespace}`.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Final

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mcp_server.lifespan import ConfigurationError, ServerState, lifespan, preflight
from mcp_server.tools import TOOLS, call_tool

if TYPE_CHECKING:
    from mcp.server.context import ServerRequestContext

__all__ = ["EXIT_CONFIG", "EXIT_OK", "SERVER_NAME", "build_server", "main"]

_LOGGER: Final = logging.getLogger(__name__)

# A session that ran and ended, including one the client interrupted.
EXIT_OK: Final = 0

# The environment is wrong. Distinct from a crash because the correct response
# differs: a crashed server is worth restarting and a misconfigured one is not,
# and a supervisor that cannot tell them apart restarts this one forever.
EXIT_CONFIG: Final = 2

# The name a client sees in its server list, and the one `MCP_INTEGRATION.md` §1
# uses as the `mcpServers` key. Not the package name (`guardmem-mcp`), which is
# a distribution; this is the product.
SERVER_NAME: Final = "guardmem"

# Reported in `serverInfo` and pinned to the distribution's own version. The SDK
# substitutes nothing when this is omitted - it reports an empty string - and a
# client that cannot tell two builds apart cannot report a bug against one.
SERVER_VERSION: Final = "0.1.0"

# Shown by clients that render it, and read by models that do not. Deliberately
# says what the server does *not* do yet: an agent told it has governed memory
# and offered no tools should be able to see why from the handshake alone.
INSTRUCTIONS: Final = (
    "GuardMem AI governs what an agent is allowed to remember: every candidate "
    "fact is extracted with a verbatim source span, checked against what is "
    "already believed, scored for confidence and blast radius, and either "
    "written, queued for human review, or rejected - with an audit record "
    "either way. Call memory.search before answering anything that depends on "
    "facts about a subject; it returns only currently-believed assertions, each "
    "with the source span it came from, and lists separately what has been "
    "retired. In this build memory.propose and memory.commit decline: the "
    "decision pipeline needs a model provider that is not wired yet, and they "
    "will not invent a decision to look complete. Treat nothing as remembered "
    "until a write path exists."
)


async def _list_tools(
    _ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    """The four core tools, in S6.2's implementation order.

    Returns:
        Every published tool. `nextCursor` is left unset - four tools is one
        page, and a cursor over a complete list is a client's invitation to ask
        again for nothing.
    """
    return types.ListToolsResult(tools=list(TOOLS))


async def _call_tool(
    ctx: ServerRequestContext[ServerState],
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    """Run one tool.

    Returns:
        The result, or a refusal carrying `isError` - `tools/__init__.py` owns
        that distinction and the reasoning behind it.

    The lifespan context is where the pool, the graph, the embedder and the
    ontology come from, which is why the handler takes `ctx` rather than
    reaching for a module-level singleton: two sessions in one process must not
    be able to see each other's state, and on stdio today there is one session
    only *by accident of the transport*.
    """
    return await call_tool(ctx.lifespan_context, params.name, params.arguments)


async def _list_resources(
    _ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListResourcesResult:
    """No resources yet; the four in `MCP_INTEGRATION.md` §3 arrive at S6.4."""
    return types.ListResourcesResult(resources=[])


async def _list_prompts(
    _ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListPromptsResult:
    """No prompts yet; the four in `MCP_INTEGRATION.md` §4 arrive at S6.4.

    Note which prompts these are *not*. `guardmem_core.prompts` holds the
    versioned files the pipeline sends to a model; §4's are the ones this server
    offers to a *client*, for a human to invoke. Same word, opposite direction,
    and the only overlap is that both are versioned.
    """
    return types.ListPromptsResult(prompts=[])


def build_server() -> Server[ServerState]:
    """Construct the server with its handlers and its lifespan bound.

    Returns:
        A `Server` ready for any transport. Constructing it is pure - no socket,
        no pool, no environment read - which is what lets a test assert on the
        advertised capabilities without a database, and is why this is separate
        from `main`.

    Handlers are passed to the constructor rather than registered with
    decorators. Both are supported by the SDK; this form keeps the set of served
    methods visible as one list, and a method that is served is a capability
    that is advertised, so the list and the handshake cannot drift.
    """
    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        lifespan=lambda _server: lifespan(),
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_list_resources=_list_resources,
        on_list_prompts=_list_prompts,
    )


async def serve_stdio() -> None:
    """Serve one stdio session until the client closes it.

    Raises:
        Anything the lifespan raises - see `lifespan`. A startup failure must
        reach the client's log rather than be swallowed into a server that
        connects and then answers nothing.

    stdio is the transport Claude Desktop, Cursor and the inspector all use
    (`MCP_INTEGRATION.md` §1), and it has one hard rule this module obeys by
    construction: **stdout belongs to the protocol.** A stray `print` is a
    malformed JSON-RPC frame. That is why `main` sends logging to stderr, and
    why nothing in this package writes to stdout at all - enforced rather than
    intended, since ruff's `T201` covers `services/` and fails `make lint` on a
    `print` anywhere in this package.
    """
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    """The `guardmem-mcp` console script.

    Returns:
        `EXIT_OK` when a session ended normally or was interrupted, and
        `EXIT_CONFIG` when the environment is wrong. Two codes rather than a
        bool, for the reason `scripts/checkpoint_b.py` gives about its three: a
        supervisor restarting a crashed server and a supervisor restarting a
        *misconfigured* one are different behaviours, and the second one is a
        loop that never converges.

    Synchronous because a `[project.scripts]` entry point is called as a plain
    function, and it owns the event loop for the same reason `lifespan` owns the
    pool: one process, one loop, created at the top and not by a library.

    **Configuration is validated before the transport is opened.** `preflight`
    explains why at length; in short, a `Settings` failure inside the lifespan
    escapes through anyio as a 78-line `BaseExceptionGroup` with the cause buried
    in the middle, after the pipe has already been accepted. Asking first turns
    that into one line and a clean exit.

    `KeyboardInterrupt` exits quietly, and as a success. A client shutting the
    server down is the normal end of a session, and a traceback on a normal exit
    trains whoever reads the log to ignore tracebacks.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        preflight()
    except ConfigurationError as exc:
        # `exception()` would attach the pydantic traceback, which is the
        # 78-line wall this exists to replace. The chained cause is still on the
        # exception for anything that wants it.
        _LOGGER.error("guardmem-mcp cannot start: %s", exc)
        return EXIT_CONFIG
    try:
        asyncio.run(serve_stdio())
    except KeyboardInterrupt:  # pragma: no cover - needs a signal, not a test.
        _LOGGER.info("guardmem-mcp interrupted")
    return EXIT_OK
