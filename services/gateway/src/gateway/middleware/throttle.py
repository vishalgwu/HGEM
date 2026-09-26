"""What a request may spend, and what its body hashes to.  S8.3, S8.2

`RateLimit` then `BodyHash`, innermost. Hashing is work, and work done for a
request about to be refused is work done for an attacker - so the cheap rejection
comes first and the hash runs only for requests that will be handled.
"""

from __future__ import annotations

import logging
import math
from hashlib import sha256
from typing import TYPE_CHECKING, Final

import redis.exceptions
from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response

    from gateway.limits import Decision, RateLimiter

__all__ = ["BodyHash", "RateLimit"]

_LOGGER: Final = logging.getLogger(__name__)


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
