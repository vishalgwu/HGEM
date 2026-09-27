"""The request chain, and the order it runs in.  BUILD_NOTEBOOK.md S8.2, ADR-0012

S8.2: "Middleware order matters: `request_id -> auth -> tenancy -> ratelimit ->
body_hash`." It does, and each boundary is one of these reasons:

0. **`RequestDeadline` is outside all five**, added for ADR-0012 rather than by
   S8.2. Those five are peers in a sequence; a budget is a wrapper around the
   sequence, and one that started after authentication would not cover it.
1. **`request_id` first** so every line logged about a request - including the 401
   from the next layer - carries the same id. A correlation id added after the
   first thing that can fail is missing from the failures most worth correlating.
2. **`auth` before `tenancy`** because a tenant is a *property of* an authenticated
   principal. The reverse is the cross-tenant read this service exists to prevent:
   it would take a tenant from something the caller sent.
3. **`tenancy` before `ratelimit`** because S8.3's bucket is keyed by
   `tenant:api_key`, so the limiter cannot key anything until both are known.
4. **`ratelimit` before `body_hash`** because hashing is work, and work done for a
   request about to be refused is work done for an attacker.
5. **`body_hash` last** so it runs only for requests that will be handled. It feeds
   S8.3's idempotency key and the audit trail's `source_hash`.

**Starlette applies `add_middleware` in reverse**: the last added is the outermost.
`apply` adds them in reverse of the documented order so that the documented order
is the execution order, and the list is written once because a chain assembled by
six separate calls is a chain whose order is whatever the calls happen to be in.

**Pure ASGI, not `BaseHTTPMiddleware`, since S8.4.** That class cost a measured factor
of nine hundred under concurrency and made S8.4's p95 target unreachable; `_asgi.py`
carries the numbers. `apply` is unchanged in shape - `add_middleware` accepts any ASGI
class - so the order below still reads as the order it runs in.

**A package rather than one module since 2026-09-26.** It passed `RULES.md` 2.4's
400-line cap at six layers, and the seam is the one the order already implies:
framing that cannot refuse a request, access control that can, and throttling.
Splitting by layer group rather than one file per class keeps each boundary's
reasoning next to both sides of it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway.middleware.access import Authenticate, Tenancy
from gateway.middleware.identity import REQUEST_ID_HEADER, RequestDeadline, RequestId
from gateway.middleware.throttle import BodyHash, RateLimit

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "REQUEST_ID_HEADER",
    "Authenticate",
    "BodyHash",
    "RateLimit",
    "RequestDeadline",
    "RequestId",
    "Tenancy",
    "apply",
]


def apply(app: FastAPI) -> None:
    """Install the chain so that execution order matches the documented order.

    Args:
        app: The application to wrap.

    Starlette treats the last-added middleware as the outermost, so this iterates
    the documented order in reverse. `tests/unit/test_gateway_middleware.py` asserts
    the resulting order, because a chain whose order is only a comment is a chain
    that will be reordered by somebody adding a layer where the cursor happened to
    be.
    """
    app.add_middleware(BodyHash)
    app.add_middleware(RateLimit)
    app.add_middleware(Tenancy)
    app.add_middleware(Authenticate)
    app.add_middleware(RequestId)
    app.add_middleware(RequestDeadline)
