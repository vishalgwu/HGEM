"""The seeded demo tenant, as a fixture other suites can reach.  S3.6, S4.2

`make seed` writes one tenant, seven entities and twenty-eight assertions
through the real path. That database is the most realistic fixture this
repository has, and from S4.2 more than one integration suite wants it -
incumbent retrieval needs an incumbent, and the seeded allergy is one.

Moved out of `tests/integration/test_seed_demo_tenant.py` at S4.2 for exactly
that reason. Registered as a plugin from `tests/conftest.py`, like the other
fixture modules, and lazy: nothing runs the seed until a test asks for it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING, Final

import asyncpg
import pytest

from conftest import REPO_ROOT
from guardmem_core.memory.vector.pool import sqlalchemy_dsn

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

__all__ = ["PATIENT_NAME", "TENANT_SLUG", "demo", "run_seed", "seeded"]

# Invoked as a module, not as a path, since S5.6 made `scripts` a package:
# `seed_demo_tenant` imports a sibling, and a qualified import only resolves
# with the repo root on `sys.path`, which `-m` from `cwd=REPO_ROOT` provides
# and running the file by path does not. `make seed` says the same thing.
SEED: Final = "scripts.seed_demo_tenant"

# The slug `scripts/demo_tenant_data.py` writes, and the canonical name it gives
# the patient. Both are read back from the database rather than recomputed, so
# a test never has to reimplement the seed's `uuid5` derivation.
TENANT_SLUG: Final = "demo-clinic"
PATIENT_NAME: Final = "Joan Ellery"


def run_seed(dsn: str) -> str:
    """Run the seed against `dsn`, as `make seed` would.

    Args:
        dsn: A libpq DSN for a migrated database.

    Returns:
        Its stdout, which is the CLI's interface and what the counts are read
        from.

    Raises:
        RuntimeError: the seed failed. Its output is included, because a failure
            here is a broken seed and not a broken test.

    Inherits the environment and overrides one variable, exactly as the alembic
    fixture does: `Settings` has nine required fields, the DSN is one, and the
    other eight come from `.env` - which every developer has and a fresh CI
    runner gets from `cp .env.example .env`.
    """
    environment = dict(os.environ)
    environment["GM_DATABASE_URL"] = sqlalchemy_dsn(dsn)
    result = subprocess.run(
        [sys.executable, "-m", SEED],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"seed failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


@pytest.fixture(scope="session")
def seeded(postgres_dsn: str) -> Iterator[str]:
    """Run the seed once, session-wide, and return its stdout.

    Session-scoped because the seed takes a few seconds and every test that
    wants it wants the same database state. The rows belong to its own
    deterministic tenant, so they cannot collide with the per-test tenants
    `fixtures/pgvector.py` creates and destroys around them - checked by running
    the seed suite first in a shared container.
    """
    yield run_seed(postgres_dsn)


@pytest.fixture
async def demo(postgres_dsn: str, seeded: str) -> AsyncIterator[tuple[asyncpg.Connection, str]]:
    """An owner connection scoped to the demo tenant, and that tenant's id."""
    connection = await asyncpg.connect(postgres_dsn)
    try:
        tenant = await connection.fetchval("SELECT id FROM tenant WHERE slug = $1", TENANT_SLUG)
        assert tenant is not None, "the seed did not create its tenant"
        await connection.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant))
        yield connection, str(tenant)
    finally:
        await connection.close()
