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
from arq.connections import RedisSettings
from arq.connections import create_pool as create_queue
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

from gateway.auth import SettingsAuthBackend
from gateway.limits import IdempotencyStore, TokenBucket
from guardmem_core.llm.providers import build_llm
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.schemas.ontology import load_ontology
from guardmem_core.settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg
    from arq import ArqRedis

    from gateway.auth import AuthBackend
    from gateway.limits import RateLimiter
    from guardmem_core.llm.base import LLMClient
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder
    from guardmem_core.schemas.ontology import Ontology

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
            not. The tenant reaches a statement through
            `pool.tenant_transaction`, per transaction, and **not** by the S8.2
            middleware holding a connection for the request - see
            `middleware.Tenancy` for why that shape is rejected.
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
        embedder: Read-side query embedding, typed as the `Embedder` protocol.
            **The `HashEmbedder` bound today models no semantics** - it hashes
            text, so identical text retrieves identically and nothing else does.
            It is here for the reason `mcp_server.ServerState.embedder` gives:
            it is the only `Embedder` in the package, and naming it in the state
            is better than a handler reaching for one on its own. Nothing measured
            through it is a retrieval quality number.
        limiter: S8.3's token bucket, keyed by `tenant:api_key`. Process-scoped
            because `register_script` caches the script's SHA per client, so
            rebuilding it per request would re-send the body on every call.
        idempotency: Stored responses for replayed writes, S8.3. Held here rather
            than built per request for the same reason every other Redis-backed
            thing is: the client is the pool.
        queue: The arq client the async accept enqueues onto, S8.4. One per process
            for the reason every other client here is: `enqueue_job` is a Redis round
            trip and the whole 80ms budget is round trips.
        llm: The model provider, or **None when no credential is configured**. The
            same choice `mcp_server.ServerState.llm` makes and for the same reason: a
            gateway that refused to start without a model could not serve
            `memory.search`, which needs none. `mode=strict` is what refuses instead.
        graph: The entity graph. `NetworkXGraphStore`, so it is in-process and holds
            only what this process wrote - `graph_fanout` from the gateway is not a
            measurement of anything, and that feeds `R` rather than `C`.
        ontology: The validated predicate pack, loaded once.
        auth: What resolves a credential to a `Principal`, S8.2. Process-scoped
            for the same reason the pool is - `SettingsAuthBackend` parses the
            configured key set once, and a per-request parse would put every
            credential through a JSON decoder on every call. It lives here rather
            than being held by the middleware because construction reads
            `Settings`, and `build_app()` must stay importable without an
            environment: CI's `gates` job has no `.env`.
    """

    settings: Settings
    pool: asyncpg.Pool
    redis: aioredis.Redis
    http: dict[str, httpx.AsyncClient]
    tracer: trace.Tracer
    embedder: Embedder
    queue: ArqRedis
    llm: LLMClient | None
    graph: GraphStore
    ontology: Ontology
    limiter: RateLimiter
    idempotency: IdempotencyStore
    auth: AuthBackend


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
        state = await _open(stack, settings)
        _LOGGER.info(
            "guardmem-gateway started",
            extra={
                "env": settings.env,
                "llm_provider": settings.llm_provider,
                "providers": sorted(state.http),
                "model_bound": state.llm is not None,
            },
        )
        yield state


async def _open(stack: AsyncExitStack, settings: Settings) -> GatewayState:
    """Open everything the process owns and gather it into one object.

    Args:
        stack: The lifespan's exit stack. Every resource is registered the moment it
            exists, so a later failure cannot leak an earlier success.
        settings: The validated configuration.

    Returns:
        The `GatewayState` handlers read from.

    Raises:
        StoreUnavailable: Postgres is unreachable at the configured DSN.
        redis.RedisError: Redis is unreachable - see `lifespan` on the eager `ping`.

    **Split out of `lifespan` at S8.4**, when the queue, the model client, the graph
    and the ontology took that function past `RULES.md` §2.4's 50-line body cap. The
    seam is not arbitrary: `lifespan` was doing two things - opening resources, and
    being the context manager that hands them over - and only the opening grows. What
    stays behind is the shape Starlette requires; what moved is the part that gets
    longer every time this service gains a dependency.
    """
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
    # S8.4's queue. A second Redis client rather than reusing the one above:
    # `arq` wraps its own connection with the job protocol, and sharing a client
    # between raw commands and arq's would make a bucket key and a job key
    # neighbours in one namespace with two owners.
    queue = await create_queue(RedisSettings.from_dsn(str(settings.redis_url)))
    stack.push_async_callback(queue.aclose)
    # Not entered on the stack: `NetworkXGraphStore` holds an in-process dict and
    # opens nothing. The Neo4j backend does own a driver, which is why
    # `mcp_server` uses `build_graph` and enters it - the gateway names the class
    # directly because a durable graph would make `graph_fanout` differ between
    # a gateway-inline decision and a worker one for the same proposal.
    graph = NetworkXGraphStore()
    llm = await _llm_or_none(stack, settings, http)
    return GatewayState(
        settings=settings,
        pool=pool,
        redis=redis,
        http=http,
        tracer=provider.get_tracer(SERVICE),
        embedder=HashEmbedder(),
        queue=queue,
        llm=llm,
        graph=graph,
        ontology=load_ontology("clinical"),
        limiter=TokenBucket(
            redis,
            per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
        ),
        idempotency=IdempotencyStore(redis, ttl_s=settings.idempotency_ttl_s),
        # Built last and deliberately not on the exit stack: it owns no
        # socket, file or thread, so there is nothing to close. A `ValueError`
        # here is a malformed `GM_GATEWAY_API_KEYS` and takes the process down
        # at startup, which is where a configuration error belongs.
        auth=SettingsAuthBackend(settings),
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


async def _llm_or_none(
    stack: AsyncExitStack, settings: Settings, http: dict[str, httpx.AsyncClient]
) -> LLMClient | None:
    """Open the configured provider on `stack`, or log why there is none.

    Args:
        stack: The lifespan's exit stack. The provider is entered on it rather than
            returned bare: for Anthropic and OpenAI the adapter sits on an SDK client
            owning a transport, and an adapter handed back without its closer leaks it.
        settings: The process configuration.
        http: The per-provider clients; Ollama's is the one `build_llm` may use.

    Returns:
        The adapter, or None when `build_llm` refuses for want of a credential.
        Nothing is registered on `stack` in that case - `build_llm` raises on entry,
        before allocating.

    **A warning rather than a raise**, the same call `mcp_server.lifespan` makes: a
    gateway serving reads and refusing `mode=strict` is degraded rather than broken,
    and a process that would not start without a model could not serve
    `memory.search`, which needs none. The async accept is unaffected either way - it
    enqueues, and the worker owns the model.

    Only `ValueError` is caught: `build_llm` raises it for a missing key and a blank
    model id, both configuration. Anything else out of an SDK constructor is a real
    fault that should stop the process.
    """
    try:
        return await stack.enter_async_context(build_llm(settings, http["ollama"]))
    except ValueError as exc:
        _LOGGER.warning(
            "no model provider: %s. reads and the async accept work; mode=strict will refuse.",
            exc,
        )
        return None


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
