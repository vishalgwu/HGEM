"""What a request may spend, and what its body hashes to.  S8.3, S8.2

`RateLimit` then `BodyHash`, innermost. Hashing is work, and work done for a request
about to be refused is work done for an attacker - so the cheap rejection comes first
and the hash runs only for requests that will be handled.

Pure ASGI rather than `BaseHTTPMiddleware`; `_asgi.py` carries the measurement. `BodyHash`
is the layer that gained most from the move, and not only in speed - see its docstring.
"""

from __future__ import annotations

import logging
import math
from hashlib import sha256
from typing import TYPE_CHECKING, Final

import redis.exceptions

from gateway.middleware._asgi import add_header, buffered, is_http, send_json

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from gateway.limits import Decision, RateLimiter

__all__ = ["BodyHash", "RateLimit"]

_LOGGER: Final = logging.getLogger(__name__)

# Methods that carry no body, so hashing them would be paying for a hash of nothing on
# the most common request shape.
_BODYLESS: Final = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})


class RateLimit:
    """S8.3's token bucket, keyed by `tenant:api_key`.

    It sits here, after authentication and tenancy, because the key needs both - and
    S8.2 put the layer in position while it was still inert so that filling it would not
    mean inserting into a chain whose shape nothing had checked.

    **A request with no principal is not limited, and that is not a hole.** The only
    unauthenticated paths are the four probes; everything else is already a 401 by the
    time this runs, refused by a cheaper layer. Limiting liveness probes would also be
    actively wrong - an orchestrator polling `/healthz` would eventually be told 429 and
    restart a process that was fine.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Consume a token, or refuse with 429.

        Args:
            scope: The ASGI scope, carrying a principal when authenticated.
            receive: Passed through.
            send: Wrapped to carry the budget headers, or used directly to refuse.

        **Redis being down fails OPEN, and that is a decision rather than an oversight.**
        The alternative is a limiter whose unavailability takes the whole API down - a
        dependency that exists to shed load becoming the reason nothing is served.
        `ARCHITECTURE.md` §4 degrades toward serving, and what this protects is a cost
        and fairness budget rather than a correctness invariant. Logged at `warning` so
        an operator sees an unlimited window rather than discovering it from a bill.

        The contrast with tenancy is deliberate: a tenant that cannot be resolved fails
        *closed*, because that one is a correctness invariant and serving without it is a
        cross-tenant read.
        """
        if not is_http(scope):
            await self.app(scope, receive, send)
            return
        state = scope.get("state") or {}
        principal = state.get("principal")
        if principal is None:
            await self.app(scope, receive, send)
            return
        gateway = scope["app"].state.gateway
        limiter: RateLimiter = gateway.limiter
        try:
            decision = await limiter.check(principal)
        except redis.exceptions.RedisError as exc:
            _LOGGER.warning(
                "rate limiter unavailable; allowing the request",
                extra={
                    "request_id": state.get("request_id"),
                    "tenant": str(principal.tenant_id),
                    "reason": str(exc),
                },
            )
            await self.app(scope, receive, send)
            return
        limit = gateway.settings.rate_limit_per_minute
        if not decision.allowed:
            _LOGGER.info(
                "rate limited",
                extra={
                    "request_id": state.get("request_id"),
                    "tenant": str(principal.tenant_id),
                    "key_id": principal.key_id,
                },
            )
            await send_json(
                send,
                429,
                {"detail": "rate limit exceeded"},
                headers={
                    # Whole seconds, rounded UP. RFC 9110 allows only integers, and
                    # rounding down invites a retry that is still too early - which
                    # costs the client another 429 and this process another refusal.
                    "Retry-After": str(max(1, math.ceil(decision.retry_after_s))),
                    **_budget(limit, decision),
                },
            )
            return
        wrapped = send
        for name, value in _budget(limit, decision).items():
            wrapped = add_header(wrapped, name, value)
        await self.app(scope, receive, wrapped)


class BodyHash:
    """Hash the request body once, for idempotency and provenance."""

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Record `sha256` of the body for the handler to read.

        Args:
            scope: The ASGI scope. `body_hash` goes on `state` as a callable.
            receive: Wrapped so chunks are recorded as the handler reads them.
            send: Passed through.

        **The body is never read by this layer, which is the change from the
        `BaseHTTPMiddleware` version and an improvement beyond speed.** That one called
        `await request.body()`, consuming the stream, and relied on the base class
        replaying it to the handler through a memory stream - so every request was
        buffered in full whether or not the handler wanted it, and a request the handler
        was about to reject was buffered anyway.

        Here the layer passes a `receive` that records each chunk *on the way past*. The
        handler's own read is the only read, and `state["body_hash"]` is a callable
        rather than a value because at this point nothing has been read yet - it is
        resolved when a handler asks, by which time the chunks are in hand.

        Skipped for methods that carry no body, so a GET pays nothing.
        """
        if not is_http(scope):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        if scope["method"] in _BODYLESS:
            state["body_hash"] = None
            await self.app(scope, receive, send)
            return
        wrapped, collected = buffered(receive)
        state["body_hash_of"] = collected
        await self.app(scope, wrapped, send)


def _budget(limit: int, decision: Decision) -> dict[str, str]:
    """The `X-RateLimit-*` trio a client needs to pace itself.

    Args:
        limit: The configured per-minute rate.
        decision: What the bucket said.

    Returns:
        Limit, remaining and reset, as strings.

    Sent on allowed responses as well as refusals, because a client that can only learn
    its budget by exceeding it has to exceed it to behave well. `X-` names rather than
    the IETF `RateLimit-*` draft: the prefixed spellings are what existing client
    libraries already read.
    """
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, decision.remaining)),
        "X-RateLimit-Reset": str(max(0, math.ceil(decision.retry_after_s))),
    }


def body_hash_of(scope_state: dict[str, object]) -> str | None:
    """Resolve the hash of what the handler read.

    Args:
        scope_state: `request.state`'s backing mapping.

    Returns:
        The hex digest, or None for a body-less method or an empty body.

    A function rather than a value on state, because `BodyHash` runs *before* the body
    exists. Calling this after the handler has read the body is what makes the digest
    the digest of what was actually received - which is the property `source_hash` in the
    audit trail needs, and the one a value captured too early would quietly lack.
    """
    collector = scope_state.get("body_hash_of")
    if collector is None:
        return None
    body = collector() if callable(collector) else b""
    return sha256(body).hexdigest() if body else None
