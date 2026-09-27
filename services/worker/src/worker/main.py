"""The worker process.  BUILD_NOTEBOOK.md S8.4

`arq` is configured by a class, not a call: it reads `WorkerSettings` for the function
list, the Redis connection and the lifecycle hooks. So this module is mostly that
class plus the two hooks that own the process's resources.

**The same one-pool-per-process rule as the gateway**, for the same reason
`RULES.md` §2.2 gives. A worker running ten jobs concurrently that opened a pool per
job would hold ten pools, and `max_connections` is a property of the database rather
than of the worker.

**`QUEUE` is named here and imported by the gateway.** That is the one string the two
services have to agree on, and the import-linter contract forbidding them from
importing each other means it cannot be shared in code - so it is duplicated, and
`tests/unit/test_gateway_propose.py` asserts the two spellings match. A queue name
that drifted would not fail: the gateway would enqueue into a queue nothing reads,
and every proposal would be accepted and silently never evaluated, which is the worst
failure this system can have.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Final

import httpx
from arq.connections import RedisSettings

from guardmem_core.llm.providers import build_llm
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.settings import get_settings
from worker.tasks.evaluate import evaluate

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

__all__ = ["QUEUE", "WorkerSettings", "main"]

_LOGGER: Final = logging.getLogger(__name__)

# The queue both services must name identically. See the module docstring on why this
# is duplicated in `gateway.routers.memory` rather than shared.
QUEUE: Final = "guardmem:eval"


async def startup(ctx: dict[str, Any]) -> None:
    """Open what every job on this worker shares.

    Args:
        ctx: arq's context, which this populates: `settings`, `pool` and `llm` are
            read by `tasks.evaluate`, and `resources` is what `shutdown` closes.

    Raises:
        pydantic.ValidationError: the environment does not satisfy `Settings`.
        StoreUnavailable: Postgres is unreachable at the configured DSN.
        ValueError: no model provider is configured - `build_llm` refuses a blank
            credential. Every one of these is raised at startup, so a misconfigured
            worker refuses to run rather than draining the queue by failing every
            job, which looks like throughput on a dashboard.

    **The model client is opened here, once, and not per job.** `RULES.md` §2.2:
    one client per provider, created in lifespan, never per request. Until
    2026-09-27 each job built its own HTTP and SDK client and discarded the
    connection pool with it, while the composition's docstring said the client was
    shared.

    Everything is registered on one `AsyncExitStack` the moment it exists, so a
    model client that fails to open closes the pool behind it rather than leaking
    it - the argument `mcp_server.lifespan` makes about a hand-written `try/finally`.
    `libpq_dsn` because `GM_DATABASE_URL` carries SQLAlchemy's `postgresql+asyncpg://`
    marker for Alembic and asyncpg rejects it; `pool.py` owns that conversion.
    """
    settings = get_settings()
    stack = AsyncExitStack()
    try:
        pool = await create_pool(libpq_dsn(str(settings.database_url)))
        stack.push_async_callback(pool.close)
        http = await stack.enter_async_context(httpx.AsyncClient(base_url=settings.ollama_url))
        llm = await stack.enter_async_context(build_llm(settings, http))
    except BaseException:
        await stack.aclose()
        raise
    ctx.update(settings=settings, pool=pool, llm=llm, resources=stack)
    _LOGGER.info(
        "guardmem-worker started",
        extra={"env": settings.env, "queue": QUEUE, "llm_provider": settings.llm_provider},
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Close what `startup` opened, in reverse.

    Args:
        ctx: arq's context.

    A pool that is dropped rather than closed leaves its connections for Postgres to
    reap on a timeout. One orphan per restart is invisible; a supervisor restarting a
    crash loop reaches `max_connections` and takes the rest of the deployment with it.
    """
    resources = ctx.get("resources")
    if resources is not None:
        await resources.aclose()
    _LOGGER.info("guardmem-worker stopped")


class WorkerSettings:
    """What `arq worker.main.WorkerSettings` reads.

    A plain class rather than a dataclass because arq introspects it as a namespace of
    class attributes, and `functions` has to be a list of coroutines it can register
    by name - the name is what the gateway's `enqueue_job` sends.

    Attributes:
        functions: Every job this worker can run. One today; `compact`, `reindex`,
            `digest`, `sla_sweeper` and the outbox relay binding are named in
            `PROJECT_TREE.md` and arrive with the steps that need them.
        queue_name: Must equal the gateway's `QUEUE`.
        redis_settings: Built from `GM_REDIS_URL` by `main`, so the worker and the
            gateway cannot be pointed at different Redis instances by drift.
        max_jobs: Concurrency ceiling, set by `main` from `max_concurrent_scores`.
            Reusing that setting is deliberate: it already means "how many candidates
            may be in flight", and a worker running more jobs than that would exceed
            the pipeline's own fan-out bound by a factor nobody chose.
        job_timeout: A job's own ceiling. `PRD.md` §6.1 budgets full evaluation at
            p95 1.6 s and p99 3.0 s; this is far above both on purpose, because a
            timeout firing at the p99 would retry healthy work.
        keep_result: How long a result stays readable, set by `main` to match the
            idempotency window - so a caller polling a `trace_id` and a caller
            replaying a write see the same job for the same period.

    **The three settings-derived attributes are filled by `main`, not at class
    definition.** Reading `get_settings()` in the class body would make *importing*
    this module require a populated environment, and CI's `gates` job has no `.env` -
    so a unit test that imports `QUEUE` would fail there while passing on every
    developer machine. That exact failure took CI red for three commits at S8.1, and
    the shape is identical: configuration read at import time rather than at start.

    It means `arq worker.main.WorkerSettings` is not the supported entry point;
    `guardmem-worker` is, and it is what the Dockerfile and `project.scripts` name.
    """

    functions: Final[list[Callable[..., Coroutine[Any, Any, Any]]]] = [evaluate]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = QUEUE
    job_timeout = 120
    # Placeholders with the right types. `main` replaces all three before the worker
    # runs; arq reads them as plain class attributes, so assignment is the whole hook.
    redis_settings = RedisSettings()
    max_jobs = 8
    keep_result = 86_400


def main() -> None:
    """Entry point for the `guardmem-worker` console script.

    Reads the environment here rather than at import, and fills the three attributes
    `WorkerSettings` leaves as placeholders - see that class on why.

    Defers to arq's own runner rather than reimplementing the loop, signal handling
    and job polling: `arq.worker.run_worker` is what `arq <settings>` calls, and a
    console script means a Dockerfile's CMD and a developer's terminal name the same
    thing.

    Raises:
        pydantic.ValidationError: the environment does not satisfy `Settings`. Raised
            before the worker starts, which is where a configuration error belongs.
    """
    from arq.worker import run_worker

    settings = get_settings()
    WorkerSettings.redis_settings = RedisSettings.from_dsn(str(settings.redis_url))
    WorkerSettings.max_jobs = settings.max_concurrent_scores
    WorkerSettings.keep_result = settings.idempotency_ttl_s
    run_worker(WorkerSettings)  # type: ignore[arg-type]
