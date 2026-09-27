"""Who is calling, and which tenant they speak for.  BUILD_NOTEBOOK.md S8.2

`Authenticate` then `Tenancy`, and that order matters most in the whole chain: a tenant
is a property of an authenticated principal, so the reverse would have to take it from
something the caller sent - which is the cross-tenant read this service exists to
prevent.

Pure ASGI rather than `BaseHTTPMiddleware`; `_asgi.py` carries the measurement. It
matters more here than elsewhere: a 401 is the response an overloaded or probed service
sends most often, and it is now two `send` calls with no response object built.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from gateway.auth import InvalidCredentialError, credential_from
from gateway.middleware._asgi import is_http, send_json
from gateway.middleware._paths import UNAUTHENTICATED

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from gateway.auth import AuthBackend

__all__ = ["Authenticate", "Tenancy"]

_LOGGER: Final = logging.getLogger(__name__)

# One body, for every credential failure. See `auth.py`: distinguishing absent from
# malformed from unknown is how a caller enumerates keys.
_UNAUTHORIZED: Final = {"detail": "invalid or missing credential"}


class Authenticate:
    """Resolve the bearer credential to a `Principal`, or refuse.

    **The backend is read from process state per request, not held from construction.**
    Holding it would mean `build_app()` had to build one, which means reading `Settings`,
    which means importing `gateway.main` requires a populated environment. CI's `gates`
    job has no `.env`, so that import would fail there while passing on every developer
    machine - the failure that took CI red for three commits at S8.1.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Authenticate, or send a 401.

        Args:
            scope: The ASGI scope. The principal is put on `state`.
            receive: Passed through.
            send: Used directly when refusing.

        `WWW-Authenticate` is set because RFC 7235 requires it on a 401 and clients use
        it to decide what to send next. The reason for the refusal goes to the log and
        never to the caller.
        """
        if not is_http(scope) or scope["path"] in UNAUTHENTICATED:
            await self.app(scope, receive, send)
            return
        try:
            credential = credential_from(_authorization(scope))
            backend: AuthBackend = scope["app"].state.gateway.auth
            principal = backend.principal_for(credential)
        except InvalidCredentialError as exc:
            state = scope.get("state") or {}
            _LOGGER.info(
                "rejected an unauthenticated request",
                extra={
                    "request_id": state.get("request_id"),
                    "path": scope["path"],
                    "reason": str(exc),
                },
            )
            await send_json(send, 401, dict(_UNAUTHORIZED), headers={"WWW-Authenticate": "Bearer"})
            return
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


class Tenancy:
    """Bind the request to exactly one tenant.

    **This does not hold a database connection, and S8.2's own snippet suggests it
    might.** That snippet - `SELECT set_config('app.tenant_id', $1, true)` - is correct
    about the mechanism and is already implemented, in
    `guardmem_core.memory.vector.pool.tenant_transaction`, which sets it per transaction
    with `is_local=true` so it unwinds with the transaction.

    Holding one here would mean checking a connection out of the pool for the whole
    request. The pool is sized for concurrency, not for duration: a handler waiting two
    seconds on a model would hold a connection for two seconds, and at the 50 rps S8.4
    has to sustain the pool empties long before the CPU is busy. It would also make
    every request pay a `set_config` round trip whether or not it touches Postgres.

    So what this layer owns is the *decision*: one tenant per request, taken from the
    authenticated principal and from nowhere else, recorded where handlers and the audit
    trail can read it. Applying it to a statement is `tenant_transaction`'s job.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record the request's tenant.

        Args:
            scope: The ASGI scope, carrying a principal when authenticated.
            receive: Passed through.
            send: Passed through.

        A request with no principal passes through with no tenant - it is either an
        unauthenticated path or it was already refused upstream. The absence is not
        defaulted to anything: a default tenant is a cross-tenant write with a
        plausible-looking cause.
        """
        if not is_http(scope):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        principal = state.get("principal")
        state["tenant_id"] = None if principal is None else principal.tenant_id
        await self.app(scope, receive, send)


def _authorization(scope: Scope) -> str | None:
    """The `Authorization` header, or None.

    Args:
        scope: The ASGI scope.

    Returns:
        The raw header value, or None when absent.

    `latin-1` because that is what the HTTP spec says header bytes are, and unlike
    `utf-8` it cannot raise - a malformed byte from a client must not become a 500.
    """
    for key, value in scope.get("headers") or []:
        if key == b"authorization":
            decoded: str = value.decode("latin-1")
            return decoded
    return None
