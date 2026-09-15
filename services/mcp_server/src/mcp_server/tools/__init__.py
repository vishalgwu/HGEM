"""The tool surface: what is served, and how a failure reaches the caller.  S6.2

`schemas.py` says what the tools *are* - copied from `MCP_INTEGRATION.md` §2.1
to §2.4 - and the four modules beside it say what they *do*. This one joins
them and owns the single decision they share: what a client sees when a call
fails.

**A failed tool call is a result, not a protocol error.** MCP distinguishes the
two, and the distinction matters for the caller a tool actually has. A JSON-RPC
error means "this request was malformed" and a client surfaces it to its
developer; `CallToolResult(isError=True)` means "the tool ran and could not do
it", and the model reading the conversation sees the text and can act on it -
narrow the query, pass the namespace, stop asking. Every refusal here is the
second kind, because every refusal here is something an agent could plausibly
respond to.

**Nothing reaches the client as a bare protocol error, and finding out why
cost a debugging session.** The SDK turns an uncaught handler exception into
JSON-RPC `-32602`, whose message is "Invalid request parameters" - so a server
bug (a zero query vector producing a `NaN` cosine, in the case that prompted
this) was reported to the caller as *their* mistake, with no detail, while the
real `ValidationError` was visible only by calling the handler directly. Every
exception is therefore turned into a result here:

- a `GuardMemError` carries its own `code` and `retryable`, and both are
  reported. `StoreUnavailable` is the one that matters in practice, and telling
  an agent "memory is temporarily unreachable, this is retryable" is strictly
  more useful than a protocol error it cannot interpret.
- anything else is a bug in this server. The caller gets a short, safe message
  and the traceback goes to the log, where `RULES.md` §6 wants it - not into a
  tool result an agent might repeat to a user.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

import mcp_types as types

from guardmem_core.errors import GuardMemError
from mcp_server.tools.context import ToolRefusedError, context_for
from mcp_server.tools.get_entity import run_get_entity
from mcp_server.tools.pipeline import run_commit, run_propose
from mcp_server.tools.schemas import TOOLS, TOOLS_BY_NAME
from mcp_server.tools.search import run_search

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server.lifespan import ServerState
    from mcp_server.tools.context import ToolContext

__all__ = ["TOOLS", "TOOLS_BY_NAME", "call_tool"]

_LOGGER: Final = logging.getLogger(__name__)

type _Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]

# Name to handler. Keyed off `schemas.TOOLS_BY_NAME` by
# `test_every_published_tool_has_a_handler`, so a tool published in the schema
# module and never wired here - which would list in a client and then fail as
# "unknown tool" - is a test failure rather than a support ticket.
_HANDLERS: Final[dict[str, _Handler]] = {
    "memory.search": run_search,
    "memory.propose": run_propose,
    "memory.commit": run_commit,
    "memory.get_entity": run_get_entity,
}


async def call_tool(
    state: ServerState, name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    """Dispatch one `tools/call`.

    Args:
        state: The process's resources.
        name: The tool the client asked for.
        arguments: Its arguments; `None` is treated as `{}`, which is what a
            client sends for a tool it believes takes none.

    Returns:
        The result, carrying both a text block and `structuredContent`. Both,
        rather than one: `structuredContent` is what §2.1's result shape is for
        and what a program reads, while the text block is what a model reads -
        and a client that renders only content would otherwise show an agent an
        empty response to a successful call.

    An unknown `name` is a refusal rather than an exception for the same reason
    the others are: a model that hallucinated a tool name can read the list of
    real ones and try again.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return _refusal(
            f"unknown tool {name!r}. This server serves: {', '.join(sorted(_HANDLERS))}."
        )
    try:
        context = context_for(state, arguments or {})
        payload = await handler(context, arguments or {})
    except ToolRefusedError as exc:
        return _refusal(str(exc))
    except GuardMemError as exc:
        _LOGGER.warning("%s failed: %s", name, exc, exc_info=True)
        retry = " This is retryable." if exc.retryable else ""
        return _refusal(f"{name} failed [{exc.code}]: {exc.msg}{retry}")
    except Exception:
        # A bug in this server, not in the call. The message says so and says
        # nothing else - an exception string can carry a DSN, a row, or a span of
        # untrusted source text, and `RULES.md` §1.5 keeps all three out of
        # anything a caller sees.
        _LOGGER.exception("%s raised an unexpected error", name)
        return _refusal(
            f"{name} failed with an internal error in the GuardMem server. The "
            "details are in the server log; this is a bug rather than a problem "
            "with the call."
        )
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_summarise(name, payload))],
        structuredContent=payload,
    )


def _refusal(message: str) -> types.CallToolResult:
    """A tool that ran and declined, in the form a model can act on."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        isError=True,
    )


def _summarise(name: str, payload: dict[str, Any]) -> str:
    """One line of text describing a successful call.

    Returns:
        A summary for the model. Deliberately not a JSON dump of `payload` -
        that is already on the result as `structuredContent`, and repeating it
        doubles the tokens every tool call costs an agent for no added
        information.

    The counts chosen are the ones that change what an agent does next: how many
    facts it got, and - for a search - how many were *retired*, which is §2.1's
    whole reason for publishing `excluded` and the difference between "no record"
    and "not any more".
    """
    if name == "memory.search":
        found = len(payload.get("assertions", []))
        excluded = len(payload.get("excluded", []))
        tail = f", {excluded} retired and excluded" if excluded else ""
        return f"{found} believed assertion(s){tail}. Provenance is on each."
    if name == "memory.get_entity":
        grouped = payload.get("assertions", {})
        facts = sum(len(items) for items in grouped.values())
        note = "" if payload.get("graph_backed") else " (graph is in-process and empty; see S7.1)"
        return (
            f"{facts} live assertion(s) across {len(grouped)} predicate(s), "
            f"{len(payload.get('neighbors', []))} neighbour(s){note}."
        )
    return f"{name} completed."
