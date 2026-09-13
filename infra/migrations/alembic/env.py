"""Alembic environment.  BUILD_NOTEBOOK.md S3.1

Two departures from the generated async template, both required by rules this
repo already has.

**The URL comes from `Settings`, not from `alembic.ini`.** `RULES.md` §2.4:
"No global mutable state. Settings come from a single `pydantic-settings`
object, injected." A connection string in a tracked ini file would be a second
source for `GM_DATABASE_URL`, and the one that gets committed is the one that
ends up pointing at the wrong database. The ini keeps its placeholder so the
file stays valid standalone; nothing reads it.

**`target_metadata` is `None` and autogenerate is not used.** There is no
SQLAlchemy model layer in this project and there is not going to be one - the
domain lives in Pydantic (`RULES.md` §2.1) and the store implementations at
S3.2/S3.4 talk to the drivers directly. Migrations here are written by hand,
which is also what makes the RLS policies, the grants and the constraint
triggers in `0001_initial` expressible at all: autogenerate would not see them
and would cheerfully propose dropping them.

Offline mode is kept working. `RULES.md` §7 requires a "migration dry-run on
staging clone" in the release checklist, and `alembic upgrade head --sql` is how
that is produced.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from guardmem_core.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Hand-written migrations only - see the module docstring.
target_metadata = None


def _database_url() -> str:
    """The one connection string, from the one settings object.

    Returns:
        `GM_DATABASE_URL` with its password rendered. `PostgresDsn` hides the
        password from `str()` as a safety default, which is right everywhere
        except here - the driver needs it. `RULES.md` §1.5 forbids PII and
        secrets in *logs*, and this value is handed to the engine rather than
        logged.
    """
    return get_settings().database_url.unicode_string()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for a dry run."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run every pending migration inside one transaction."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine and run the migrations through it."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
