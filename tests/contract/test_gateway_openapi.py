"""The gateway's published spec, and what a generated client would believe.

BUILD_NOTEBOOK.md S8.1

    "`uvicorn gateway.main:app` serves `/healthz`, `/docs` shows the OpenAPI
    schema, and the schemathesis contract suite runs green against the published
    spec (RULES 5, `contract` suite)."

`tests/contract/` already holds the MCP half of this - `test_tool_schemas.py`
checks the schemas a client reads once and then holds. This is the REST half, and
the thing it protects is the same: a published schema is a promise made to code
nobody in this repository wrote.

**Schemathesis drives the app rather than a URL.** Its ASGI integration runs the
generated cases in-process against the app object, so the suite needs no port, no
`uvicorn` and no free socket - which is what lets it run in the `gates` CI job
beside the unit tests instead of needing the integration job's Docker.

**Nothing here starts the lifespan, and that is the load-bearing decision.**
`TestClient` runs an app's lifespan only when it is used as a context manager, and
this module deliberately does not use one: lifespan opens a Postgres pool and
pings Redis, so `with TestClient(app)` would make every assertion about a
published *document* depend on a running datastore. It would pass on a developer
machine with `make dev` up and fail in the `gates` job, which is the
green-locally-red-in-CI failure `ci.yml` was written to avoid.

**So only `/healthz` is exercised by the generated cases.** It takes no dependency
and touches nothing external, by design - see `routers/health.py`. `/readyz`
probes both datastores, so a generated case against it would be asserting that
Postgres is up; its 503 is a documented response and belongs to a test with a
container, which is S8.2's integration work rather than this suite's.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
import schemathesis
from fastapi import FastAPI
from gateway.lifespan import SERVICE
from gateway.main import build_app
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def _no_resources(_app: FastAPI) -> AsyncIterator[None]:
    """A lifespan that owns nothing.

    Args:
        _app: Ignored. Nothing is put on `app.state`, because no route reached
            from this module reads it.

    Yields:
        None.

    `build_app` takes this so the suite can exercise the published schema without
    a datastore - see that function's `lifespan` argument for the whole argument.
    An ASGI transport enters the app's lifespan on every call, so without this the
    generated cases open a Postgres pool to check a JSON document.
    """
    yield


# Built once, with no resources. `build_app()` allocates nothing either way -
# resources open when the app is *entered* - so the cost here is FastAPI's route
# table, and every case reads the same schema, which is the object under test.
_APP: FastAPI = build_app(lifespan=_no_resources)

# `from_dict` over the app's own `openapi()`, then `app` set so the transport is
# ASGI. NOT `from_asgi("/openapi.json", app)`, which is the obvious spelling and
# is wrong here: it *fetches* the document through the ASGI stack, and that runs
# the lifespan at import time - so collecting this module opened a Postgres pool
# and the whole file errored on a machine with the dev stack down. Reading
# `app.openapi()` is also the same object `/docs` renders, so nothing is lost.
schema = schemathesis.openapi.from_dict(_APP.openapi())
schema.app = _APP


def _client() -> TestClient:
    """A client that does NOT run the app's lifespan.

    Returns:
        A `TestClient` over the module's app.

    Constructed rather than entered, deliberately - see the module docstring.
    `TestClient.__enter__` is what runs lifespan, so calling the client directly
    exercises the routes with `app.state.gateway` unset. Every route reached from
    this module is one that takes no `GatewayDep`, so nothing reads it.
    """
    return TestClient(_APP)


@schema.include(path="/healthz").parametrize()
def test_the_published_spec_describes_what_the_app_returns(
    case: schemathesis.Case[Any],
) -> None:
    """Every generated case against `/healthz` conforms to the published schema.

    Args:
        case: One generated request, from schemathesis.

    This is the check that catches a response model changed without the schema
    being regenerated, a status code returned but never declared, and a
    `Content-Type` that does not match what the document says. None of those fail
    a handler test, because a handler test asserts what the handler returns rather
    than what the schema claimed it would.
    """
    case.call_and_validate()


class TestTheDocumentItselfIsServable:
    """What `/docs` needs in order to render, checked without a browser."""

    def test_the_schema_is_openapi_3_and_names_this_service(self) -> None:
        """`/docs` renders from `/openapi.json`, so a 200 there is the check.

        Asserting the title as well because it is what a reader sees first in the
        rendered page, and `SERVICE` is the one string this process identifies
        itself by - in spans, in logs and here. Three readers of one constant is
        the reason it is a constant.
        """
        document = _client().get("/openapi.json")

        assert document.status_code == 200
        body = document.json()
        assert body["openapi"].startswith("3.")
        assert body["info"]["title"] == SERVICE

    def test_docs_is_served_and_redoc_is_not(self) -> None:
        """S8.1 asks for `/docs`; the second renderer is deliberately off.

        FastAPI publishes ReDoc at `/redoc` by default over the same schema, and
        `build_app` passes `redoc_url=None`. Pinned because "we only serve the one
        the step named" is otherwise a claim nothing checks, and an unused
        documentation surface is one more path to keep reachable and reason about
        under auth.

        **`/redoc` answers 401 rather than 404 since S8.2, and the assertion is
        `!= 200` for that reason.** `middleware._UNAUTHENTICATED` lists the four
        paths that may be reached without a credential, and an unknown path is not
        among them - so an anonymous request is refused before routing decides
        there is nothing there. That is the better order: a 404 from an
        unauthenticated caller tells them which paths exist, and path enumeration
        is reconnaissance. An authenticated caller still gets a 404, which is the
        answer that is actually useful to them.
        """
        client = _client()

        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code != 200


class TestEveryRouterS81NamesIsRegistered:
    """S8.1's router list, checked as a list rather than trusted."""

    def test_the_six_routers_are_all_mounted(self) -> None:
        """`health`, `memory`, `review`, `policy`, `audit`, `telemetry`.

        Five carry no endpoints yet, so this cannot be checked through the
        published paths - an empty router contributes nothing to the document.
        It is checked through `ROUTERS`, which is what `build_app` iterates, so
        the assertion is over the thing that would actually be wrong: a router
        written and never added to the tuple.
        """
        from gateway.routers import ROUTERS

        assert len(ROUTERS) == 6

    @pytest.mark.parametrize("path", ["/healthz", "/readyz"])
    def test_the_health_endpoints_are_published(self, path: str) -> None:
        """Both, because only one of them is in the step's DONE WHEN.

        `/readyz` is this module's own addition - see `routers/health.py` for why
        liveness and readiness must not be the same endpoint - and an endpoint
        added without being published is an endpoint no deployment can be
        configured to read.
        """
        assert path in _APP.openapi()["paths"]
