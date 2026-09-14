"""The Postgres pool, and the transactions everything on it runs in.  S3.2, S3.3

Separate from `pgvector_store.py` because it has a different lifetime and a
different owner. A pool is process-scoped and opened once at startup; a
`PgVectorStore` is request-scoped, because it is bound to the tenant of an
authenticated request. Putting the constructor for the long-lived thing inside
the module for the short-lived one invites a call site that opens a pool per
request, which is the classic way to exhaust `max_connections`.

S3.3 added the two transaction helpers, and they are here rather than on the
store for a reason worth stating plainly: `SET LOCAL app.tenant_id` is the only
thing standing between a pooled connection and a cross-tenant read, and a
second, subtly different copy of it is exactly the bug it exists to prevent.
The outbox relay needs the same transaction semantics as the store and is not a
store, so the semantics belong to neither of them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final

import asyncpg
from pgvector.asyncpg import register_vector

from guardmem_core.errors import StoreUnavailable

if TYPE_CHECKING:
    from asyncpg.pool import PoolConnectionProxy

    from guardmem_core.types import TenantId

__all__ = [
    "Conn",
    "create_pool",
    "libpq_dsn",
    "sqlalchemy_dsn",
    "tenant_transaction",
    "transaction",
]

# What `Pool.acquire()` actually hands back. Not a `Connection`: it is a proxy
# that forwards to one and is returned to the pool on exit, and `asyncpg-stubs`
# is right to distinguish them. Named here, next to the helpers that yield it,
# so the store and the relay annotate the same thing the same way.
type Conn = PoolConnectionProxy[asyncpg.Record]

# SQLAlchemy names a driver in the URL scheme; libpq does not. `GM_DATABASE_URL`
# is specified to carry the SQLAlchemy form because Alembic reads it, and
# asyncpg rejects it outright - so every asyncpg call site has to convert, and
# three of them were doing it inline with this literal before the S3.6 audit.
_SQLALCHEMY_SCHEME: Final = "postgresql+asyncpg://"
_LIBPQ_SCHEME: Final = "postgresql://"


def libpq_dsn(url: str) -> str:
    """Convert a DSN to the form asyncpg accepts.

    Args:
        url: A Postgres URL in either form. Already-libpq input is returned
            unchanged, so this is safe to apply twice.

    Returns:
        The same DSN with SQLAlchemy's `+asyncpg` driver marker removed.

    Named rather than inlined because the alternative is a magic string at every
    asyncpg call site, and a typo in one of them produces a connection error
    that reads like a network fault rather than like a string bug.

    **Anchored at the front**, unlike the three inline `str.replace(..., 1)`
    calls it replaced. A scheme is a prefix; `replace` is not, so it would
    happily rewrite the first occurrence anywhere in the string - including
    inside a password. Nobody has that password, and a conversion that can
    corrupt a credential under any input is not one worth keeping.
    """
    if not url.startswith(_SQLALCHEMY_SCHEME):
        return url
    return _LIBPQ_SCHEME + url.removeprefix(_SQLALCHEMY_SCHEME)


def sqlalchemy_dsn(url: str) -> str:
    """Convert a DSN to the form `GM_DATABASE_URL` and Alembic expect.

    Args:
        url: A Postgres URL in either form. Already-SQLAlchemy input is returned
            unchanged.

    Returns:
        The same DSN carrying the `+asyncpg` driver marker.

    The inverse of `libpq_dsn`, and anchored at the front for the reason that
    one gives. Needed just as often: anything that starts a database and then
    hands it to `alembic upgrade head` has a libpq DSN and needs the other one.
    """
    if url.startswith(_SQLALCHEMY_SCHEME) or not url.startswith(_LIBPQ_SCHEME):
        return url
    return _SQLALCHEMY_SCHEME + url.removeprefix(_LIBPQ_SCHEME)


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


@asynccontextmanager
async def transaction(pool: asyncpg.Pool) -> AsyncIterator[Conn]:
    """Yield a pooled connection inside a transaction, translating driver faults.

    Args:
        pool: The process-wide pool.

    Yields:
        A connection with a transaction open. It commits on a clean exit and
        rolls back on any exception, which is `asyncpg.Transaction`'s own
        behaviour and the only reason this wrapper can be this thin.

    Raises:
        StoreUnavailable: the pool could not produce a working connection, or
            the connection died mid-transaction. Retryable, which the driver's
            own exception types do not say to a caller above the store.

    Use this for the tables that carry no tenant column - `outbox` is the only
    one today. Anything touching a tenant-scoped table wants
    `tenant_transaction` instead, and the difference is not a style preference:
    an RLS-protected table read on a connection with no `app.tenant_id` returns
    zero rows rather than an error.
    """
    try:
        async with pool.acquire() as connection, connection.transaction():
            yield connection
    except (OSError, asyncpg.PostgresConnectionError) as exc:  # pragma: no cover
        # Needs a database that dies mid-transaction, which is a fault injection
        # test and not this suite's.
        raise StoreUnavailable(f"postgres connection failed: {exc}") from exc


@asynccontextmanager
async def tenant_transaction(
    pool: asyncpg.Pool, tenant_id: TenantId, *, timeout_s: float
) -> AsyncIterator[Conn]:
    """Yield a connection inside a transaction scoped to one tenant.

    Args:
        pool: The process-wide pool.
        tenant_id: The tenant every statement in this transaction speaks for.
        timeout_s: Ceiling on the `set_config` round trip, per `RULES.md` §2.2.

    Yields:
        A connection with `app.tenant_id` set and a transaction open.

    Raises:
        StoreUnavailable: the pool could not produce a working connection.

    The third argument to `set_config` is `is_local`, and passing `true` is what
    makes this safe on a *pooled* connection: the setting reverts at COMMIT or
    ROLLBACK, so it cannot leak to whoever checks the connection out next. A
    plain `SET` here would be a cross-tenant read with no symptom - the query
    would succeed and return the wrong tenant's rows.
    """
    async with transaction(pool) as connection:
        await connection.execute(
            "SELECT set_config('app.tenant_id', $1, true)", tenant_id, timeout=timeout_s
        )
        yield connection
