"""Liveness and readiness.  BUILD_NOTEBOOK.md S8.1

S8.1's DONE WHEN names `/healthz`, so that path is published interface rather
than a convention this module chose.

**Two endpoints, because they answer different questions and a deployment acts on
the answers differently.** `/healthz` asks "is this process alive?" and touches
nothing external, so an orchestrator reads it to decide whether to *restart* the
container. `/readyz` asks "can it serve a request?" and probes its datastores, so
a load balancer reads it to decide whether to *route* to the container.

Collapsing them into one endpoint that checks Postgres is a real outage: a
database blip becomes a restart loop across every replica at once, which removes
the capacity that would have absorbed the blip. `ARCHITECTURE.md` §4's failure
table wants the opposite - shed traffic, keep the process.
"""

from __future__ import annotations

from typing import Final, Literal

import asyncpg
import redis.exceptions
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from gateway.dependencies import GatewayDep
from gateway.lifespan import SERVICE

__all__ = ["router"]

router: Final = APIRouter(tags=["health"])

# What a probe is allowed to fail with. Both drivers raise their own error trees
# plus the stdlib's for a socket that never answered, and `RULES.md` §2.3 forbids
# catching `Exception` outside the outermost boundary - so the tuple is spelled
# out rather than widened. A probe that swallowed everything would report
# "degraded" for a bug in this module, which is the one failure it cannot fix by
# shedding traffic.
_UNREACHABLE: Final = (OSError, TimeoutError, asyncpg.PostgresError, redis.exceptions.RedisError)


class Liveness(BaseModel):
    """The answer `/healthz` gives.

    Attributes:
        status: Always `"ok"`. A process that cannot return this returns nothing,
            which is itself the signal.
        service: Which service answered, so a response read out of context still
            says where it came from.
    """

    status: Literal["ok"] = "ok"
    service: str


class Readiness(BaseModel):
    """The answer `/readyz` gives.

    Attributes:
        status: `"ready"` only when every dependency below is reachable.
        postgres: Whether the pool served a connection that answered.
        redis: Whether Redis answered a `PING`.
    """

    status: Literal["ready", "degraded"]
    postgres: bool
    redis: bool


@router.get("/healthz", summary="Liveness", response_model=Liveness)
async def healthz() -> Liveness:
    """Report that the process is running.

    Returns:
        `Liveness`, always.

    Takes no dependency and touches no datastore, deliberately. See the module
    docstring: this is the endpoint that decides whether to restart the
    container, and a restart is the wrong response to a database that is briefly
    unreachable.
    """
    return Liveness(service=SERVICE)


@router.get(
    "/readyz",
    summary="Readiness",
    response_model=Readiness,
    # Declared, because the handler returns it. An undocumented status code is a
    # contract-suite failure by construction - schemathesis reads the published
    # responses and treats anything else as a violation - and it would be a real
    # one: a client generated from this schema would have no branch for the 503
    # this endpoint exists to send.
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": Readiness,
            "description": "At least one dependency is unreachable. Do not route here.",
        }
    },
)
async def readyz(state: GatewayDep, response: Response) -> Readiness:
    """Report whether this process can serve a request right now.

    Args:
        state: Process resources; both datastores are probed through it.
        response: Mutated to set the status code - see below.

    Returns:
        `Readiness` naming each dependency, so an operator reads *which* one is
        down rather than only that something is.

    **503 rather than 200-with-a-flag when degraded.** A load balancer reads the
    status code and nothing else; `"degraded"` under a 200 keeps the replica in
    rotation, which is the failure this endpoint exists to prevent. The body is
    for the human who then goes looking.

    Neither probe raises. A readiness check that 500s on an unreachable
    dependency has reported what a 503 already reported, while also spending the
    error budget it exists to protect.
    """
    postgres, cache = await _postgres_ok(state), await _redis_ok(state)
    if not (postgres and cache):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="degraded", postgres=postgres, redis=cache)
    return Readiness(status="ready", postgres=True, redis=True)


async def _postgres_ok(state: GatewayDep) -> bool:
    """Whether the pool can serve a connection that answers.

    Args:
        state: Holds the pool.

    Returns:
        True when `SELECT 1` came back.

    A query rather than the pool's own counters, because what a caller needs to
    know is whether a *statement* can run: a pool holding idle connections to a
    Postgres in recovery reports healthy on its counters and fails everything.
    """
    try:
        return bool(await state.pool.fetchval("SELECT 1") == 1)
    except _UNREACHABLE:
        return False


async def _redis_ok(state: GatewayDep) -> bool:
    """Whether Redis answers a `PING`.

    Args:
        state: Holds the client.

    Returns:
        True when the server replied.
    """
    try:
        return bool(await state.redis.ping())
    except _UNREACHABLE:
        return False
