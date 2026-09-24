"""One tenant cannot read another's, on every surface.  BUILD_NOTEBOOK.md S8.2

    "a token for tenant A cannot read tenant B's assertions through REST, MCP, or
    the SDK. All three must fail."

**What each of the three clauses means here, because they are not symmetrical.**

- **REST** carries a token, so the clause is literal: same process, same query,
  two credentials, and the wrong one sees nothing. `TestTheRestSurface`.
- **MCP carries no token at all.** `mcp_server` speaks stdio to one client and
  takes its tenant from `GM_MCP_TENANT_ID` - `settings.py` says so at length, and
  says it exists "because authentication does not". So there is no token to swap
  and the clause cannot be tested as written. What *can* be tested, and is, is the
  binding the MCP surface actually has: the tenant-bound `PgVectorStore` its tools
  read through. `TestTheBindingBothSurfacesShare` covers it, and it is the same
  object the REST path builds per request, which is why one test serves both.
- **The SDK does not exist.** `PROJECT_TREE.md` places it at
  `packages/guardmem-sdk-python/` and nothing has built it, so the clause is not
  satisfiable at S8.2. Recorded as an explicit skip rather than quietly dropped -
  see `TestTheSdkSurface`.

**Why this needs a real Postgres.** The isolation under test is not application
code. `queries.py` contains no tenant predicate at all - grep it - so a search
issues SQL that would happily return every tenant's rows. What stops it is the
`tenant_isolation` policy `0001_initial` puts on `assertion`, evaluated by
Postgres against `app.tenant_id`. A test with a fake store would assert that a
fake filters, which is a statement about the fake.

**And `FORCE ROW LEVEL SECURITY` is why the fixtures use two roles.** The
migration applies `FORCE`, so even the table owner is subject to its own
policies. `owner` seeds and reveals; everything under test runs as
`guardmem_app`, which is the role a deployment uses.

The positive case in each class is not padding. A cross-tenant read returning
nothing is indistinguishable from a query that was broken, a namespace that was
wrong, or an embedder that matched nothing - so each surface asserts that tenant
A *can* see its own row before asserting that tenant B cannot.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import pytest
from fastapi import FastAPI
from gateway.auth import SettingsAuthBackend
from gateway.lifespan import GatewayState
from gateway.main import build_app
from pydantic import SecretStr
from starlette.testclient import TestClient

from fixtures.assertions import NS
from fixtures.mcp import settings as build_settings
from fixtures.pgvector import TIMEOUT_S, assertion, write_and_reveal
from guardmem_core.memory.vector.hash_embedder import HashEmbedder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from guardmem_core.memory.vector.pgvector_store import PgVectorStore
    from guardmem_core.settings import Settings

# The namespace both tenants use, taken from the shared builder rather than
# retyped: `fixtures.assertions.assertion` writes into `NS` and takes no namespace
# argument, so a literal here that drifted from it would make every search miss
# and every isolation assertion pass for the wrong reason.
NAMESPACE: Final = str(NS)

KEY_A: Final = "key-for-tenant-a-0001"
KEY_B: Final = "key-for-tenant-b-0002"


def _settings_with_keys(tenant_a: str, tenant_b: str) -> Settings:
    """Settings carrying one key per tenant, isolated from the developer's `.env`.

    Args:
        tenant_a: The tenant that owns the seeded row.
        tenant_b: The tenant that must not see it.

    Returns:
        A validated `Settings` whose `gateway_api_keys` names both.

    Built through `fixtures.mcp.settings`, which passes `_env_file=None`. That is
    how every other test in this repository builds one and it matters here: a
    populated local `.env` could supply a third key, or a different
    `store_timeout_s`, and the test would then pass or fail on the machine's
    configuration rather than on the policy under test.
    """
    grants = {
        KEY_A: {"tenant": tenant_a, "scopes": ["memory:read"]},
        KEY_B: {"tenant": tenant_b, "scopes": ["memory:read"]},
    }
    return build_settings(gateway_api_keys=SecretStr(json.dumps(grants)))


@pytest.fixture
async def gateway(pool: asyncpg.Pool, tenancy: dict[str, str]) -> AsyncIterator[FastAPI]:
    """The real gateway app over the real pool, with a key for each tenant.

    Args:
        pool: The `guardmem_app` pool - the role a deployment runs as, not the
            owner, because `FORCE ROW LEVEL SECURITY` makes that distinction the
            whole point.
        tenancy: Two tenant ids and an entity.

    Yields:
        The application, with `app.state.gateway` populated from these fixtures.

    **A hand-built `GatewayState` rather than the real lifespan.** The real one
    opens its own pool, and the test needs the app to share the *fixture's* pool -
    that is what keeps the seeded row and the read in one database with one
    lifetime. Everything the request path actually touches is real: the pool, the
    RLS policies, the store, the embedder, and the auth backend parsing a real
    key set.
    """
    settings = _settings_with_keys(tenancy["tenant"], tenancy["other"])

    @asynccontextmanager
    async def _state(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = GatewayState(
            settings=settings,
            pool=pool,
            redis=None,  # type: ignore[arg-type]
            http={},
            tracer=None,  # type: ignore[arg-type]
            embedder=HashEmbedder(),
            auth=SettingsAuthBackend(settings),
        )
        yield

    yield build_app(lifespan=_state)


@pytest.fixture
async def seeded(store: PgVectorStore, owner: asyncpg.Connection, tenancy: dict[str, str]) -> str:
    """One assertion belonging to tenant A, visible to tenant A.

    Args:
        store: Bound to tenant A by the `tenancy` fixture.
        owner: Used to reveal the row, since `FORCE` applies to the owner too.
        tenancy: The ids.

    Returns:
        The text the assertion was embedded from, so a search can match it.

    `write_and_reveal` is the existing helper for exactly this - writing through
    the app role and then making the row visible for assertions - so this does not
    re-implement the seeding the integration suite already does.
    """
    written = assertion(tenancy)
    await write_and_reveal(store, owner, written)
    return str(written.object)


class TestTheRestSurface:
    """`GET /memory/search`, which is the REST read S8.2 names."""

    async def test_tenant_a_can_read_its_own_assertion(self, gateway: FastAPI, seeded: str) -> None:
        """The control. Without it, the refusal below proves nothing.

        A cross-tenant read returning zero hits looks identical to a broken query,
        a wrong namespace, or an embedder that matched nothing - so the suite has
        to show the row is reachable at all.
        """
        with TestClient(gateway) as client:
            response = client.get(
                "/memory/search",
                params={"q": seeded, "namespace": NAMESPACE},
                headers={"Authorization": f"Bearer {KEY_A}"},
            )

        assert response.status_code == 200
        assert response.json()["hits"], "tenant A cannot see its own row; the test is vacuous"

    async def test_tenant_b_cannot_read_tenant_as_assertion(
        self, gateway: FastAPI, seeded: str
    ) -> None:
        """S8.2's DONE WHEN, for REST.

        Same query, same namespace, same running process - only the credential
        differs, and the credential is the only thing that decides the tenant.

        **200 with no hits, not 403.** RLS makes another tenant's rows *invisible*
        rather than forbidden, and that is the correct semantics to expose: a 403
        would confirm that something existed to be refused, which is itself a
        cross-tenant disclosure.
        """
        with TestClient(gateway) as client:
            response = client.get(
                "/memory/search",
                params={"q": seeded, "namespace": NAMESPACE},
                headers={"Authorization": f"Bearer {KEY_B}"},
            )

        assert response.status_code == 200
        assert response.json()["hits"] == []

    async def test_no_credential_reads_nothing(self, gateway: FastAPI, seeded: str) -> None:
        """The unauthenticated case, which must not fall back to any tenant.

        A default tenant is the same breach with a more plausible cause, so the
        absence of a credential is a 401 rather than an empty result from some
        configured default.
        """
        with TestClient(gateway) as client:
            response = client.get("/memory/search", params={"q": seeded, "namespace": NAMESPACE})

        assert response.status_code == 401

    async def test_a_tenant_cannot_be_named_in_the_request(
        self, gateway: FastAPI, seeded: str, tenancy: dict[str, str]
    ) -> None:
        """Sending somebody else's tenant id must change nothing.

        The endpoint takes no tenant parameter, so an extra query string key is
        ignored rather than honoured. Asserted because "there is no such
        parameter" is a property that a later commit could remove by accident
        while every other test here still passed.
        """
        with TestClient(gateway) as client:
            response = client.get(
                "/memory/search",
                params={
                    "q": seeded,
                    "namespace": NAMESPACE,
                    "tenant_id": tenancy["tenant"],
                    "tenant": tenancy["tenant"],
                },
                headers={"Authorization": f"Bearer {KEY_B}"},
            )

        assert response.status_code == 200
        assert response.json()["hits"] == []


class TestTheBindingBothSurfacesShare:
    """The RLS boundary itself, with no HTTP layer in the way.

    REST and MCP both reach Postgres through a tenant-bound `PgVectorStore`, and
    for MCP that binding *is* the whole isolation story - it has no credential to
    check, only `GM_MCP_TENANT_ID`. So this class is the MCP clause as well as the
    foundation of the REST one.

    Testing it directly also localises a failure: red here is a policy or a
    `set_config` problem, red in `TestTheRestSurface` with this green is gateway
    wiring.
    """

    async def test_a_store_bound_to_the_other_tenant_sees_nothing(
        self,
        pool: asyncpg.Pool,
        tenancy: dict[str, str],
        seeded: str,
    ) -> None:
        """A second store, same pool, different tenant.

        This is what `dependencies.vector_store` builds per request, so it is the
        object whose tenant binding is the security boundary.
        """
        from guardmem_core.memory.vector.pgvector_store import PgVectorStore
        from guardmem_core.types import Namespace, TenantId

        embedder = HashEmbedder()
        other = PgVectorStore(
            pool, embedder, tenant_id=TenantId(tenancy["other"]), timeout_s=TIMEOUT_S
        )

        found = await other.search(
            namespace=Namespace(NAMESPACE),
            embedding=(await embedder.embed([seeded]))[0],
            k=10,
            filters={},
        )

        assert found == []


class TestTheSdkSurface:
    """S8.2's third clause, and why it is not satisfied here.

    "a token for tenant A cannot read tenant B's assertions through REST, MCP, or
    the SDK" names three surfaces. The SDK is `packages/guardmem-sdk-python/` in
    `PROJECT_TREE.md` and does not exist in this repository, so there is nothing
    to authenticate against and nothing to refuse.

    This class is deliberately a failing-to-exist marker rather than an absence.
    A DONE WHEN with a clause nobody can satisfy should be visible in the suite
    that claims to cover it: the step that builds the SDK adds its case here, and
    until then `make test-integration` reports one skip that names the reason.
    """

    @pytest.mark.skip(reason="packages/guardmem-sdk-python does not exist yet; see PROJECT_TREE")
    def test_a_tenant_a_token_cannot_read_tenant_b_through_the_sdk(self) -> None:
        """Placeholder for the SDK's own client."""
        raise AssertionError("unreachable while skipped")
