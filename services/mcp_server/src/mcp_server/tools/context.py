"""Who a tool call speaks for, and what it is allowed to touch.  S6.2

Every one of the four tools needs the same two answers before it can do
anything: which tenant, and which namespace. In the finished system neither is a
question - `MCP_INTEGRATION.md` §1's `GUARDMEM_API_KEY` carries the tenant, the
gateway resolves it (S8.1) and its middleware sets the RLS context (S8.2), and a
tool handler never sees the decision. None of that exists, and this server talks
straight to Postgres.

So the tenant comes from configuration and the refusal to proceed without it is
the whole point of this module. `RULES.md` §4 wants defence in depth - "RLS
*and* namespace prefixing *and* an app-layer check" - and with no authenticated
request there is nothing for the first two to be *about*. A default would be a
cross-tenant read with no symptom: the query succeeds and returns somebody
else's rows.

**The namespace is allowed a default and the tenant is not**, which is not an
inconsistency. §2.1 publishes "defaults to server-configured namespace" on the
tool schema itself, so a per-call namespace is part of the contract and a
configured fallback is what the contract asks for. Tenancy is not on any tool
schema, in any of §2.1-§2.4, deliberately: a caller that could name its own
tenant is a caller that could read another one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from uuid import UUID

from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.types import Namespace, TenantId

if TYPE_CHECKING:
    from guardmem_core.memory.vector.base import VectorStore
    from mcp_server.lifespan import ServerState

__all__ = ["ToolContext", "ToolRefusedError", "context_for"]

# `assertion.tenant_id` is a `uuid` column and every statement casts `$n::uuid`,
# so a non-UUID here fails inside asyncpg with a message about a cast rather
# than about configuration. Checked at the edge instead.
_TENANT_HINT: Final = (
    "GM_MCP_TENANT_ID must be a UUID - it is the value row-level security is set "
    "from, and the assertion table's tenant_id column is `uuid`. `make seed` "
    "prints the demo tenant's id."
)


class ToolRefusedError(Exception):
    """A tool call that will not be attempted, and the reason a caller can act on.

    Distinct from `guardmem_core.errors.GuardMemError` for the reason
    `ConfigurationError` is: that hierarchy is the *domain* one, enumerated in
    `RULES.md` §2.3, and every member carries an `http_status` and an `mcp_code`
    because it describes something that happened to a *candidate*. This
    describes something that stopped a call from happening at all - an
    unconfigured server, an unbuilt dependency - and it is turned into an MCP
    error result by `tools/__init__.py` rather than mapped onto a domain code
    that would misdescribe it.
    """


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler needs that is not in its own arguments.

    Attributes:
        state: The process's resources.
        tenant_id: Whose data this call may touch.
        namespace: The isolation scope, from the call or the configured default.
        store: The assertion store, bound to `tenant_id`. Typed as the
            `VectorStore` protocol rather than as `PgVectorStore`, which is the
            rule the whole architecture rests on: `ARCHITECTURE.md` §2.4 makes a
            backend an operator decision, so only the composition root names a
            concrete class. `context_for` is that root and builds the Postgres
            one; everything downstream is held to the protocol, which is also
            what lets the unit suite drive these handlers against
            `FakeVectorStore` with no database. **Request-scoped, and
            built per call rather than held on `ServerState`** - `pool.py` gives
            the reason: a pool is process-scoped and a store is bound to the
            tenant of one request, so caching one on the process is how a server
            ends up serving every tenant as the first one that connected.
    """

    state: ServerState
    tenant_id: TenantId
    namespace: Namespace
    store: VectorStore


def context_for(
    state: ServerState, arguments: dict[str, object], *, require_namespace: bool = True
) -> ToolContext:
    """Resolve the tenant and namespace for one tool call.

    Args:
        state: The process's resources.
        arguments: The call's arguments. Only `namespace` is read here.
        require_namespace: Whether this caller is scoped to a namespace at all.

            **Every tool is; `guardmem://audit/{trace_id}` is not.** A trace is
            minted per proposal and its audit events are keyed by tenant and
            trace alone - one proposal may write across two namespaces, so
            scoping its record to one would silently drop half of it. S6.4 found
            this the hard way: the audit resource refused outright on a server
            configured with a tenant and no `GM_MCP_DEFAULT_NAMESPACE`, because
            it was asking for something it does not use.

            Passing `False` yields a context whose `namespace` is the configured
            default if there is one and `Namespace("")` if there is not, and a
            caller that passes `False` **must not read `namespace`** - an empty
            one selects nothing and would look like an empty namespace rather
            than like a mistake. The tenant is required either way, because
            every reader here touches governed memory.

    Returns:
        The context, with a store already bound to the tenant.

    Raises:
        ToolRefusedError: the server has no configured tenant, the configured tenant
            is not a UUID, the call's `namespace` is not a string, or - when
            `require_namespace` - none was given and none is configured.

    Every failure here is a refusal rather than a fallback, and every message
    names the variable to set. A tool that guessed any of these would produce a
    query that succeeds and answers the wrong question, which is the failure mode
    this whole product is about.
    """
    if not state.settings.mcp_tenant_id:
        raise ToolRefusedError(
            "this server has no tenant configured, so it will not read or write "
            f"memory. Set GM_MCP_TENANT_ID. {_TENANT_HINT}"
        )
    try:
        UUID(state.settings.mcp_tenant_id)
    except ValueError as exc:
        raise ToolRefusedError(
            f"GM_MCP_TENANT_ID is not a UUID: {state.settings.mcp_tenant_id!r}. {_TENANT_HINT}"
        ) from exc

    namespace = (
        _namespace(state, arguments)
        if require_namespace
        else Namespace(state.settings.mcp_default_namespace)
    )
    tenant_id = TenantId(state.settings.mcp_tenant_id)
    return ToolContext(
        state=state,
        tenant_id=tenant_id,
        namespace=namespace,
        store=PgVectorStore(
            state.pool,
            state.embedder,
            tenant_id=tenant_id,
            timeout_s=state.settings.store_timeout_s,
        ),
    )


def _namespace(state: ServerState, arguments: dict[str, object]) -> Namespace:
    """The call's namespace, or the configured default.

    Raises:
        ToolRefusedError: the argument is present and not a string, or absent with no
            default configured.

    The type check is not redundant with the tool's `inputSchema`. The SDK
    validates the *envelope* - that `arguments` is an object - and leaves the
    per-property types to the server unless an `outputSchema`-style validator is
    wired, so a client sending `{"namespace": 42}` reaches here. It would
    otherwise become the string `"42"` somewhere downstream and select nothing.
    """
    given = arguments.get("namespace")
    if given is not None:
        if not isinstance(given, str):
            raise ToolRefusedError(f"namespace must be a string, got {type(given).__name__}")
        return Namespace(given)
    if not state.settings.mcp_default_namespace:
        raise ToolRefusedError(
            "no namespace was given and this server has no default. Pass one in "
            "the tool call, or set GM_MCP_DEFAULT_NAMESPACE - MCP_INTEGRATION.md "
            "§2.1 makes the namespace default to the server's configured one."
        )
    return Namespace(state.settings.mcp_default_namespace)
