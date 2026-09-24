"""The request chain, and the order S8.2 specifies.  BUILD_NOTEBOOK.md S8.2

    "Middleware order matters: `request_id -> auth -> tenancy -> ratelimit ->
    body_hash`."

It matters, so it is asserted. The order lives in one function that adds five
layers, and Starlette applies them in reverse of the order they are added - a
chain whose order is only a comment is a chain that will be reordered by
somebody adding a layer at the bottom of the list because that is where the
cursor was.

**No datastore, and no lifespan.** These are unit tests: every case here either
hits an unauthenticated path or is refused before a handler runs, and the two
that need a principal install a fake backend on `app.state` directly. The
cross-tenant read that needs a real Postgres and real RLS is
`tests/security/test_tenant_isolation.py`, which is S8.2's actual DONE WHEN.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI, Request
from gateway.auth import InvalidCredentialError, Principal, SettingsAuthBackend, credential_from
from gateway.main import build_app
from gateway.middleware import REQUEST_ID_HEADER
from pydantic import SecretStr
from starlette.testclient import TestClient

from guardmem_core.types import TenantId

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TENANT_A = TenantId("11111111-1111-4111-8111-111111111111")
KEY_A = "key-a-0000000000"


class _Backend:
    """One key, one tenant. Satisfies `AuthBackend` structurally."""

    def principal_for(self, credential: str) -> Principal:
        """Resolve the single configured key.

        Args:
            credential: The presented bearer token.

        Returns:
            The principal for `KEY_A`.

        Raises:
            InvalidCredentialError: anything else.
        """
        if credential != KEY_A:
            raise InvalidCredentialError("unknown key")
        return Principal(tenant_id=TENANT_A, key_id=credential[:8], scopes=frozenset({"x:read"}))


class _Unreachable:
    """A datastore that is honestly down.

    `/readyz` probes Postgres and Redis, and these tests have neither. Raising
    `OSError` is what a closed socket raises, so the readiness handler takes its
    real `_UNREACHABLE` branch and answers 503 - rather than an `AttributeError`
    from a stub with no `pool`, which would escape as a 500 and make the test pass
    or fail for reasons unrelated to the chain.
    """

    async def fetchval(self, *_args: object, **_kwargs: object) -> object:
        """Fail as an unreachable Postgres does.

        Raises:
            OSError: always.
        """
        raise OSError("no database in a unit test")

    async def ping(self) -> bool:
        """Fail as an unreachable Redis does.

        Raises:
            OSError: always.
        """
        raise OSError("no redis in a unit test")


@dataclass(frozen=True)
class _State:
    """Just the fields the chain and the health router read."""

    auth: _Backend
    pool: _Unreachable
    redis: _Unreachable


def _app() -> FastAPI:
    """An app with the chain installed and a fake auth backend.

    Returns:
        The application, with `app.state.gateway` carrying only what the
        middleware and the health router read.

    The lifespan is replaced with one that opens nothing - see `build_app`'s
    `lifespan` argument - and `app.state.gateway` is a stand-in. No pool, no
    Redis, no tracer: nothing here reaches a handler that needs one.
    """

    @asynccontextmanager
    async def _no_resources(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = _State(_Backend(), _Unreachable(), _Unreachable())
        yield

    return build_app(lifespan=_no_resources)


def _chain(app: FastAPI) -> list[str]:
    """The installed middleware, outermost first.

    Args:
        app: The application to inspect.

    Returns:
        Class names in execution order.

    Starlette types `Middleware.cls` as `_MiddlewareFactory`, a callable protocol
    with no `__name__`, so the attribute is read through `getattr`. It is always a
    class here because `add_middleware` is only ever given one.
    """
    return [str(getattr(layer.cls, "__name__", layer.cls)) for layer in app.user_middleware]


class TestTheChainRunsInTheDocumentedOrder:
    """S8.2's sentence, as an assertion."""

    def test_the_five_layers_are_in_s8_2_order(self) -> None:
        """`request_id -> auth -> tenancy -> ratelimit -> body_hash`.

        Read off `user_middleware`, which Starlette lists outermost-first - so
        this list is execution order. `apply` adds them reversed, and this is the
        test that says the reversal is right rather than merely present.
        """
        names = _chain(build_app())

        assert names == ["RequestId", "Authenticate", "Tenancy", "RateLimit", "BodyHash"]

    def test_auth_runs_before_tenancy(self) -> None:
        """Stated separately because it is the one whose reversal is a breach.

        A tenancy layer that ran first would have to take the tenant from
        something the caller sent, which is the cross-tenant read this service
        exists to prevent. The test above would catch a reorder; this one says
        why that particular reorder matters.
        """
        names = _chain(build_app())

        assert names.index("Authenticate") < names.index("Tenancy")


class TestRequestId:
    """A correlation id on every response, including failures."""

    def test_a_generated_id_is_echoed(self) -> None:
        """No header in, an id out."""
        with TestClient(_app()) as client:
            response = client.get("/healthz")

        assert response.headers[REQUEST_ID_HEADER]

    def test_a_supplied_id_is_honoured(self) -> None:
        """So a caller's own logs correlate with ours."""
        with TestClient(_app()) as client:
            response = client.get("/healthz", headers={REQUEST_ID_HEADER: "abc123"})

        assert response.headers[REQUEST_ID_HEADER] == "abc123"

    def test_a_supplied_id_is_truncated(self) -> None:
        """It is caller-controlled and reaches every log line for this request.

        Unbounded, one request could put a megabyte into the log for each of
        several lines. 64 characters is longer than any id worth correlating.
        """
        with TestClient(_app()) as client:
            response = client.get("/healthz", headers={REQUEST_ID_HEADER: "z" * 500})

        assert len(response.headers[REQUEST_ID_HEADER]) == 64

    def test_a_refused_request_still_carries_one(self) -> None:
        """The reason `request_id` is first.

        A correlation id added after the first thing that can fail is missing
        from the failures most worth correlating - and a 401 is exactly the line
        somebody greps for.
        """
        with TestClient(_app()) as client:
            response = client.get("/memory/search?q=x&namespace=n")

        assert response.status_code == 401
        assert response.headers[REQUEST_ID_HEADER]


class TestAuthentication:
    """What reaches a handler, and what does not."""

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({}, id="absent"),
            pytest.param({"Authorization": "Basic abc"}, id="wrong-scheme"),
            pytest.param({"Authorization": "Bearer "}, id="empty-token"),
            pytest.param({"Authorization": "Bearer nope"}, id="unknown-key"),
        ],
    )
    def test_every_credential_failure_is_the_same_401(self, headers: dict[str, str]) -> None:
        """One body for four different failures, deliberately.

        Absent, malformed and unknown are distinguishable server-side and are
        distinguished in the log. Telling them apart in the *response* is how a
        caller enumerates valid keys, so the body is identical.
        """
        with TestClient(_app()) as client:
            response = client.get("/memory/search?q=x&namespace=n", headers=headers)

        assert response.status_code == 401
        assert response.json() == {"detail": "invalid or missing credential"}

    def test_a_401_carries_www_authenticate(self) -> None:
        """RFC 7235 requires it, and clients use it to decide what to send."""
        with TestClient(_app()) as client:
            response = client.get("/memory/search?q=x&namespace=n")

        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("path", ["/healthz", "/readyz", "/openapi.json", "/docs"])
    def test_the_unauthenticated_paths_need_no_credential(self, path: str) -> None:
        """A process that could not report its own liveness without a key could
        not be monitored by anything that does not hold one.

        `/readyz` answers 503 here rather than 200 - there is no pool behind this
        app - and that is still not a 401, which is what this asserts.
        """
        with TestClient(_app()) as client:
            assert client.get(path).status_code != 401

    def test_the_scheme_is_case_insensitive(self) -> None:
        """RFC 7235 says it is, and rejecting `Bearer` while accepting `bearer`
        would be a support ticket rather than a security control."""
        assert credential_from("Bearer abc") == "abc"
        assert credential_from("bearer abc") == "abc"
        assert credential_from("BEARER abc") == "abc"


class TestTheSettingsBackend:
    """Parsing `GM_GATEWAY_API_KEYS`, and refusing to guess."""

    def _backend(self, value: str) -> SettingsAuthBackend:
        """Build a backend over one configured value.

        Args:
            value: What `GM_GATEWAY_API_KEYS` would hold.

        Returns:
            The backend.
        """
        settings = type("_S", (), {"gateway_api_keys": SecretStr(value)})()
        return SettingsAuthBackend(settings)

    def test_an_empty_value_authenticates_nobody_and_does_not_raise(self) -> None:
        """The gateway must still start. `/healthz` needs no principal, and a
        process that refused to boot without keys could not report its own
        liveness - so "no keys" is a valid configuration that 401s everything."""
        backend = self._backend("")

        with pytest.raises(InvalidCredentialError):
            backend.principal_for("anything")

    def test_a_configured_key_resolves_to_its_tenant_and_scopes(self) -> None:
        """The whole point: a credential names a tenant."""
        backend = self._backend(
            json.dumps({KEY_A: {"tenant": str(TENANT_A), "scopes": ["memory:read"]}})
        )

        resolved = backend.principal_for(KEY_A)

        assert resolved.tenant_id == TENANT_A
        assert resolved.permits("memory:read")
        assert not resolved.permits("memory:write")

    def test_the_key_id_is_not_the_key(self) -> None:
        """`key_id` reaches logs and the audit trail, so it must not be usable.

        Eight characters identifies which credential was used among a handful
        without being one.
        """
        backend = self._backend(json.dumps({KEY_A: {"tenant": str(TENANT_A)}}))

        resolved = backend.principal_for(KEY_A)

        assert resolved.key_id == KEY_A[:8]
        assert resolved.key_id != KEY_A

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("{not json", id="malformed-json"),
            pytest.param('["a"]', id="not-an-object"),
            pytest.param('{"k": "just-a-string"}', id="grant-not-an-object"),
            pytest.param('{"k": {"scopes": []}}', id="no-tenant"),
            pytest.param('{"k": {"tenant": "t", "scopes": "read"}}', id="scopes-not-a-list"),
        ],
    )
    def test_a_malformed_key_set_raises_at_construction(self, value: str) -> None:
        """At startup, where an operator is reading the output.

        Deferring it to the first request would turn a configuration mistake into
        a 500 on traffic, which reads as a client problem.
        """
        with pytest.raises(ValueError, match=r"GM_GATEWAY_API_KEYS|tenant|scopes"):
            self._backend(value)

    def test_no_error_message_echoes_the_configured_value(self) -> None:
        """The value is a set of credentials. A `ValueError` at startup goes to a
        log, and a log that contains the keys is the leak `SecretStr` exists to
        prevent one spelling of."""
        # The `secret =` assignment trips the keyword detector whatever the
        # value is, so it is marked rather than disguised - the same call
        # `fixtures/mcp.py` makes for its unused Neo4j password. Renaming the
        # variable to dodge the scanner would be hiding the pattern the
        # scanner exists to find.
        secret = "super-secret-key-material"  # pragma: allowlist secret

        with pytest.raises(ValueError) as caught:
            self._backend(json.dumps({secret: {"scopes": []}}))

        assert secret not in str(caught.value)


class TestBodyHash:
    """One hash of one body, for idempotency and the audit chain."""

    def test_a_get_is_not_hashed(self) -> None:
        """A GET carries no body, so hashing it would be paying for a hash of
        nothing on the most common request shape."""
        app = _app()
        seen: dict[str, Any] = {}

        # `request: Request`, not `Any`. FastAPI reads the annotation to decide
        # what a parameter is, so `Any` makes `request` a query parameter and the
        # call 422s without ever reaching the body.
        @app.get("/probe-get")
        async def probe(request: Request) -> dict[str, str]:
            seen["hash"] = request.state.body_hash
            return {}

        with TestClient(app) as client:
            client.get("/probe-get", headers={"Authorization": f"Bearer {KEY_A}"})

        assert seen["hash"] is None
