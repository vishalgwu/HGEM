"""Who is calling, and which tenant they speak for.  BUILD_NOTEBOOK.md S8.2

`Authenticate` then `Tenancy`, and that order matters most in the whole chain: a
tenant is a property of an authenticated principal, so the reverse would have to
take it from something the caller sent - which is the cross-tenant read this
service exists to prevent.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from gateway.auth import InvalidCredentialError, credential_from
from gateway.middleware._paths import UNAUTHENTICATED

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response

    from gateway.auth import AuthBackend

__all__ = ["Authenticate", "Tenancy"]

_LOGGER: Final = logging.getLogger(__name__)

# One body, for every credential failure. See `auth.py`: distinguishing absent from
# malformed from unknown is how a caller enumerates keys.
_UNAUTHORIZED: Final = {"detail": "invalid or missing credential"}


class Authenticate(BaseHTTPMiddleware):
    """Resolve the bearer credential to a `Principal`, or refuse.

    **The backend is read from process state at dispatch, not held from
    construction.** Holding it would mean `build_app()` had to build one, which
    means reading `Settings`, which means importing `gateway.main` requires a
    populated environment. CI's `gates` job has no `.env` - only the `integration`
    job does `cp .env.example .env` - so that import would fail there while
    passing on every developer machine, taking the contract suite with it.

    The backend is process-scoped configuration, exactly like the pool, so it
    lives where the pool lives: on `GatewayState`, built by lifespan.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Authenticate, or return 401.

        Args:
            request: The incoming request.
            call_next: The rest of the chain.

        Returns:
            The downstream response, or a fixed 401.

        `WWW-Authenticate` is set because RFC 7235 requires it on a 401 and
        clients use it to decide what to send next.
        """
        if request.url.path in UNAUTHENTICATED:
            return await call_next(request)
        try:
            credential = credential_from(request.headers.get("Authorization"))
            backend: AuthBackend = request.app.state.gateway.auth
            principal = backend.principal_for(credential)
        except InvalidCredentialError as exc:
            # The reason goes to the log, never to the caller.
            _LOGGER.info(
                "rejected an unauthenticated request",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "path": request.url.path,
                    "reason": str(exc),
                },
            )
            return JSONResponse(
                _UNAUTHORIZED,
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.principal = principal
        return await call_next(request)


class Tenancy(BaseHTTPMiddleware):
    """Bind the request to exactly one tenant.

    **This does not hold a database connection, and S8.2's own snippet suggests
    it might.** That snippet - `SELECT set_config('app.tenant_id', $1, true)` - is
    correct about the mechanism and is already implemented, in
    `guardmem_core.memory.vector.pool.tenant_transaction`, which sets it per
    transaction with `is_local=true` so it unwinds with the transaction.

    Holding one here instead would mean checking a connection out of the pool for
    the whole request. The pool is sized for concurrency, not for request
    duration: a handler that waits on a model for two seconds would hold a
    connection for two seconds, and at the 50 rps S8.4 has to sustain the pool is
    empty long before the CPU is busy. It would also make every request pay a
    `set_config` round trip whether or not it touches Postgres, which `/readyz`
    and any future cached read would not.

    So what this layer owns is the *decision*: one tenant per request, taken from
    the authenticated principal and from nowhere else, recorded where handlers and
    the audit trail can read it. Applying it to a statement is
    `tenant_transaction`'s job at the point a statement is made.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Record the request's tenant.

        Args:
            request: The incoming request, carrying a principal when authenticated.
            call_next: The rest of the chain.

        Returns:
            The downstream response.

        A request with no principal passes through with no tenant - it is either
        an unauthenticated path or it was already refused upstream. The absence is
        not defaulted to anything: a default tenant is a cross-tenant write with a
        plausible-looking cause.
        """
        principal = getattr(request.state, "principal", None)
        request.state.tenant_id = None if principal is None else principal.tenant_id
        return await call_next(request)
