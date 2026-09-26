"""What every request gets before anything may refuse it.  ADR-0012, S8.2

`RequestDeadline` then `RequestId`, outermost first. Both are framing rather than
policy: one starts the clock, the other names the request, and neither can refuse
it. Putting them outside the layers that *can* refuse is what makes a 401 carry a
correlation id and sit inside a budget.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from starlette.middleware.base import BaseHTTPMiddleware

from gateway.deadline import Deadline
from gateway.middleware._paths import UNBUDGETED

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response

__all__ = ["REQUEST_ID_HEADER", "RequestDeadline", "RequestId"]

# Echoed on every response. `X-Request-Id` rather than `traceparent`: this is the
# id a human quotes in a support thread, and it stays readable when OTel
# propagation arrives beside it rather than replacing it.
REQUEST_ID_HEADER: Final = "X-Request-Id"


class RequestDeadline(BaseHTTPMiddleware):
    """Start the request's time budget, outside everything else.

    **Outermost, and that is the whole point.** ADR-0012 asks for "one deadline,
    threaded through and decremented", and a budget that started after
    authentication would not cover authentication - so a request stuck resolving a
    credential against a slow store would be outside its own budget. The clock has
    to start when the request arrives.

    This makes the chain six layers where S8.2 named five. The five keep their
    relative order exactly; a deadline is not a peer of them, it is a wrapper
    around all of them, which is why it goes on the outside rather than into the
    sequence. `test_gateway_middleware.py` asserts both facts separately.

    **It does not enforce the budget by cancelling anything.** Cancelling a handler
    mid-write is how a transaction is left open and an outbox row is orphaned;
    `asyncio.timeout` around `call_next` would do exactly that. What enforces the
    budget is every downstream call asking `for_call()` for its timeout, so the
    request runs out of things it is *allowed to start* rather than being killed
    part way through one. A 504 arrives from the first call that finds no budget.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a deadline and report what was left.

        Args:
            request: The incoming request.
            call_next: The rest of the chain.

        Returns:
            The downstream response, carrying `X-Budget-Remaining-Ms` - except on
            the probe paths, which are passed through untouched. See `UNBUDGETED`.

        The header is for the operator reading a slow trace, not for the client to
        act on: a client cannot spend a budget it does not own. It is the cheapest
        way to see how much of `PRD.md` §6.1's 80ms a request actually used, which
        is the number S8.4's load test has to move.
        """
        if request.url.path in UNBUDGETED:
            return await call_next(request)
        budget_s = request.app.state.gateway.settings.request_deadline_s
        deadline = Deadline.after(budget_s)
        request.state.deadline = deadline
        response = await call_next(request)
        response.headers["X-Budget-Remaining-Ms"] = f"{deadline.remaining_s * 1000:.0f}"
        return response


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
