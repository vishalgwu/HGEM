"""Why these layers are pure ASGI, and the two patterns they share.  S8.4

**`BaseHTTPMiddleware` cost this service a factor of nine hundred, measured.** S8.4's
DONE WHEN is "locust run at 50 rps shows p95 < 80 ms on the async propose endpoint".
The first run reported **p50 330 ms, p95 440 ms** at 50.23 rps - five times over. A
single request measured 16 ms of server time and `/healthz` answered in 3 ms, so
service time was never the problem; the latency only appeared under concurrency.

Isolated with a micro-benchmark over one trivial POST route:

| stack | p50 | throughput |
|---|---|---|
| no middleware | 0.2 ms | 1859 rps |
| 6 x `BaseHTTPMiddleware` | **196.5 ms** | **217 rps** |
| 6 x pure ASGI | 0.22 ms | 2678 rps |

Redis was ruled out first: 50 concurrent `SET`+`GET` complete in 42 ms of wall time,
about 1200 ops/sec, against the ~150/sec the target load needs.

`BaseHTTPMiddleware` gives each request an anyio task group and a pair of memory
object streams so that `dispatch` can look like a function of request to response.
That is a real convenience and six of them nested is a different thing: every request
carries six task groups, and under concurrency the scheduling serialises.
Starlette's own documentation warns the class has performance limitations.

**So these layers are written against the ASGI interface directly.** The cost is that
a layer is no longer a function of request to response - it wraps `send`, or it does
not call the app at all - and the two helpers below are the patterns that recur.

Nothing here is a framework. Two functions and a type alias, because three modules
each needed the same six lines and the sixth one would have drifted.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping

    from starlette.types import Message, Receive, Scope, Send

__all__ = ["HeaderSetter", "add_header", "send_json"]

# What `add_header` produces: a `send` that a layer passes downstream in place of the
# real one. Named because the alternative is repeating the signature in three modules.
if TYPE_CHECKING:
    HeaderSetter = Callable[[Message], Awaitable[None]]
else:  # pragma: no cover - a runtime alias only `mypy` reads
    HeaderSetter = object

_UTF8: Final = "utf-8"


def add_header(send: Send, name: str, value: str) -> HeaderSetter:
    """Wrap `send` so the response gains one header.

    Args:
        send: The downstream `send`.
        name: Header name.
        value: Header value.

    Returns:
        A `send` to pass to the inner app.

    **Appended on `http.response.start` and nowhere else.** That is the only message
    carrying headers, and appending rather than replacing matters: a handler that set
    the same header deliberately should win, and a layer that overwrote it would be
    silently undoing the more specific decision.

    Headers are raw bytes in ASGI, so both halves are encoded here rather than at each
    call site - a `str` in that list is a `TypeError` from deep inside the server,
    which is a poor way to learn about a typo.
    """
    raw = (name.encode(_UTF8), value.encode(_UTF8))

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers: list[tuple[bytes, bytes]] = list(message.get("headers") or [])
            headers.append(raw)
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


async def send_json(
    send: Send,
    status: int,
    body: MutableMapping[str, object],
    headers: dict[str, str] | None = None,
) -> None:
    """Send a JSON response without calling the inner app.

    Args:
        send: The downstream `send`.
        status: The status code.
        body: The payload, serialised compactly.
        headers: Extra headers, e.g. `WWW-Authenticate` or `Retry-After`.

    This is what a refusing layer does instead of returning a `Response`. Writing the
    two messages by hand rather than reaching for `JSONResponse` keeps the layer free
    of Starlette's response machinery, which is most of what made the class it replaces
    expensive - and a refusal is the path that has to stay cheap, because it is the one
    an overloaded service takes most often.

    `content-length` is set because a response without it forces chunked encoding, and
    a chunked 401 is a second round trip to say no.
    """
    payload = json.dumps(body, separators=(",", ":")).encode(_UTF8)
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    for key, value in (headers or {}).items():
        raw.append((key.encode(_UTF8), value.encode(_UTF8)))
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": payload})


def buffered(receive: Receive) -> tuple[Receive, Callable[[], bytes]]:
    """Wrap `receive` so the body can be read once and still reach the handler.

    Args:
        receive: The downstream `receive`.

    Returns:
        A replacement `receive`, and a callable returning the bytes seen so far.

    **This is what `BodyHash` needs and what `BaseHTTPMiddleware` made look free.**
    Reading `await request.body()` in a layer consumes the stream, so the handler must
    be given it back - the base class did that by replaying through a memory stream,
    which is part of what cost the factor above.

    Here the layer does not read the body at all: it passes a `receive` that records
    each chunk on the way past. The handler's own read is the only read, and the hash
    is computed from what it saw. So the body crosses the boundary once, and a layer
    that wanted it never has to buffer a request that the handler was going to reject.
    """
    chunks: list[bytes] = []

    async def wrapped() -> Message:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(bytes(message.get("body") or b""))
        return message

    return wrapped, lambda: b"".join(chunks)


def is_http(scope: Scope) -> bool:
    """Whether this connection is the kind these layers apply to.

    Args:
        scope: The ASGI scope.

    Returns:
        True for `http`.

    Every layer needs this guard, and forgetting it is not a small bug: a lifespan or
    websocket scope has no `headers` and no `path`, so a layer that assumed `http`
    raises during *startup* and the process never serves anything. `BaseHTTPMiddleware`
    handled it, which is why the guard is easy to forget when moving off it.
    """
    return bool(scope["type"] == "http")
