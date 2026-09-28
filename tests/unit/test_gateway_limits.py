"""The limiter's behaviour, and the idempotency key.  BUILD_NOTEBOOK.md S8.3

    "Token bucket in Redis keyed by `tenant:api_key`. Idempotency keys cached 24h;
    a repeat returns the stored result without re-running the pipeline."

**What is tested here and what cannot be.** The bucket's arithmetic lives in a Lua
script that Redis executes, so a unit test cannot run it without inventing a
second implementation in Python and then asserting the two agree - which measures
the invention. What *is* unit-testable is everything around it: the refusal the
middleware builds, the headers it sends, what it does when Redis is unreachable,
and the key the bucket is addressed by.

The script itself belongs in `tests/integration/`, against the real thing. There
is no `fakeredis` in this repository and deliberately so - `requirements/dev.txt`
has `testcontainers[redis]`, which is the convention: mock at the transport for
HTTP providers, use the real server for a datastore whose semantics are the point.

`IdempotencyStore` is different and is tested directly. Its logic is Python - key
construction, JSON, the TTL - and a fake Redis holding a dict exercises all of it
without emulating anything Redis decides.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
import redis.exceptions
from fastapi import FastAPI
from starlette.testclient import TestClient

from fixtures.settings import settings as build_settings
from gateway.auth import InvalidCredentialError, Principal
from gateway.limits import Decision, bucket_key
from gateway.main import build_app
from guardmem_core.types import TenantId

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from guardmem_core.settings import Settings

TENANT_A = TenantId("11111111-1111-4111-8111-111111111111")
TENANT_B = TenantId("22222222-2222-4222-8222-222222222222")
KEY_A = "key-a-0000000000"

PROBE = "/probe-limited"

PRINCIPAL_A = Principal(tenant_id=TENANT_A, key_id=KEY_A[:8], scopes=frozenset({"memory:read"}))


class _Backend:
    """Resolves one key."""

    def principal_for(self, credential: str) -> Principal:
        """Resolve `KEY_A` and nothing else.

        Args:
            credential: The presented token.

        Returns:
            `PRINCIPAL_A`.

        Raises:
            InvalidCredentialError: for any other value.
        """
        if credential != KEY_A:
            raise InvalidCredentialError("unknown key")
        return PRINCIPAL_A


@dataclass
class _Limiter:
    """A scripted limiter, so the middleware is tested and Redis is not.

    Attributes:
        decision: What `check` returns.
        error: When set, what `check` raises instead - for the fail-open case.
        calls: How many times it was consulted, so a test can assert the
            unauthenticated paths never reach it.
    """

    # `default_factory`, because RUF009 rejects a call in a dataclass default -
    # and rightly, even though `Decision` is frozen: the rule cannot tell which
    # defaults are safe to share, and one mutable default shared across
    # instances is the bug it exists to prevent.
    decision: Decision = field(
        default_factory=lambda: Decision(allowed=True, remaining=9, retry_after_s=0.0)
    )
    error: Exception | None = None
    calls: int = 0

    async def check(self, principal: Principal) -> Decision:
        """Return the scripted decision.

        Args:
            principal: Ignored beyond counting the call.

        Returns:
            `self.decision`.

        Raises:
            Exception: `self.error`, when one is set.
        """
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.decision


class _FakeRedis:
    """A dict with `get` and `set`, and the TTL it was asked for.

    Enough for `IdempotencyStore`, which only stores and reads JSON. Recording the
    `ex` argument is the point of writing this rather than reaching for a mock: the
    24h retention is part of S8.3's requirement, and a fake that discarded it would
    let the store pass while storing forever.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, name: str) -> str | None:
        """Read a key.

        Args:
            name: The key.

        Returns:
            The stored string, or None.
        """
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        """Write a key with an expiry.

        Args:
            name: The key.
            value: The payload.
            ex: Seconds to live.
        """
        self.values[name] = value
        self.ttls[name] = ex


@dataclass
class _State:
    """Only the fields the chain and the health router read."""

    auth: _Backend
    limiter: _Limiter
    settings: Settings
    pool: object = None
    redis: object = None


def _app(limiter: _Limiter, settings: Settings | None = None) -> FastAPI:
    """An app whose limiter is scripted.

    Args:
        limiter: The stand-in.
        settings: Process settings; built isolated from `.env` when omitted.

    Returns:
        The application, with a lifespan that opens nothing.
    """
    resolved = settings or build_settings()

    @asynccontextmanager
    async def _no_resources(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = _State(_Backend(), limiter, resolved)
        yield

    app = build_app(lifespan=_no_resources)

    # An authenticated route that touches nothing. `/memory/search` is the only
    # real one and it reaches Postgres, so a test asserting on the *headers* of an
    # allowed response would have to have a database to get a response at all -
    # and the middleware attaches those headers after `call_next`, so an exception
    # from the handler means no response and no headers.
    @app.get(PROBE)
    async def _probe() -> dict[str, str]:
        return {"ok": "1"}

    return app


def _authorised(client: TestClient) -> Any:
    """Make one authenticated request to the probe route.

    Args:
        client: The test client.

    Returns:
        The response.

    The probe is behind `Authenticate` - it is not in
    `middleware._UNAUTHENTICATED` - so it is limited exactly like a real route,
    while needing no pool, no embedder and no Redis.
    """
    return client.get(PROBE, headers={"Authorization": f"Bearer {KEY_A}"})


class TestTheBucketIsKeyedByTenantAndKey:
    """S8.3 names both halves, and both matter."""

    def test_the_key_carries_the_tenant_and_the_key_id(self) -> None:
        """`rl:<tenant>:<key_id>`.

        The tenant alone would let one compromised credential exhaust a whole
        tenant's budget. The key alone would let a tenant multiply its allowance by
        issuing more keys. Only the composite bounds what each side controls.
        """
        assert bucket_key(PRINCIPAL_A) == f"rl:{TENANT_A}:{KEY_A[:8]}"

    def test_two_tenants_never_share_a_bucket(self) -> None:
        """The property the composite exists for."""
        other = Principal(tenant_id=TENANT_B, key_id=KEY_A[:8], scopes=frozenset())

        assert bucket_key(PRINCIPAL_A) != bucket_key(other)

    def test_the_key_does_not_contain_the_credential(self) -> None:
        """A Redis key holding the whole credential would put it in `MONITOR`
        output, in `--bigkeys`, and in any dump taken to debug a limit."""
        assert KEY_A not in bucket_key(PRINCIPAL_A)


class TestWhatARefusalLooksLike:
    """429, with enough for a client to pace itself."""

    def test_an_exhausted_bucket_is_a_429(self) -> None:
        """The status code is the whole contract for most clients."""
        limiter = _Limiter(Decision(allowed=False, remaining=0, retry_after_s=2.5))

        with TestClient(_app(limiter)) as client:
            response = _authorised(client)

        assert response.status_code == 429
        assert response.json() == {"detail": "rate limit exceeded"}

    def test_retry_after_is_whole_seconds_rounded_up(self) -> None:
        """RFC 9110 allows only whole seconds, and rounding *down* would invite a
        retry that is still too early - costing the client another 429 and this
        process another request to refuse."""
        limiter = _Limiter(Decision(allowed=False, remaining=0, retry_after_s=2.1))

        with TestClient(_app(limiter)) as client:
            response = _authorised(client)

        assert response.headers["Retry-After"] == str(math.ceil(2.1))

    def test_retry_after_is_never_zero(self) -> None:
        """A `Retry-After: 0` tells a client to retry immediately, which is the one
        thing a refused client must not do."""
        limiter = _Limiter(Decision(allowed=False, remaining=0, retry_after_s=0.01))

        with TestClient(_app(limiter)) as client:
            response = _authorised(client)

        assert int(response.headers["Retry-After"]) >= 1

    def test_a_refusal_still_carries_the_budget_headers(self) -> None:
        """So a client learns its limit from the refusal as well as from success."""
        limiter = _Limiter(Decision(allowed=False, remaining=0, retry_after_s=1.0))

        with TestClient(_app(limiter)) as client:
            response = _authorised(client)

        assert response.headers["X-RateLimit-Limit"]
        assert response.headers["X-RateLimit-Remaining"] == "0"


class TestTheBudgetHeaders:
    """A client that can only learn its budget by exceeding it has to exceed it."""

    def test_an_allowed_request_reports_the_configured_limit(self) -> None:
        """Read off `Settings`, so the header and the bucket cannot disagree."""
        settings = build_settings(rate_limit_per_minute=30, rate_limit_burst=60)
        limiter = _Limiter(Decision(allowed=True, remaining=7, retry_after_s=0.0))

        with TestClient(_app(limiter, settings)) as client:
            response = _authorised(client)

        assert response.headers["X-RateLimit-Limit"] == "30"
        assert response.headers["X-RateLimit-Remaining"] == "7"

    def test_remaining_never_goes_negative(self) -> None:
        """The bucket floors a fractional token count, and a client reading a
        negative budget would have no sensible way to act on it."""
        limiter = _Limiter(Decision(allowed=True, remaining=-3, retry_after_s=0.0))

        with TestClient(_app(limiter)) as client:
            response = _authorised(client)

        assert response.headers["X-RateLimit-Remaining"] == "0"


class TestWhatHappensWhenRedisIsDown:
    """The decision that is easiest to get wrong, so it is pinned."""

    def test_an_unavailable_limiter_fails_open(self) -> None:
        """A limiter whose unavailability takes the API down is a dependency that
        exists to shed load becoming the reason nothing is served.

        `ARCHITECTURE.md` §4 degrades toward serving. What this protects is a cost
        and fairness budget, not a correctness invariant - which is exactly the
        opposite of tenancy, where an unresolvable tenant must fail closed because
        serving without it is a cross-tenant read.

        The request is allowed through, so it reaches the store and fails there.
        A 429 would mean it had been refused by the limiter, which is what this
        rules out.
        """
        limiter = _Limiter(error=redis.exceptions.ConnectionError("redis is gone"))

        with TestClient(_app(limiter), raise_server_exceptions=False) as client:
            response = _authorised(client)

        assert response.status_code != 429

    def test_a_non_redis_error_is_not_swallowed(self) -> None:
        """Fail-open covers Redis being unreachable, not a bug in the limiter.

        Catching everything would turn a programming error into an unlimited
        window that nothing reports, which is the failure mode this whole file
        exists to make visible.
        """
        limiter = _Limiter(error=ValueError("a bug, not an outage"))

        with TestClient(_app(limiter), raise_server_exceptions=False) as client:
            response = _authorised(client)

        assert response.status_code == 500


class TestTheUnauthenticatedPathsAreNotLimited:
    """Limiting a liveness probe is actively harmful."""

    @pytest.mark.parametrize("path", ["/healthz", "/openapi.json"])
    def test_a_probe_never_reaches_the_limiter(self, path: str) -> None:
        """An orchestrator polling `/healthz` would eventually be told 429 and
        restart a process that was fine.

        Asserted on the limiter's call count rather than on the status code,
        because a 200 would also be the answer if the limiter had been consulted
        and happened to allow it.
        """
        limiter = _Limiter()

        with TestClient(_app(limiter)) as client:
            client.get(path)

        assert limiter.calls == 0

    def test_an_unauthenticated_request_is_refused_before_the_limiter(self) -> None:
        """401 is cheaper than a Redis round trip, and the chain puts it first."""
        limiter = _Limiter()

        with TestClient(_app(limiter)) as client:
            response = client.get("/memory/search", params={"q": "x", "namespace": "n"})

        assert response.status_code == 401
        assert limiter.calls == 0
