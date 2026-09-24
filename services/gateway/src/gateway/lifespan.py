"""What the gateway process owns, and how long it owns it.  BUILD_NOTEBOOK.md S8.1

S8.1's instruction: "Lifespan creates: Postgres pool, Redis pool, one
`httpx.AsyncClient` per provider, OTel tracer." All four are here, and three of
them need a note about what "one per process" is protecting.

**`RULES.md` §2.2 puts pool creation in lifespan explicitly - one pool per
process, never one per request.** Under an ASGI server that is not a style
preference: a pool opened per request serialises every handler behind a TCP
handshake and a Postgres backend fork, and at the 50 rps S8.4 has to sustain it
exhausts `max_connections` long before it exhausts the machine.

**Shutdown closes what startup opened, in reverse, and `AsyncExitStack` is
why.** This mirrors `mcp_server.lifespan` deliberately, including the reasoning
that put it there: a hand-written `try/finally` registered each resource only
once the *next* one had been constructed, so a pool opened and then a client that
raised leaked the pool. The stack fixes that by construction - a resource is
registered the moment it exists, and unwinding is LIFO.

**The tracer provider is process-global and that is not this module's choice.**
`opentelemetry` keeps one provider per process behind a module-level setter, so
"one tracer per gateway" is the only shape available. It is set here rather than
at import so that a test importing the app does not install a provider as a side
effect, and shut down on the way out so a second `lifespan()` in one interpreter
- which is exactly what the test suite does - does not leak a worker thread per
instance.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx
import redis.asyncio as aioredis
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

__all__ = ["SERVICE", "GatewayState", "lifespan"]

_LOGGER: Final = logging.getLogger(__name__)

# The `service.name` every span from this process carries. One string, here,
# because `PRD.md` §6.1's latency SLAs are read per service and a span whose
# service name is the library it happened to run through is unattributable.
SERVICE: Final = "guardmem-gateway"


@dataclass(frozen=True, slots=True)
class GatewayState:
    """Process-scoped resources, built once at startup.

    Reachable from a handler as `request.app.state.gateway`, which is FastAPI's
    own channel for this and the reason nothing here is a module global -
    `RULES.md` §2.4's "no global mutable state", with the single instance passed
    rather than imported.

    Attributes:
        settings: The validated configuration. Held rather than re-read, so every
            handler sees the same values a restart would be needed to change.
        pool: The Postgres pool. Request-scoped stores are built *from* it -
            `PgVectorStore` binds a tenant and the tenant comes from an
            authenticated request - so the pool lives here and the store does
            not. S8.2's tenancy middleware sets `app.tenant_id` on a connection
            checked out of this pool, which is what makes RLS apply.
        redis: The Redis client, for what the ingress owns rather than what the
            engine owns: S8.3's token bucket, its idempotency cache, and later
            the review leases. `redis.asyncio.Redis` is itself a connection pool,
            so this field is the pool.
        http: One `httpx.AsyncClient` per provider, keyed by provider name.
            `RULES.md` §2.2 again - a client per request throws away connection
            reuse on a path whose whole cost is round trips. Keyed rather than one
            shared client because a client carries a `base_url` and the providers
            do not share one.
        tracer: This process's tracer. Handed over rather than fetched at call
            time so a handler cannot acquire one from a provider that lifespan
            has already shut down.
    """

    settings: Settings
    pool: asyncpg.Pool
    redis: aioredis.Redis
    http: dict[str, httpx.AsyncClient]
    tracer: trace.Tracer


@asynccontextmanager
async def lifespan(_app: object = None) -> AsyncIterator[GatewayState]:
    """Open the process's resources, hand them over, and close them again.

    Args:
        _app: The `FastAPI` instance Starlette passes to a lifespan. Unused and
            ignored: nothing here needs the app object, and taking it
            positionally with a default is what lets a test call this directly as
            `async with lifespan():`. Starlette's signature is the constraint;
            the default is the affordance. Same shape as
            `mcp_server.lifespan.lifespan`, for the same reason.

    Yields:
        The `GatewayState` every handler reads from.

    Raises:
        pydantic.ValidationError: the environment does not satisfy `Settings` - a
            missing `GM_DATABASE_URL`, a threshold set out of order, a stray key
            in `.env`. Raised before anything is allocated, so a misconfigured
            process fails at startup rather than on first request.
        StoreUnavailable: Postgres is unreachable at the configured DSN.
        redis.RedisError: Redis is unreachable. See the note below on why this
            is raised at startup rather than discovered by a caller.

    **Redis is connected eagerly, with a `ping`.** `redis.asyncio.Redis` is lazy
    - constructing it opens nothing - so without this the first request to reach
    the rate limiter would be what discovers Redis is down, and it would discover
    it as a 500 on somebody's request rather than as a process that refused to
    start. The timeouts are set rather than left at the driver's default of none,
    per `RULES.md` §2.2: every outbound call has an explicit timeout.

    The DSN is converted with `libpq_dsn` because `GM_DATABASE_URL` carries
    SQLAlchemy's `postgresql+asyncpg://` marker for Alembic's benefit and asyncpg
    rejects it outright - `pool.py` owns that conversion and this is one of its
    call sites rather than another inline `str.replace`.
    """
    settings = get_settings()
    async with AsyncExitStack() as stack:
        # Pushed first so it unwinds last: the final line of a process's log is
        # the one saying it stopped, not a resource closing.
        stack.callback(_LOGGER.info, "guardmem-gateway stopped")
        provider = _tracing(stack, settings)
        pool = await create_pool(libpq_dsn(str(settings.database_url)))
        stack.push_async_callback(pool.close)
        redis = aioredis.Redis.from_url(
            str(settings.redis_url),
            socket_timeout=settings.store_timeout_s,
            socket_connect_timeout=settings.store_timeout_s,
        )
        stack.push_async_callback(redis.aclose)
        await redis.ping()
        http = {
            name: await stack.enter_async_context(httpx.AsyncClient(base_url=url))
            for name, url in _provider_urls(settings).items()
        }
        _LOGGER.info(
            "guardmem-gateway started",
            extra={
                "env": settings.env,
                "llm_provider": settings.llm_provider,
                "providers": sorted(http),
            },
        )
        yield GatewayState(
            settings=settings,
            pool=pool,
            redis=redis,
            http=http,
            tracer=provider.get_tracer(SERVICE),
        )


def _tracing(stack: AsyncExitStack, settings: Settings) -> TracerProvider:
    """Install this process's tracer provider and arrange its teardown.

    Args:
        stack: The lifespan's exit stack, which gets the shutdown callback.
        settings: Read for `env`, so a span carries which deployment produced it.

    Returns:
        The provider, for the caller to take a tracer from.

    **No exporter is configured here, and that is a later step's job rather than
    a gap.** A provider with no span processor collects spans and drops them,
    which is what is wanted until there is a collector to send them to: the
    instrumentation is live, so a missing span is a bug visible now rather than
    one discovered when the exporter lands. Adding OTLP later is a processor on
    this provider and touches nothing else.

    `shutdown` is registered rather than left to interpreter exit because the test
    suite runs this lifespan repeatedly in one process, and a provider replaced
    without being shut down leaks its worker thread per instance.
    """
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: SERVICE, "deployment.environment": settings.env})
    )
    trace.set_tracer_provider(provider)
    stack.callback(provider.shutdown)
    return provider


def _provider_urls(settings: Settings) -> dict[str, str]:
    """The base URL of every model provider this process talks HTTP to.

    Args:
        settings: Read for `ollama_url`.

    Returns:
        Provider name to base URL, one entry per provider the gateway owns a
        client for.

    **Only Ollama appears, and the other two are not omissions.** The Anthropic
    and OpenAI adapters each run on their vendor SDK, which constructs and owns
    its own `httpx2` client - `llm/providers/selection.py` is the composition root
    for that, and a second client here would be an unused socket plus a second
    place to configure a timeout. What this dict holds is the providers the
    gateway speaks to directly, and S9.2's router is where a second entry would
    come from.
    """
    return {"ollama": settings.ollama_url}
