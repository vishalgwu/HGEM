"""The MCP protocol surface, and the process entry point.  BUILD_NOTEBOOK.md S6.1

S6.1's DONE WHEN, verbatim: `npx @modelcontextprotocol/inspector uv run
guardmem-mcp` "connects and lists zero tools without error". That sentence is
the specification for this module, and both halves of it are load-bearing.

**S6.2 replaced the empty tool list with the four core tools**, and **S6.4
replaced the empty resource and prompt lists.** All three are
`MCP_INTEGRATION.md` copied exactly - §2.1-§2.4 into `tools/schemas.py`, §3 into
`resources/`, §4 into `prompts/` - because a description on any of them is read
by a model rather than by a person, and changing one changes behaviour.

**The same split runs through all three surfaces: build what exists, decline the
rest by name.** `memory.search` and `memory.get_entity` are complete;
`memory.propose` and `memory.commit` govern a real write now but still name what
they cannot do. Three of §3's seven resources are served. Two of §4's four
prompts are served and two refuse, because there is no review queue (S18.1) and
nothing measures drift (S20.x). Each refusal names the step it waits on, which
is the difference between a gap and a mystery.

**1 correction to this step, found by building it.**

1. *`resources.subscribe` is NOT advertised, against the step's instruction.*
   S6.1 says "advertise capabilities: tools, resources, prompts, and
   `resources.subscribe`". The first three are claims about what can be listed
   and are true the moment a handler exists. The fourth is a claim about what
   the server will *send*: a client that subscribes to a resource is promised
   `notifications/resources/updated` when the underlying state changes, and
   nothing in this system can produce that notification. Advertising it would
   make every client wait forever for an event that is never coming, and
   "subscribed and silent" is indistinguishable from "nothing has changed" - a
   failure with no symptom, which is the class of bug this repository spends its
   comments on.

   **S6.4 was named as its home and is not.** That note said the natural place
   was "the step that has both a resource and a change feed, and the natural
   home is S6.4 alongside `guardmem://memory/{namespace}`." Half of it arrived:
   the resource exists and the snapshot it renders does change, because
   ADR-0010's applier writes and the relay makes rows visible. The *feed* did
   not. Nothing tells a live session that a write happened - there is no bus, no
   `LISTEN/NOTIFY`, and on stdio no second process to hear one - so the
   capability would still be a promise this server cannot keep. It moves to
   **S8.4**, the worker, which is the first component that watches writes rather
   than performing them. `capabilities.resources.subscribe` is `false` in the
   handshake today, and that is checked rather than assumed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
from typing import TYPE_CHECKING, Final

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mcp_server.lifespan import ConfigurationError, ServerState, lifespan, preflight
from mcp_server.prompts import PROMPTS, get_prompt
from mcp_server.resources import RESOURCE_TEMPLATES, list_resources, read_resource
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
    ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListResourcesResult:
    """The resources this server can name from configuration alone.

    Returns:
        The two keyed by `GM_MCP_DEFAULT_NAMESPACE`, or nothing when none is
        set. `resources/list` answers "what can I attach right now"; the
        variable ones are answered by `resources/templates/list` below.
    """
    return types.ListResourcesResult(resources=list_resources(ctx.lifespan_context))


async def _list_resource_templates(
    _ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListResourceTemplatesResult:
    """The URI shapes a client may construct.

    Returns:
        S6.4's three templates. `MCP_INTEGRATION.md` §3: "Templates are
        advertised via `resources/templates/list` so clients can construct URIs
        for namespaces they discover at runtime" - which is the only way
        `guardmem://audit/{trace_id}` is reachable at all, since a trace id is
        minted per proposal and cannot be listed in advance.
    """
    return types.ListResourceTemplatesResult(resource_templates=list(RESOURCE_TEMPLATES))


async def _read_resource(
    ctx: ServerRequestContext[ServerState],
    params: types.ReadResourceRequestParams,
) -> types.ReadResourceResult:
    """Read one resource.

    Raises:
        ToolRefusedError: the URI names nothing this server serves, or the
            reader declines. Deliberately **not** converted into a successful
            empty body - `resources/__init__.py` has the argument, and the short
            version is that a person attaching a resource has no model in the
            loop to interpret a refusal, so "could not load" must not render as
            "nothing is known".
    """
    return await read_resource(ctx.lifespan_context, str(params.uri))


async def _list_prompts(
    _ctx: ServerRequestContext[ServerState],
    _params: types.PaginatedRequestParams | None,
) -> types.ListPromptsResult:
    """The four prompts of `MCP_INTEGRATION.md` §4.

    Note which prompts these are *not*. `guardmem_core.prompts` holds the
    versioned files the pipeline sends to a model; §4's are the ones this server
    offers to a *client*, for a human to invoke. Same word, opposite direction,
    and the only overlap is that both are versioned - which is exactly the
    overlap the two served ones exist to exploit.

    Two of the four decline when invoked, and their titles say so, because a
    client's menu is the last place a person looks before picking one.
    """
    return types.ListPromptsResult(prompts=list(PROMPTS))


async def _get_prompt(
    ctx: ServerRequestContext[ServerState],
    params: types.GetPromptRequestParams,
) -> types.GetPromptResult:
    """Render one prompt.

    Raises:
        ToolRefusedError: unknown name, missing argument, or one of the two
            prompts §4 publishes that this build cannot serve.
    """
    return get_prompt(ctx.lifespan_context, params.name, params.arguments)


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
        on_list_resource_templates=_list_resource_templates,
        on_read_resource=_read_resource,
        on_list_prompts=_list_prompts,
        on_get_prompt=_get_prompt,
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
    # stderr is forced to UTF-8 before anything writes to it. Python picks the
    # console encoding otherwise, which on Windows is cp1252, and this server's
    # diagnostics are full of `§` - thirty-nine section references across the
    # refusal messages alone, because every one of them cites the clause it is
    # enforcing. Under cp1252 each becomes a replacement character, so the log a
    # Claude Desktop user is told to read ("MCP_INTEGRATION.md ?2.1") points at
    # nothing. The client sees the text correctly either way; it is the *log*
    # that loses it, which is the copy an operator actually has.
    #
    # `errors="replace"` rather than the default: a log write that raises
    # `UnicodeEncodeError` inside a logging handler is swallowed and the line is
    # simply lost, which is a worse failure than a mangled character. Same
    # reasoning as the Makefile's `PYTHONIOENCODING := utf-8`, which exists
    # because `lint-imports` hit this on its spinner.
    #
    # Guarded by an `isinstance` rather than a `hasattr`, and not only to satisfy
    # `mypy`: `sys.stderr` is typed `TextIO`, which has no `reconfigure`, and at
    # runtime it is whatever the host put there. Under `pytest`'s capture it is
    # not a `TextIOWrapper` at all, so an unguarded call fails the one place this
    # function is easiest to test.
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
