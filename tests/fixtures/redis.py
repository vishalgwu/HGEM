"""A real Redis for the integration suite.  BUILD_NOTEBOOK.md S8.3

S8.3's token bucket is a Lua script, and a script is the one thing a fake cannot
stand in for: emulating it in Python would mean writing a second implementation
and then asserting the two agree, which measures the second one. So the bucket's
arithmetic - refill, saturation at capacity, the atomic decrement - is verified
against the server that will run it.

Modelled on `fixtures.postgres` deliberately, down to the escape hatch and the
skip: `GM_TEST_REDIS_URL` points the suite at an already-running Redis for
iterating against `make dev`, and an absent Docker daemon skips rather than
fails, because a developer without Docker should still be able to run everything
else.

Synchronous and session-scoped for the reason that file gives: `pytest-asyncio`
runs with a function-scoped event loop, so a session-scoped *async* fixture would
outlive the loop it was created on. Container startup is blocking work anyway.

Registered from `tests/conftest.py` as a plugin rather than as a second
`conftest.py`, same as the others - two modules named `conftest` in a tree with no
`__init__.py` are a pair `mypy` refuses outright.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    import redis.asyncio as aioredis

__all__ = ["flushed", "redis_url"]

# Pinned for the reason `fixtures.postgres` pins its image: a floating tag makes a
# green run unreproducible. 7-alpine because the bucket uses only `HMGET`, `HSET`,
# `EXPIRE` and `EVALSHA`, all of which predate 7 by years - the pin is about
# reproducibility rather than about needing a recent server.
_IMAGE: Final = "redis:7-alpine"
_PORT: Final = 6379
_STARTUP_TIMEOUT_S: Final = 60.0


def _docker_available() -> bool:
    """Can we talk to a Docker daemon at all?

    Returns:
        True when `docker info` answers.

    Duplicated from `fixtures.postgres` rather than shared, and that is a real
    trade. Importing it would couple the Redis fixture to the Postgres one, so a
    test needing only Redis would start pulling in a 620 MB image's module - and
    the function is six lines whose behaviour is a subprocess call. The shared
    version arrives when a third fixture wants it.
    """
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """A URL for a running Redis.

    Yields:
        A `redis://` URL the gateway's client can be built from.

    Session-scoped: starting a container takes seconds and every test wants the
    same server. Tests are responsible for their own keys - see `flushed` below,
    which is the function-scoped half.
    """
    external = os.environ.get("GM_TEST_REDIS_URL")
    if external:
        yield external
        return
    if not _docker_available():
        pytest.skip("no Docker daemon; set GM_TEST_REDIS_URL to use an existing Redis")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = DockerContainer(_IMAGE).with_exposed_ports(_PORT)
    container.start()
    try:
        wait_for_logs(container, "Ready to accept connections", timeout=_STARTUP_TIMEOUT_S)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_PORT)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture
async def flushed(redis_url: str) -> AsyncIterator[aioredis.Redis]:
    """A client on an empty database, emptied again afterwards.

    Args:
        redis_url: The session-scoped server.

    Yields:
        A connected client.

    **`FLUSHDB` before as well as after.** After alone is the obvious choice and
    is not enough: a test that fails mid-way leaves keys behind, and the *next*
    test then starts with a bucket that already has tokens drawn from it. Bucket
    tests are stateful by nature, so a leaked key is a test that passes or fails
    depending on what ran before it.

    Function-scoped over a session-scoped server, which is the same split
    `fixtures.pgvector` uses: the expensive thing is shared, the state is not.
    """
    import redis.asyncio as aioredis_impl

    client = aioredis_impl.Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
