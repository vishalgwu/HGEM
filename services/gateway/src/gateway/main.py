"""The ASGI application.  BUILD_NOTEBOOK.md S8.1

S8.1's DONE WHEN: "`uvicorn gateway.main:app` serves `/healthz`, `/docs` shows
the OpenAPI schema, and the schemathesis contract suite runs green against the
published spec." `app` is therefore published interface - the module path and the
attribute name are both in a command an operator types - and `build_app` exists
beside it so a test can have an app without the process-global one.

**`build_app()` is a factory and `app` is one call of it.** The instance has to
exist at module level because the step's own command names it, but a test that
could *only* use that instance would be sharing one app across the suite, and two
tests wanting different settings could not both have them. The factory is also
what `uvicorn --factory` wants if a deployment ever needs its workers to build
their own.

**Routers are registered from `ROUTERS` rather than one call each.** See
`routers/__init__.py` - a router registered by hand is one somebody can forget,
or register after the middleware meant to cover it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI

from gateway.lifespan import SERVICE, lifespan
from gateway.routers import ROUTERS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.types import Lifespan

__all__ = ["app", "build_app"]

# One sentence, shown at the top of `/docs`. Kept here rather than in a settings
# field because it describes what this service *is*, which no deployment should
# be able to disagree about.
_SUMMARY: Final = "Governed memory for AI agents - REST ingress."

_DESCRIPTION: Final = """\
Production ingress for the GuardMem AI decision engine.

Every write is governed before it lands: extracted with provenance, validated
against the ontology, scored for confidence and risk, and recorded in a
hash-chained audit log. See `ARCHITECTURE.md` for the three-layer pipeline this
surface fronts, and `ADR-0005` for why MCP rather than this is the primary agent
surface.
"""


def build_app(*, lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    """Assemble the application.

    Args:
        lifespan: What owns the process's resources. Defaults to the real one.
            Overridden by tests that exercise the *published schema* rather than
            the running service - the contract suite reads
            `/openapi.json` and drives `/healthz`, and both go through an ASGI
            transport that enters the app's lifespan, which would open a Postgres
            pool and ping Redis to check a document. That test then passes only on
            a machine with `make dev` up and fails in the `gates` CI job, which is
            the green-locally-red-in-CI failure `ci.yml` exists to prevent.

            A parameter rather than a monkeypatch because the seam is real: what
            the app *is* and what the process *owns* are separable, and this is
            the line between them.

    Returns:
        A `FastAPI` instance with every router registered and a lifespan attached,
        but nothing started - the resources open when the app is entered, not
        when it is built.

    **`redoc_url=None`.** FastAPI publishes both Swagger UI and ReDoc by default
    over the same schema. S8.1 asks for `/docs` and a second renderer is a second
    surface to keep reachable, so only the one the step names is served.

    The `/openapi.json` path is left at its default deliberately: the contract
    suite reads the schema from there, and moving it would make the suite's
    configuration a thing that can disagree with the app.
    """
    app = FastAPI(
        title=SERVICE,
        summary=_SUMMARY,
        description=_DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan or _lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    for router in ROUTERS:
        app.include_router(router)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Adapt `gateway.lifespan` to what Starlette expects.

    Args:
        app: The application being started. Its `state` is where the yielded
            resources are put.

    Yields:
        None. Starlette treats a lifespan's yielded value as a state mapping to
        merge into `app.state`, which would scatter the fields of `GatewayState`
        across it as untyped attributes. Putting the dataclass on `app.state`
        under one name keeps it a single typed object - see
        `dependencies.gateway_state`, which is the only reader.
    """
    async with lifespan(app) as state:
        app.state.gateway = state
        yield


# The instance `uvicorn gateway.main:app` serves.
app: Final = build_app()
