"""The request chain.  BUILD_NOTEBOOK.md S8.2

S8.2: "Middleware order matters: `request_id -> auth -> tenancy -> ratelimit ->
body_hash`." It does, and each boundary is one of these reasons:

1. **`request_id` first** so that every line logged about a request - including
   the 401 from the next layer - carries the same id. A correlation id added after
   the first thing that can fail is a correlation id missing from the failures you
   most want to correlate.
2. **`auth` before `tenancy`** because a tenant is a *property of* an
   authenticated principal. The reverse order is the cross-tenant read this
   service exists to prevent: it would take a tenant from something the caller
   sent.
3. **`tenancy` before `ratelimit`** because S8.3's bucket is keyed by
   `tenant:api_key`, so the limiter cannot key anything until both are known.
4. **`ratelimit` before `body_hash`** because hashing is work, and work done for a
   request that is about to be refused is work done for an attacker. Cheap
   rejections come first.
5. **`body_hash` last** so it runs only for requests that will be handled. It
   feeds S8.3's idempotency key and the audit trail's `source_hash`.

**Starlette applies `add_middleware` in reverse.** The last one added is the
outermost. `apply` below adds them in reverse of the documented order so the
documented order is the execution order - and the list is written once, in that
order, because a chain assembled by five separate calls is a chain whose order is
whatever the calls happen to be in.

**Nothing here opens a database connection.** The tenancy layer resolves *which*
tenant a request speaks for and puts it on the request; it does not hold a
connection with `app.tenant_id` set for the request's lifetime. That shape is
tempting because S8.2's snippet shows `set_config` on a connection, but a pooled
connection held across a handler serialises every request behind the pool and at
the 50 rps S8.4 must sustain it exhausts `max_connections`. `pool.tenant_transaction`
is the seam that already exists and already sets the variable, per transaction.
See `tenancy` for the whole argument.
"""

from __future__ import annotations

import logging
import math
import uuid
from hashlib import sha256
from typing import TYPE_CHECKING, Final

import redis.exceptions
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from gateway.auth import InvalidCredentialError, credential_from

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gateway.auth import AuthBackend
    from gateway.limits import Decision, RateLimiter

__all__ = ["REQUEST_ID_HEADER", "apply"]

_LOGGER: Final = logging.getLogger(__name__)

# Echoed on every response. `X-Request-Id` rather than `traceparent`: this is the
# id a human quotes in a support thread, and it stays readable when OTel
# propagation arrives beside it rather than being replaced by it.
REQUEST_ID_HEADER: Final = "X-Request-Id"

# Paths that carry no principal. Deliberately exact rather than a prefix match: a
# prefix would make `/healthz-internal` unauthenticated too, and an auth bypass
# that comes from a `startswith` is the classic version of this bug.
_UNAUTHENTICATED: Final = frozenset({"/healthz", "/readyz", "/openapi.json", "/docs"})

# One body, for every credential failure. See `auth.py`: distinguishing absent
# from malformed from unknown is how a caller enumerates keys.
_UNAUTHORIZED: Final = {"detail": "invalid or missing credential"}


class RequestId(BaseHTTPMiddleware):
    """Give every request an id, and echo it back."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach an id, then echo it on the way out.

        Args:
            request: The incoming request.
            call_next: The rest of the chain.

        Returns:
            The downstream response, with `X-Request-Id` set.

        **A client-supplied id is honoured, and that is a deliberate trade.** It
        makes a caller's own logs correlate with ours, which is the whole point of
        the header. It also means the value is caller-controlled and must never be
        used as anything but a correlation label - never as a cache key, never in
        a SQL string, and never trusted to be unique. It is truncated because an
        unbounded header would otherwise reach every log line.
        """
        supplied = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = supplied[:64] if supplied else uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


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
        if request.url.path in _UNAUTHENTICATED:
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


class RateLimit(BaseHTTPMiddleware):
    """S8.3's token bucket, keyed by `tenant:api_key`.

    It sits here, after authentication and tenancy, because the key needs both -
    and S8.2 put the layer in position while it was still inert so that filling it
    would not mean inserting into a chain whose shape nothing had checked.

    **A request with no principal is not limited, and that is not a hole.** The
    only unauthenticated paths are `/healthz`, `/readyz`, `/openapi.json` and
    `/docs`; everything else is already a 401 by the time this runs, refused by a
    cheaper layer. Limiting liveness probes would also be actively wrong - an
    orchestrator polling `/healthz` would eventually be told 429 and restart a
    process that was fine.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Consume a token, or refuse with 429.

        Args:
            request: The incoming request, carrying a principal when authenticated.
            call_next: The rest of the chain.

        Returns:
            The downstream response, with the three `X-RateLimit-*` headers, or a
            429 carrying `Retry-After`.

        **Redis being down fails OPEN, and that is a decision rather than an
        oversight.** The alternative is a limiter whose unavailability takes the
        whole API down - a dependency that exists to shed load becoming the reason
        nothing is served. `ARCHITECTURE.md` §4 degrades toward human review and
        toward serving, not toward refusing, and the thing this protects is a cost
        and fairness budget rather than a correctness invariant. It is logged at
        `warning` so an operator sees an unlimited window rather than discovering
        it from a bill.

        The contrast with tenancy is deliberate and worth stating: a tenant that
        cannot be resolved fails *closed*, because that one is a correctness
        invariant and serving without it is a cross-tenant read.
        """
        principal = getattr(request.state, "principal", None)
        if principal is None:
            return await call_next(request)
        limiter: RateLimiter = request.app.state.gateway.limiter
        try:
            decision = await limiter.check(principal)
        except redis.exceptions.RedisError as exc:
            _LOGGER.warning(
                "rate limiter unavailable; allowing the request",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "tenant": str(principal.tenant_id),
                    "reason": str(exc),
                },
            )
            return await call_next(request)
        if not decision.allowed:
            _LOGGER.info(
                "rate limited",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "tenant": str(principal.tenant_id),
                    "key_id": principal.key_id,
                },
            )
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    # Integer seconds, rounded UP. RFC 9110 allows only whole
                    # seconds, and rounding down would invite a retry that is
                    # still too early - which costs the client another 429 and
                    # this process another request to refuse.
                    "Retry-After": str(max(1, math.ceil(decision.retry_after_s))),
                    **_limit_headers(request, decision),
                },
            )
        response = await call_next(request)
        response.headers.update(_limit_headers(request, decision))
        return response


class BodyHash(BaseHTTPMiddleware):
    """Hash the request body once, for idempotency and provenance."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Compute `sha256` of the body and record it.

        Args:
            request: The incoming request.
            call_next: The rest of the chain.

        Returns:
            The downstream response.

        **The body is read here and must still be readable by the handler.**
        Starlette caches it on the request once awaited, so a later `await
        request.json()` sees the same bytes rather than an exhausted stream. That
        is the behaviour this depends on, and it is the reason this layer exists at
        all rather than each handler hashing its own body: two hashes of one body
        is two things that can disagree, and `source_hash` is in the audit chain.

        Skipped for methods that carry no body, so a GET does not pay for a hash
        of nothing.
        """
        if request.method in {"GET", "HEAD", "OPTIONS", "DELETE"}:
            request.state.body_hash = None
            return await call_next(request)
        body = await request.body()
        request.state.body_hash = sha256(body).hexdigest() if body else None
        return await call_next(request)


def _limit_headers(request: Request, decision: Decision) -> dict[str, str]:
    """The `X-RateLimit-*` trio a client needs to pace itself.

    Args:
        request: For the configured limit, read off process state.
        decision: What the bucket said.

    Returns:
        Limit, remaining and reset, as strings.

    Sent on allowed responses as well as refusals, because a client that can only
    learn its budget by exceeding it has to exceed it to behave well. `X-` names
    rather than the IETF `RateLimit-*` draft: the prefixed spellings are what
    every existing client library already reads.
    """
    settings = request.app.state.gateway.settings
    return {
        "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
        "X-RateLimit-Remaining": str(max(0, decision.remaining)),
        "X-RateLimit-Reset": str(max(0, math.ceil(decision.retry_after_s))),
    }


def apply(app: FastAPI) -> None:
    """Install the chain so that execution order matches the documented order.

    Args:
        app: The application to wrap.

    Starlette treats the last-added middleware as the outermost, so this iterates
    the documented order in reverse. Writing the order once, forwards, and
    reversing here is what keeps the list readable as the thing S8.2 specifies -
    `tests/unit/test_gateway_middleware.py` asserts the resulting order, because a
    chain whose order is only a comment is a chain that will be reordered.
    """
    app.add_middleware(BodyHash)
    app.add_middleware(RateLimit)
    app.add_middleware(Tenancy)
    app.add_middleware(Authenticate)
    app.add_middleware(RequestId)
