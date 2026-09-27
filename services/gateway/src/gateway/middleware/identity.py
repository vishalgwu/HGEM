"""What every request gets before anything may refuse it.  ADR-0012, S8.2

`RequestDeadline` then `RequestId`, outermost first. Both are framing rather than
policy: one starts the clock, the other names the request, and neither can refuse it.
Putting them outside the layers that *can* refuse is what makes a 401 carry a
correlation id and sit inside a budget.

Both are pure ASGI rather than `BaseHTTPMiddleware` - see `_asgi.py` for the
measurement that forced it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from gateway.deadline import Deadline
from gateway.middleware._asgi import add_header, is_http
from gateway.middleware._paths import UNBUDGETED

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["REQUEST_ID_HEADER", "RequestDeadline", "RequestId"]

# Echoed on every response. `X-Request-Id` rather than `traceparent`: this is the id a
# human quotes in a support thread, and it stays readable when OTel propagation arrives
# beside it rather than replacing it.
REQUEST_ID_HEADER: Final = "X-Request-Id"

# How much of a client-supplied id is kept. It reaches every log line for the request
# and is caller-controlled, so unbounded means one request can put a megabyte into each
# of several lines. 64 is longer than any id worth correlating.
_MAX_ID: Final = 64


class RequestDeadline:
    """Start the request's time budget, outside everything else.

    **Outermost, and that is the whole point.** ADR-0012 asks for "one deadline,
    threaded through and decremented", and a budget that started after authentication
    would not cover authentication - so a request stuck resolving a credential against
    a slow store would be outside its own budget. The clock starts when the request
    arrives.

    This makes the chain six layers where S8.2 named five. The five keep their relative
    order exactly; a deadline is not a peer of them, it is a wrapper around all of them,
    which is why it goes on the outside rather than into the sequence.

    **It does not enforce the budget by cancelling anything.** Cancelling a handler
    mid-write is how a transaction is left open and an outbox row is orphaned. What
    enforces the budget is every downstream call asking `for_call()` for its timeout, so
    a request runs out of things it is *allowed to start* rather than being killed part
    way through one. A 504 arrives from the first call that finds no budget.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach a deadline and report what was left.

        Args:
            scope: The ASGI scope. `state` is where the deadline is put, which is what
                `Request.state` reads.
            receive: Passed through untouched.
            send: Wrapped to add `X-Budget-Remaining-Ms`.

        The probe paths are passed through with no budget at all - see `_paths` on why
        `UNBUDGETED` exists as its own name. The header is for the operator reading a
        slow trace rather than for the client to act on: a client cannot spend a budget
        it does not own, and it is the cheapest way to see how much of `PRD.md` §6.1's
        80 ms a request actually used.
        """
        if not is_http(scope) or scope["path"] in UNBUDGETED:
            await self.app(scope, receive, send)
            return
        budget_s = scope["app"].state.gateway.settings.request_deadline_s
        deadline = Deadline.after(budget_s)
        scope.setdefault("state", {})["deadline"] = deadline
        remaining = add_header(send, "X-Budget-Remaining-Ms", f"{deadline.remaining_s * 1000:.0f}")
        await self.app(scope, receive, remaining)


class RequestId:
    """Give every request an id, and echo it back."""

    def __init__(self, app: ASGIApp) -> None:
        """Hold the inner app.

        Args:
            app: The rest of the stack.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Attach an id, then echo it on the way out.

        Args:
            scope: The ASGI scope.
            receive: Passed through untouched.
            send: Wrapped to add `X-Request-Id`.

        **A client-supplied id is honoured, and that is a deliberate trade.** It makes a
        caller's own logs correlate with ours, which is the whole point of the header.
        It also means the value is caller-controlled and must never be used as anything
        but a correlation label - never as a cache key, never in a SQL string, and never
        trusted to be unique.
        """
        if not is_http(scope):
            await self.app(scope, receive, send)
            return
        supplied = _header(scope, REQUEST_ID_HEADER)
        request_id = supplied[:_MAX_ID] if supplied else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        await self.app(scope, receive, add_header(send, REQUEST_ID_HEADER, request_id))


def _header(scope: Scope, name: str) -> str:
    """One request header, case-insensitively.

    Args:
        scope: The ASGI scope.
        name: The header to find.

    Returns:
        The value, stripped, or `""` when absent.

    ASGI hands headers over as a list of lowercase byte pairs rather than a mapping, so
    this is the lookup `Request.headers` would have done. Decoding with `latin-1` is
    what the HTTP spec says header bytes are, and it cannot raise - a `utf-8` decode of
    a malformed header would, which would turn a client's bad byte into a 500.
    """
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key == wanted:
            decoded: str = value.decode("latin-1").strip()
            return decoded
    return ""
