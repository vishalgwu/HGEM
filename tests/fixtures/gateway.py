"""Fakes for driving the gateway without its datastores.  BUILD_NOTEBOOK.md S8.1-S8.4

Four test modules had grown their own copy of this - `test_gateway_middleware`,
`test_gateway_limits`, `test_gateway_propose` and `test_tenant_isolation` each
declared a fake auth backend, an always-allowing limiter and a partial
`GatewayState`. The duplication was not the worst of it: every time `GatewayState`
gained a field, four modules failed with `Missing positional arguments`, and the
fix was four near-identical edits. S8.3 and S8.4 each cost that.

**A helper module rather than pytest fixtures, deliberately.** These are
constructors, not per-test resources: a test wants a state with *one* thing changed,
and `fake_state(queue=FakeQueue())` says that in a line, where a fixture would need
one per combination. `tests/conftest.py` puts this directory on the path, so a plain
import works.

**`FakeState` is a dataclass with defaults, not a `GatewayState`.** The real one is
frozen with thirteen required fields, most of which need a datastore. Building one in
a unit test means a dozen `None  # type: ignore` lines, which is what the four copies
all did. This carries only the fields the request path reads, and a handler reaching
for one it does not have fails with an `AttributeError` naming it - which is the right
failure, because it means the test is exercising something it did not intend to.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from gateway.auth import InvalidCredentialError, Principal
from gateway.limits import Decision
from gateway.main import build_app

from fixtures.mcp import settings as build_settings
from guardmem_core.types import TenantId

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from guardmem_core.settings import Settings

__all__ = [
    "READER",
    "READ_KEY",
    "TENANT",
    "WRITER",
    "WRITE_KEY",
    "FakeAuth",
    "FakeIdempotency",
    "FakeLimiter",
    "FakeQueue",
    "FakeState",
    "app_with",
    "fake_state",
]

TENANT = TenantId("11111111-1111-4111-8111-111111111111")

# Two credentials, because the scope check is worth exercising from both sides. A
# single all-scopes key would make every 403 test have to build its own principal.
WRITE_KEY = "key-write-000001"
READ_KEY = "key-read-0000001"

WRITER = Principal(
    tenant_id=TENANT, key_id=WRITE_KEY[:8], scopes=frozenset({"memory:read", "memory:write"})
)
READER = Principal(tenant_id=TENANT, key_id=READ_KEY[:8], scopes=frozenset({"memory:read"}))


class FakeAuth:
    """Resolves the two keys above and refuses everything else.

    Satisfies `AuthBackend` structurally, which is the point of that Protocol: no
    registration, no base class, and `mypy --strict` still checks the signature.
    """

    def principal_for(self, credential: str) -> Principal:
        """Resolve a configured key.

        Args:
            credential: The presented bearer token.

        Returns:
            `WRITER` or `READER`.

        Raises:
            InvalidCredentialError: any other value.
        """
        if credential == WRITE_KEY:
            return WRITER
        if credential == READ_KEY:
            return READER
        raise InvalidCredentialError("unknown key")


class FakeLimiter:
    """Always allows.

    The bucket's own behaviour is tested against a real Redis in
    `tests/integration/test_rate_limit_bucket.py`, and its middleware in
    `test_gateway_limits.py` with a scripted decision. Everything else wants a limiter
    that is simply not in the way - a real one would make an unrelated test fail on a
    429, which is a wrong answer to the question being asked.
    """

    async def check(self, principal: Principal) -> Decision:
        """Allow the request.

        Args:
            principal: Ignored.

        Returns:
            An allowing `Decision` with a nominal budget.
        """
        return Decision(allowed=True, remaining=100, retry_after_s=0.0)


@dataclass
class FakeQueue:
    """Records enqueues instead of making them.

    Attributes:
        jobs: One tuple per call - `(job name, payload, job id, queue name)`. All four
            are recorded because all four are part of the contract with the worker: the
            name is what arq dispatches on, the payload is where the tenant crosses a
            process boundary, the id is what stops a retry double-evaluating, and the
            queue name is the string whose drift would silently strand every proposal.
    """

    jobs: list[tuple[str, dict[str, Any], str | None, str | None]] = field(default_factory=list)

    async def enqueue_job(
        self,
        function: str,
        *args: Any,
        _job_id: str | None = None,
        _queue_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Record what would have been enqueued.

        Args:
            function: The job name.
            args: The payload, positionally, as the gateway sends it.
            _job_id: arq's dedupe key.
            _queue_name: Which queue.
            kwargs: Accepted and ignored - arq's signature is wider than the gateway
                uses, and a fake that refused the extras would fail on a change to
                arq rather than to this repository.
        """
        self.jobs.append((function, args[0], _job_id, _queue_name))


@dataclass
class FakeIdempotency:
    """An in-memory stand-in for S8.3's store.

    Attributes:
        stored: Keyed by the same triple the real store hashes into a Redis key -
            tenant, client key, body hash. Keeping the tenant in the key here as well
            is deliberate: it is what makes a cross-tenant replay test meaningful
            rather than a test of this fake's shape.
    """

    stored: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)

    async def get(self, principal: Principal, key: str, body_hash: str) -> dict[str, Any] | None:
        """Read a stored response.

        Args:
            principal: The caller.
            key: The client's idempotency key.
            body_hash: `sha256` of the request body.

        Returns:
            What was stored, or None.
        """
        return self.stored.get((str(principal.tenant_id), key, body_hash))

    async def put(
        self, principal: Principal, key: str, body_hash: str, response: dict[str, Any]
    ) -> None:
        """Store a response for replay.

        Args:
            principal: The caller.
            key: The client's idempotency key.
            body_hash: `sha256` of the request body.
            response: What to return on a replay.
        """
        self.stored[str(principal.tenant_id), key, body_hash] = response


@dataclass
class FakeState:
    """Only the fields the request path reads.

    Attributes:
        auth: Resolves credentials.
        limiter: Consulted per authenticated request.
        settings: Read for the rate-limit headers and the request deadline.
        queue: Where the async accept enqueues.
        idempotency: Where a replayed write is stored.
        llm: None by default, so `mode=strict` answers 503 - which is the behaviour
            most tests want, and the one that needs no model.
        graph: Left None; only the inline pipeline touches it.
        ontology: Left None, same reason.
        pool: Left None. A handler that reaches it in a unit test is doing something
            the test did not intend, and an `AttributeError` naming `pool` says so more
            clearly than a connection error would.
        redis: Left None, same reason.
        embedder: Left None; `GET /memory/search` needs one and has the live stack.
        tracer: Left None; nothing asserts on spans yet.
    """

    auth: FakeAuth = field(default_factory=FakeAuth)
    limiter: FakeLimiter = field(default_factory=FakeLimiter)
    settings: Settings = field(default_factory=build_settings)
    queue: FakeQueue = field(default_factory=FakeQueue)
    idempotency: FakeIdempotency = field(default_factory=FakeIdempotency)
    llm: Any = None
    graph: Any = None
    ontology: Any = None
    pool: Any = None
    redis: Any = None
    embedder: Any = None
    tracer: Any = None


def fake_state(**overrides: Any) -> FakeState:
    """A state with every fake fresh, and any field replaced.

    Args:
        overrides: Fields to set. `fake_state(llm=object())` is how a test says "and a
            model is configured" without restating the other eleven.

    Returns:
        The state.

    Fresh fakes per call, not module-level singletons: `FakeQueue` and
    `FakeIdempotency` accumulate, so a shared instance would make one test's enqueue
    visible to the next and the order tests ran in would matter.
    """
    return FakeState(**overrides)


def app_with(state: FakeState) -> FastAPI:
    """An app over `state`, with a lifespan that opens nothing.

    Args:
        state: What handlers will read from `request.app.state.gateway`.

    Returns:
        The application.

    The lifespan is replaced rather than skipped, because `TestClient` runs one and the
    real one would open a Postgres pool. `build_app` takes the argument for exactly
    this - see its docstring on why that seam exists.
    """

    @asynccontextmanager
    async def _no_resources(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = state
        yield

    return build_app(lifespan=_no_resources)
