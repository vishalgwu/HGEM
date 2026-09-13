"""A migrated Postgres for the integration suite.  BUILD_NOTEBOOK.md S3.2

S3.1 left the integration tests skipping unless a developer happened to have
`make dev` running, and said so in as many words: "S3.2 is the step that brings
testcontainers in and makes that enforceable." This is that.

**The container is provisioned by the repository's own initdb scripts.** Not by
a copy of them written for tests - `infra/docker/initdb/` is mounted into the
image, so the extensions and the `guardmem_app` role come from exactly the
files the dev stack uses. A test fixture that created the role itself would
pass on the day someone broke `02-app-role.sql`, and the whole point of
`RULES.md` non-negotiable #2 is that the role is real.

**The schema is applied by `alembic upgrade head`, as a subprocess.** Also not a
copy: importing the revision and calling `upgrade()` would skip `env.py`, the
version table, and the ordering `alembic` imposes, which is most of what a
migration *is*. If it runs here, `make migrate` runs.

`GM_TEST_DATABASE_URL` short-circuits all of it and points the suite at an
already-migrated database. That is for iterating against `make dev`; CI and the
DONE WHEN use the container, because a database someone has been poking at by
hand is not evidence of anything.

Registered as a plugin from `tests/conftest.py` rather than living in
`tests/integration/conftest.py`, which is where it started. Two files named
`conftest` in a tree with no `__init__.py` are two modules with the same name,
and `mypy` refuses the pair outright - so the suite `make typecheck` covers
would have stopped being checkable the moment this file existed. The fixtures
are lazy, so nothing starts a container until a test asks for one.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Final

import asyncpg
import pytest

from conftest import REPO_ROOT

__all__ = ["app_role_dsn", "postgres_dsn"]

# Resolved by marker in `conftest`, not by `parents[n]` - see that module for
# why the index is the part worth avoiding.
INITDB_DIR: Final = REPO_ROOT / "infra" / "docker" / "initdb"

# The same image and the same tag as `infra/docker/docker-compose.dev.yml`.
# Pinned for the reason `RULES.md` §3 pins model ids: a floating tag makes a
# green run unreproducible, and here it would also silently change the pgvector
# version that `assertion_hnsw` is built by.
_IMAGE: Final = "pgvector/pgvector:0.8.6-pg16"
_DB: Final = "guardmem"
_OWNER: Final = "guardmem"
_OWNER_PASSWORD: Final = "guardmem"
_STARTUP_TIMEOUT_S: Final = 90.0


def _docker_available() -> bool:
    """Can we talk to a Docker daemon at all?"""
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


def _wait_until_accepting(dsn: str) -> None:
    """Block until the server answers as `guardmem`, or give up.

    A successful authenticated connection is the signal, rather than a line in
    the log. The Postgres entrypoint prints "ready to accept connections" twice -
    once for the temporary server it runs the initdb scripts against, once for
    the real one - so matching that string is the classic way to start querying
    a database whose `guardmem_app` role does not exist yet. The temporary
    server listens on a unix socket only, so a TCP connection that authenticates
    cannot be it.

    Raises:
        TimeoutError: the container never became usable.
    """

    async def _probe() -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                connection = await asyncpg.connect(dsn, timeout=5)
            except (OSError, asyncpg.PostgresError) as exc:
                last = exc
                await asyncio.sleep(0.5)
                continue
            await connection.close()
            return
        raise TimeoutError(f"postgres did not accept connections in {_STARTUP_TIMEOUT_S}s: {last}")

    asyncio.run(_probe())


def _migrate(dsn: str) -> None:
    """Run `alembic upgrade head` against `dsn`.

    `env.py` reads the URL from `Settings`, so the override is an environment
    variable rather than an argument - and it carries the `+asyncpg` driver
    marker, because that is what `GM_DATABASE_URL` is specified to hold.

    Raises:
        RuntimeError: the migration failed. The subprocess output is included,
            since a failure here is a broken migration and not a broken test.
    """
    environment = dict(os.environ)
    environment["GM_DATABASE_URL"] = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """A libpq DSN for a migrated, `guardmem_app`-provisioned database.

    Yields:
        The owner's DSN. Tests that need the least-privilege role build their
        own from it - `RULES.md` non-negotiable #2 is about what
        `guardmem_app` cannot do, so a fixture that handed out that connection
        by default would make it easy to test the wrong role by accident.

    Session-scoped: starting a container and migrating it takes tens of seconds,
    and every test in this suite wants the same schema. Tests are responsible
    for cleaning up their own rows, which they already were.

    Synchronous on purpose. `pytest-asyncio` runs in auto mode with a
    function-scoped event loop, so a session-scoped *async* fixture would
    outlive the loop it was created on. Container startup is blocking work
    anyway; the one piece that needs a loop gets its own via `asyncio.run`.
    """
    external = os.environ.get("GM_TEST_DATABASE_URL")
    if external:
        yield external.replace("postgresql+asyncpg://", "postgresql://", 1)
        return
    if not _docker_available():
        pytest.skip("no Docker daemon; set GM_TEST_DATABASE_URL to use an existing database")

    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(_IMAGE)
        .with_env("POSTGRES_USER", _OWNER)
        .with_env("POSTGRES_PASSWORD", _OWNER_PASSWORD)
        .with_env("POSTGRES_DB", _DB)
        # Index ordering differs between machines without it; the compose file
        # sets the same thing for the same reason.
        .with_env("POSTGRES_INITDB_ARGS", "--encoding=UTF8 --locale=C")
        .with_volume_mapping(str(INITDB_DIR), "/docker-entrypoint-initdb.d", "ro")
        .with_exposed_ports(5432)
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        dsn = f"postgresql://{_OWNER}:{_OWNER_PASSWORD}@{host}:{port}/{_DB}"
        _wait_until_accepting(dsn)
        _migrate(dsn)
        yield dsn
    finally:
        container.stop()


@pytest.fixture(scope="session")
def app_role_dsn(postgres_dsn: str) -> str:
    """The same database as `guardmem_app`, the least-privilege role.

    The credential is a development one, created by
    `infra/docker/initdb/02-app-role.sql` in a container that lives for the
    length of one test run.
    """
    _, _, endpoint = postgres_dsn.rpartition("@")
    return f"postgresql://guardmem_app:guardmem_app@{endpoint}"  # pragma: allowlist secret
