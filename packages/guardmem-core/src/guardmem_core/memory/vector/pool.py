"""Opening the Postgres pool the stores run on.  BUILD_NOTEBOOK.md S3.2

Separate from `pgvector_store.py` because it has a different lifetime and a
different owner. A pool is process-scoped and opened once at startup; a
`PgVectorStore` is request-scoped, because it is bound to the tenant of an
authenticated request. Putting the constructor for the long-lived thing inside
the module for the short-lived one invites a call site that opens a pool per
request, which is the classic way to exhaust `max_connections`.

S3.3's outbox relay needs the same pool and is not a vector store.
"""

from __future__ import annotations

import asyncpg
from pgvector.asyncpg import register_vector

from guardmem_core.errors import StoreUnavailable

__all__ = ["create_pool"]


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    """Open a connection pool with the codec this store needs.

    Args:
        dsn: A libpq DSN. Note that `settings.database_url` carries SQLAlchemy's
            `postgresql+asyncpg://` driver marker for Alembic's benefit and
            asyncpg does not understand it, so strip that before passing it here.
        min_size: Connections held open when idle.
        max_size: Ceiling. `RULES.md` §2.2 wants bounded resources; an unbounded
            pool turns one slow query into an outage of the whole database.

    Returns:
        A pool whose every connection can round-trip `vector`.

    Raises:
        StoreUnavailable: Postgres is unreachable or refused the connection.

    The registration is why this helper exists rather than a bare
    `asyncpg.create_pool` at each call site. `pgvector` installs its codec per
    *connection*, so a pool that grows a new one later without this `init` hook
    starts failing on a vector parameter halfway through the day - a fault that
    only appears under load.
    """

    async def _init(connection: asyncpg.Connection) -> None:
        await register_vector(connection)

    try:
        return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - needs a dead database
        raise StoreUnavailable(f"cannot reach Postgres at the configured DSN: {exc}") from exc
