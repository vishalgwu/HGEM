"""The resource surface: what can be attached, and how a URI is resolved.  S6.4

`MCP_INTEGRATION.md` §3 publishes seven URI templates; S6.4 builds the three the
notebook names - `guardmem://memory/{namespace}`, `guardmem://audit/{trace_id}`
and `guardmem://ontology/{namespace}`. The other four arrive with the thing they
describe: `timeline` and `policy` with §2.5 and the policy pack, `queue/pending`
with S18.1's review queue, and the entity card alongside `memory.get_entity`'s
graph half.

**A resource is not a tool, and the difference decides the error handling.**
`tools/__init__.py` turns every failure into `CallToolResult(isError=True)`,
because a tool's caller is a *model* that can read the refusal and try
something else. A resource is attached by a **person**, in a client's UI, and
there is no model in that loop to reason about a refusal - so a bad URI is a
protocol error, which is what a client renders as "this resource could not be
loaded". Inventing an empty-but-successful body instead would put "this
namespace has no memory" on a reader's screen when the truth was "you typed the
namespace wrong", and those must not look alike.

**Two of the three are exact and one is a snapshot.** `ontology` and `audit`
return what they name. `memory` returns *up to* `_SNAPSHOT_LIMIT` believed
assertions and says so in the body when it truncates, because `VectorStore` has
no "list everything" method - see `memory.py` for why that is the honest shape
rather than a missing feature.

**`resources.subscribe` is still not advertised, and S6.4 was supposed to be
where it arrived.** `server.py`'s S6.1 note says the natural home is "the step
that has both a resource and a change feed, and the natural home is S6.4
alongside `guardmem://memory/{namespace}`". Half of that is now true: the
resource exists. The change feed does not. A write reaches Postgres through
ADR-0010's applier and nothing tells a *session* about it - there is no bus, no
`LISTEN/NOTIFY`, and on stdio no second process to hear one. Advertising
`subscribe` would promise `notifications/resources/updated` that never arrive,
and "subscribed and silent" is indistinguishable from "nothing has changed",
which is the failure-with-no-symptom that note exists to refuse. It belongs with
the worker at S8.4, which is the first thing in this system that watches writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlsplit

import mcp_types as types

from mcp_server.resources.audit import AUDIT_TEMPLATE, read_audit
from mcp_server.resources.memory import MEMORY_TEMPLATE, read_memory
from mcp_server.resources.ontology import ONTOLOGY_TEMPLATE, read_ontology
from mcp_server.resources.uris import SCHEME, uri_for
from mcp_server.tools.context import ToolRefusedError, context_for

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_server.lifespan import ServerState
    from mcp_server.tools.context import ToolContext

__all__ = [
    "RESOURCE_TEMPLATES",
    "SCHEME",
    "list_resources",
    "read_resource",
]

# Every template S6.4 serves, in the order §3's table lists them.
RESOURCE_TEMPLATES: Final[tuple[types.ResourceTemplate, ...]] = (
    MEMORY_TEMPLATE,
    AUDIT_TEMPLATE,
    ONTOLOGY_TEMPLATE,
)

type _Reader = Callable[[ToolContext, str], Awaitable[types.TextResourceContents]]

# Authority segment to reader. `guardmem://memory/patient:7781` parses as
# authority `memory` and path `/patient:7781`, so the authority is the *kind* of
# resource and the path is its argument. Keyed off `RESOURCE_TEMPLATES` by
# `test_every_template_has_a_reader`, so a template advertised and not wired -
# which lists in a client and then fails on click - is a test failure.
_READERS: Final[dict[str, _Reader]] = {
    "memory": read_memory,
    "audit": read_audit,
    "ontology": read_ontology,
}


def list_resources(state: ServerState) -> list[types.Resource]:
    """The concrete resources this server can name without being told anything.

    Args:
        state: The process's resources.

    Returns:
        One entry per template that the *configuration* already answers the
        variable for, which today is the two keyed by namespace, and only when
        `GM_MCP_DEFAULT_NAMESPACE` is set. An empty list otherwise.

    **`guardmem://audit/{trace_id}` is never listed**, and that is not an
    oversight. A trace id is minted per proposal, so there is no "the" trace to
    offer; a client constructs that URI from a `trace_id` a tool result gave it,
    which is exactly what `resources/templates/list` is for.

    Listing nothing when no namespace is configured is deliberate too. The
    alternative is a resource whose URI contains an empty segment, which reads
    as a real attachment in a client's picker and fails the moment it is
    clicked. `context_for` already refuses that case with a message naming the
    variable; a resource list is not a place a message can be read.
    """
    namespace = state.settings.mcp_default_namespace
    if not namespace:
        return []
    return [
        types.Resource(
            uri=uri_for("memory", namespace),
            name=f"Believed memory - {namespace}",
            description=(
                "What GuardMem currently believes about this namespace: every live "
                "assertion with its confidence and the verbatim span it came from, "
                "plus what has been retired. Attach it to answer from governed "
                "memory rather than from the conversation."
            ),
            mime_type="text/markdown",
        ),
        types.Resource(
            uri=uri_for("ontology", namespace),
            name=f"Predicate schema - {namespace}",
            description=(
                "The predicates this namespace accepts, with their object types, "
                "cardinality and impact. Attach it before proposing facts: a "
                "candidate outside this vocabulary is quarantined by the schema "
                "gate rather than stored."
            ),
            mime_type="text/yaml",
        ),
    ]


async def read_resource(state: ServerState, uri: str) -> types.ReadResourceResult:
    """Resolve one `resources/read`.

    Args:
        state: The process's resources.
        uri: The URI the client asked for.

    Returns:
        One content block, typed by the template that matched.

    Raises:
        ToolRefusedError: the scheme is wrong, the authority names no template,
            the path is empty, or the resource's own reader declines - an
            unconfigured tenant, a trace with no events, a namespace that is not
            a string. The SDK turns this into a JSON-RPC error, which is what a
            client renders as a failed attachment; see the module docstring for
            why a resource does not get the tool surface's `isError` treatment.

    The tenant comes from `context_for`, exactly as it does for a tool, and for
    the same reason: there is no authenticated request to resolve one from, so
    `GM_MCP_TENANT_ID` is it. A resource read is a read of governed memory and
    gets the same refusal rather than a quieter one.
    """
    kind, argument = _split(uri)
    reader = _READERS.get(kind)
    if reader is None:
        raise ToolRefusedError(
            f"no resource at {uri!r}. This server serves: "
            + ", ".join(f"{SCHEME}://{name}/…" for name in sorted(_READERS))
            + ". The full set is in MCP_INTEGRATION.md §3; the rest arrive with "
            "the features they describe."
        )
    # `memory` and `ontology` are keyed by namespace and `audit` is not, so the
    # path segment is handed to `context_for` as a namespace only where it is
    # one. Passing a trace id there would resolve a namespace named after a
    # trace and read an empty one.
    #
    # `require_namespace=False` for audit, and leaving it out was a real bug:
    # the reader asks only for a tenant, but `context_for` demanded a namespace
    # as well, so a server with a tenant and no `GM_MCP_DEFAULT_NAMESPACE` could
    # not read an audit record at all - refused for want of something it never
    # uses. Caught by the integration test that pins the variable empty.
    scoped = kind != "audit"
    arguments: dict[str, object] = {"namespace": argument} if scoped else {}
    context = context_for(state, arguments, require_namespace=scoped)
    return types.ReadResourceResult(contents=[await reader(context, argument)])


def _split(uri: str) -> tuple[str, str]:
    """Break a `guardmem://` URI into its kind and its one argument.

    Returns:
        `(authority, first path segment)`, percent-decoded.

    Raises:
        ToolRefusedError: the scheme is not `guardmem`, or there is no path.

    **The path is unquoted and the result is never interpolated into SQL.** A
    namespace reaches `PgVectorStore` as a bound parameter and a trace id
    reaches the audit read the same way, so this is a lookup key rather than a
    fragment - `RULES.md` §4's parameterised-SQL rule is kept by the store, and
    this function's job is only to not mangle a legitimate value. Namespaces
    contain a colon (`patient:7781`), which is a legal path character, so
    splitting on `/` rather than on `:` is what keeps them intact.
    """
    parsed = urlsplit(uri)
    if parsed.scheme != SCHEME:
        raise ToolRefusedError(
            f"{uri!r} is not a GuardMem resource: expected the {SCHEME}:// scheme, "
            f"got {parsed.scheme or 'none'}://."
        )
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    if not segments:
        raise ToolRefusedError(
            f"{uri!r} names a resource kind with no argument. "
            f"{SCHEME}://memory/ and {SCHEME}://ontology/ take a namespace, "
            f"{SCHEME}://audit/ takes a trace id."
        )
    return parsed.netloc, segments[0]
