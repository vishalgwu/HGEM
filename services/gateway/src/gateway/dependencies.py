"""How a handler reaches what the process owns.  BUILD_NOTEBOOK.md S8.1

One dependency, and it exists so that no handler writes
`request.app.state.gateway` itself. `app.state` is untyped - Starlette declares
it as an object you may set anything on - so every direct read is a place where
`mypy --strict` sees `Any` and stops checking. `RULES.md` §2.1's whole argument
for `NewType` over `str` applies to the same effect here: the types are only
worth having where they are actually seen.

`Annotated[..., Depends(...)]` rather than a default argument value, because
`state: GatewayState = Depends(gateway_state)` puts a non-`GatewayState` object
in a slot annotated `GatewayState`, and `mypy --strict` is right to reject it.
The `Annotated` spelling is the one FastAPI documents for exactly this reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from guardmem_core.memory.vector.pgvector_store import PgVectorStore

if TYPE_CHECKING:
    from gateway.auth import Principal
    from gateway.lifespan import GatewayState
    from guardmem_core.memory.vector.base import Embedder

__all__ = [
    "EmbedderDep",
    "GatewayDep",
    "PrincipalDep",
    "StoreDep",
    "embedder",
    "gateway_state",
    "principal",
    "vector_store",
]


def gateway_state(request: Request) -> GatewayState:
    """The process resources lifespan built.

    Args:
        request: The live request, for its `app`.

    Returns:
        The `GatewayState` lifespan yielded at startup.

    Raises:
        AttributeError: the app is serving without having run its lifespan. That
            is a programming error rather than a request problem - it means an
            `app` was constructed and mounted without `lifespan`, or a test built
            a `TestClient` without entering its context - so it is deliberately
            left to surface rather than converted into a 503. A 503 would say
            "try again", and no number of retries fixes it.
    """
    state: GatewayState = request.app.state.gateway
    return state


# The annotation every handler uses. Named rather than spelled out at each call
# site so that the wiring changes in one place if `app.state` ever stops being
# where this lives.
GatewayDep = Annotated["GatewayState", Depends(gateway_state)]


def principal(request: Request) -> Principal:
    """The authenticated caller.

    Args:
        request: The live request.

    Returns:
        The `Principal` the auth middleware resolved.

    Raises:
        RuntimeError: the route is authenticated but the middleware did not run,
            or the path was added to `middleware._UNAUTHENTICATED` and still asks
            for a principal. Both are programming errors and neither is a 401:
            answering 401 would tell a caller their credential was rejected when
            in fact it was never examined, which is the kind of wrong signal that
            gets an auth bug diagnosed as a client problem for a week.
    """
    found = getattr(request.state, "principal", None)
    if found is None:
        raise RuntimeError(
            "no principal on the request: this route is behind Authenticate, so "
            "either the middleware chain is not installed or the path is listed "
            "as unauthenticated while a handler asks for a caller."
        )
    resolved: Principal = found
    return resolved


def vector_store(request: Request) -> PgVectorStore:
    """A store bound to this request's tenant, and to no other.

    Args:
        request: The live request, carrying the principal.

    Returns:
        A `PgVectorStore` whose every statement runs with
        `app.tenant_id` set to the caller's tenant.

    **The tenant comes from the principal and from nowhere else.** Not from a
    query parameter, not from a body field, not from a header. That is the whole
    isolation guarantee: `queries.py` contains no tenant predicate at all, so a
    read is scoped by the RLS policy alone, and the policy reads
    `app.tenant_id`, which `pool.tenant_transaction` sets from the value this
    function passes. A tenant taken from anything the caller sends would be a
    cross-tenant read with a plausible-looking cause.

    Constructed per request rather than held on `GatewayState`, because a store
    binds one tenant and the process serves many. The *pool* is what is shared;
    binding is cheap and is the thing that must not be.
    """
    state = gateway_state(request)
    return PgVectorStore(
        state.pool,
        state.embedder,
        tenant_id=principal(request).tenant_id,
        timeout_s=state.settings.store_timeout_s,
    )


def embedder(request: Request) -> Embedder:
    """This process's query embedder.

    Args:
        request: The live request.

    Returns:
        The `Embedder` lifespan built, typed as the protocol.

    A dependency of its own rather than reached through the store, whose
    `_embedder` is private. The store's copy is for writes; this is the read
    path's, and they are the same object today only because there is one
    `Embedder` in the package.
    """
    return gateway_state(request).embedder


# The annotations routes use, for the reason `GatewayDep` is named.
PrincipalDep = Annotated["Principal", Depends(principal)]
EmbedderDep = Annotated["Embedder", Depends(embedder)]
StoreDep = Annotated["PgVectorStore", Depends(vector_store)]
