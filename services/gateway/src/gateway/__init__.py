"""GuardMem AI's REST gateway.  BUILD_NOTEBOOK.md S8.1

Production ingress for the decision engine: `PHASES_AND_ROADMAP.md` §2's goal is
"production ingress with model routing, fallback, security armor, and budget
control", and this package is where a request enters.

**Not the primary agent surface.** `ADR-0005` makes MCP that, and this exists for
the callers MCP does not serve - a dashboard BFF, a service integration, anything
speaking HTTP. Both surfaces front the same `guardmem_core` pipeline, which is
what `ARCHITECTURE.md` §2.2 means by the engine running identically in the
gateway, the worker and the eval harness.

What ships at S8.1 is the skeleton: the process starts, opens its resources,
serves `/healthz` and `/readyz`, and publishes an OpenAPI document. Auth, tenancy
and RLS context are S8.2; rate limiting and idempotency are S8.3; the async path
is S8.4. The five routers those steps fill are declared and empty - see
`routers/__init__.py`.

`main.py` owns the application, `lifespan.py` owns the process's resources, and
they are separate because they fail differently: a handler bug is a bad response
and a lifespan bug is a process that will not start, and the second is the one an
operator reads at three in the morning.
"""

from gateway.dependencies import GatewayDep, gateway_state
from gateway.lifespan import SERVICE, GatewayState, lifespan
from gateway.main import app, build_app

__all__ = [
    "SERVICE",
    "GatewayDep",
    "GatewayState",
    "app",
    "build_app",
    "gateway_state",
    "lifespan",
]
