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

if TYPE_CHECKING:
    from gateway.lifespan import GatewayState

__all__ = ["GatewayDep", "gateway_state"]


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
