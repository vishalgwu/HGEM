"""The gateway's routers, in the order S8.1 names them.  BUILD_NOTEBOOK.md S8.1

S8.1: "Routers: `health`, `memory`, `review`, `policy`, `audit`, `telemetry`.
Routers contain no logic - validate, call one core function, shape response
(RULES 2.4)."

`ROUTERS` is that list as data rather than six `include_router` calls in
`main.py`. The difference matters once middleware and auth arrive: a router
registered by hand is a router somebody can forget to register, or register after
the middleware that was supposed to cover it, and neither mistake fails a test
that only checks the endpoints it knows about.

Only `health` carries endpoints today. The other five are declared and empty,
each naming the step that fills it - see their module docstrings for why an empty
router is the honest state rather than a stub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from gateway.routers import audit, health, memory, policy, review, telemetry

if TYPE_CHECKING:
    from fastapi import APIRouter

__all__ = ["ROUTERS"]

# Order is S8.1's own, and `health` is first on purpose: it is the only one with
# no prefix, so listing it first keeps the OpenAPI document's tag order stable
# for the contract suite to read.
ROUTERS: Final[tuple[APIRouter, ...]] = (
    health.router,
    memory.router,
    review.router,
    policy.router,
    audit.router,
    telemetry.router,
)
