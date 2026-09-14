"""How a chain link is spelled as an `audit_event` row.  S5.5

The same seam `outbox.py` draws: this module knows the table's shape from
`0001_initial` and the statements that fill and read it, and `audit.py` knows
the chain arithmetic. Neither knows about the other's problem.

**`append` takes a connection, not a pool, and that is the whole of `RULES.md`
non-negotiable #4.** "The audit event is written in the same transaction as the
state change - not after, not best-effort." A method that acquired its own
connection could not do that however carefully it was called: the audit row
would commit separately, and a crash between the two would leave a state change
with no record, or a record of a change that rolled back. Taking the caller's
connection makes the guarantee structural.

**Appends to one tenant's chain are serialised with an advisory lock.** Two
concurrent transactions would both read the same head, both compute a link
claiming it as `prev_digest`, and both insert - a fork, which `verify_chain`
reports as a break at the second one. `pg_advisory_xact_lock` is held to COMMIT
and needs no UPDATE privilege, which matters because the migration revokes
UPDATE on this table: `SELECT ... FOR UPDATE` would be refused outright.

**`Conn` comes from `memory/vector/pool.py`, which is a wart worth naming.**
That module is the Postgres connection layer for the whole package - the
outbox relay uses it too - and it sits under `memory/vector/` only because
that is where S3.2 first needed it. Importing it from `observability` is the
second caller saying so. Moving it is a rename across three steps' worth of
code and belongs in its own commit, not in this one.

**`prev_digest` and `digest` are `BYTEA` in the column and hex `str` on the
model.** `ARCHITECTURE.md` §5 specifies the column; hex is what a log line, an
API response and a test failure can all show. The conversion lives here, which
is the only place that knows both.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from guardmem_core.observability.audit import GENESIS, next_link, verify_chain
from guardmem_core.schemas.receipt import AuditEvent
from guardmem_core.types import TenantId, TraceId

if TYPE_CHECKING:
    from datetime import datetime

    from asyncpg import Record

    from guardmem_core.memory.vector.pool import Conn
    from guardmem_core.observability.audit import ChainVerification

# Every `uuid` parameter is cast in the statement rather than converted to
# `uuid.UUID` here. That is `rowmap.py`'s convention from S3.2 and it is what
# keeps `TenantId` a `str` the whole way down - `types.py` makes the ids
# `NewType`s over `str`, and a layer that quietly swapped in `UUID` objects
# would produce values that compare unequal to every id the pipeline holds.
__all__ = [
    "INSERT_AUDIT",
    "SELECT_CHAIN",
    "SELECT_HEAD",
    "append",
    "event_from_row",
    "read_chain",
    "verify_tenant_chain",
]

# One statement per question. `RETURNING seq` because `BIGSERIAL` assigns it and
# the caller's link does not know it until the insert lands.
INSERT_AUDIT: Final = """
    INSERT INTO audit_event (tenant_id, trace_id, kind, payload, prev_digest, digest, created_at)
    VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7)
    RETURNING seq
"""

# The current head of one tenant's chain. `ORDER BY seq DESC LIMIT 1` rather
# than `max(seq)`, so the planner uses the primary key index directly.
SELECT_HEAD: Final = """
    SELECT digest FROM audit_event
    WHERE tenant_id = $1::uuid
    ORDER BY seq DESC
    LIMIT 1
"""

# The whole chain, oldest first - which is the order `verify_chain` requires and
# says it does not check.
SELECT_CHAIN: Final = """
    SELECT seq, tenant_id, trace_id, kind, payload, prev_digest, digest, created_at
    FROM audit_event
    WHERE tenant_id = $1::uuid
    ORDER BY seq ASC
"""

# Serialises appends within one tenant's chain. The key is derived from the
# tenant id rather than being a constant, so two tenants never wait on each
# other - the chains are independent and a global lock would make every write in
# the system queue behind every other.
_LOCK_CHAIN: Final = "SELECT pg_advisory_xact_lock(hashtext($1::text))"


async def append(
    connection: Conn,
    *,
    tenant_id: TenantId,
    trace_id: TraceId,
    kind: str,
    payload: dict[str, object],
    created_at: datetime,
) -> AuditEvent:
    """Extend a tenant's chain, inside the caller's transaction.

    Args:
        connection: A connection with a transaction already open and
            `app.tenant_id` already set - `pool.tenant_transaction` produces
            one. Passed in rather than acquired, so the row commits with the
            state change it records. See the module docstring.
        tenant_id: Whose chain to extend.
        trace_id: The proposal that produced the event.
        kind: One of `AuditEvent`'s six.
        payload: The event body. Must be JSON-safe; `canonical_json` says why.
        created_at: System time of the event.

    Returns:
        The inserted link, with `seq` filled in from the sequence.

    Raises:
        TypeError: the payload is not JSON-safe.
        ValueError: `kind` is not one of the six.

    Reads the head and writes the new link in the same round trip pair, under
    the advisory lock, so the `prev_digest` a link claims is still the head when
    it lands.
    """
    await connection.execute(_LOCK_CHAIN, tenant_id)
    head = await connection.fetchval(SELECT_HEAD, tenant_id)
    link = next_link(
        tenant_id=tenant_id,
        trace_id=trace_id,
        kind=kind,
        payload=payload,
        prev_digest=bytes(head).hex() if head is not None else GENESIS,
        created_at=created_at,
    )
    seq = await connection.fetchval(
        INSERT_AUDIT,
        link.tenant_id,
        link.trace_id,
        link.kind,
        json.dumps(link.payload),
        bytes.fromhex(link.prev_digest),
        bytes.fromhex(link.digest),
        link.created_at,
    )
    return link.model_copy(update={"seq": seq})


async def read_chain(connection: Conn, tenant_id: TenantId) -> list[AuditEvent]:
    """Every link in one tenant's chain, oldest first.

    Args:
        connection: A connection with `app.tenant_id` set - `audit_event` is
            RLS-protected, so one without it reads zero rows rather than
            raising, and an empty chain verifies vacuously.
        tenant_id: Whose chain.

    Returns:
        The links, in `seq` order.

    Reads the whole chain rather than paging it. That is honest for now and will
    not be for a busy tenant; the incremental form has to checkpoint a verified
    prefix, which needs somewhere to record the checkpoint, and inventing that
    before there is a chain long enough to need it would be a guess.
    """
    rows = await connection.fetch(SELECT_CHAIN, tenant_id)
    return [event_from_row(row) for row in rows]


async def verify_tenant_chain(connection: Conn, tenant_id: TenantId) -> ChainVerification:
    """`verify_chain(tenant_id)` as S5.5 names it.

    Args:
        connection: As `read_chain`.
        tenant_id: Whose chain to verify.

    Returns:
        `{verified, broken_at, checked}`.

    The two halves are separate functions because only this one needs a
    database: `verify_chain` is the arithmetic and is unit-tested without one.
    """
    return verify_chain(await read_chain(connection, tenant_id))


def event_from_row(row: Record) -> AuditEvent:
    """Rebuild a link from its row.

    `payload` comes back from `JSONB` as text unless a codec is registered, and
    `create_pool` registers none for it - so it is parsed here. The digests come
    back as `bytes` and become hex, which is the conversion this module exists
    to own.
    """
    payload = row["payload"]
    return AuditEvent(
        seq=row["seq"],
        # `str()` is not decoration, for the reason `rowmap.py` spells out:
        # asyncpg returns `uuid.UUID` for a UUID column, and `TenantId` is a
        # `NewType` over `str`, so the model would otherwise carry an object
        # that compares unequal to every tenant id the pipeline holds. The
        # integration suite is what caught it - pydantic's `strict=True`
        # refused the `UUID` outright, which is the failure being loud.
        tenant_id=TenantId(str(row["tenant_id"])),
        trace_id=TraceId(row["trace_id"]),
        kind=row["kind"],
        payload=json.loads(payload) if isinstance(payload, str) else payload,
        prev_digest=bytes(row["prev_digest"]).hex(),
        digest=bytes(row["digest"]).hex(),
        created_at=row["created_at"],
    )
