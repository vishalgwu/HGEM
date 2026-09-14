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

**Every statement here carries an explicit `timeout_s`, and the advisory lock is
why it is required rather than defaulted.** `RULES.md` §2.2 asks for an explicit
timeout on every outbound call, and `PgVectorStore` and `OutboxRelay` have taken
one since S3.2 - these four functions shipped without one, which is the
difference between a slow append and a hung worker. `pg_advisory_xact_lock`
blocks until the holder's transaction ends, with no bound of its own: one
transaction that stalls while holding a tenant's chain lock would otherwise
park every later append on that tenant *forever*, each holding a pooled
connection, until the pool is exhausted and the process stops serving every
other tenant too. A bounded wait turns that into a failed request with a
retryable error, which is the difference between an incident and a blip.

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
    from guardmem_core.schemas.verdict import DecisionRecord

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
    "append_decision",
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
    timeout_s: float,
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
        timeout_s: Per-statement ceiling, from `settings.store_timeout_s`.
            Required and not defaulted, for the reason `PgVectorStore` gives -
            and for the sharper one in the module docstring: the first statement
            below waits on a lock that has no bound of its own.

    Returns:
        The inserted link, with `seq` filled in from the sequence.

    Raises:
        TypeError: the payload is not JSON-safe.
        ValueError: `kind` is not one of the six.
        asyncio.TimeoutError: the chain lock was held for longer than
            `timeout_s`. The caller's transaction rolls back, so the state
            change this event records rolls back with it - which is the correct
            outcome, since a state change with no audit row is the one thing
            non-negotiable #4 forbids.

    Reads the head and writes the new link in the same round trip pair, under
    the advisory lock, so the `prev_digest` a link claims is still the head when
    it lands.
    """
    await connection.execute(_LOCK_CHAIN, tenant_id, timeout=timeout_s)
    head = await connection.fetchval(SELECT_HEAD, tenant_id, timeout=timeout_s)
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
        timeout=timeout_s,
    )
    return link.model_copy(update={"seq": seq})


async def append_decision(
    connection: Conn,
    record: DecisionRecord,
    *,
    tenant_id: TenantId,
    trace_id: TraceId,
    created_at: datetime,
    timeout_s: float,
) -> AuditEvent:
    """Record one Layer 3 decision on the tenant's chain.

    Args:
        connection: As `append`.
        record: What was decided, and everything it was decided from.
        tenant_id: Whose chain.
        trace_id: The proposal.
        created_at: System time of the decision.
        timeout_s: As `append`.

    Returns:
        The inserted link.

    **A DECISION is not a state change, which is why this may stand alone.**
    `RULES.md` non-negotiable #4 binds the audit event to "the state change" -
    and three of the four outcomes change nothing: a REJECT, a HITL_REVIEW and
    an ESCALATE write no assertion. The record *is* the artifact. An AUTO_WRITE
    does change state, and the `WRITE` event for it has to commit with the
    assertion; that is the applier's transaction and it does not exist yet (see
    `pipeline/orchestrator.py`). Calling this and then writing separately would
    satisfy neither rule, so nothing here does.

    `mode="json"` because the payload is hashed as canonical JSON and re-read
    from `JSONB`: the enums have to be their string values and the floats plain
    numbers, or `canonical_json` refuses them - which it does loudly rather than
    coercing, for the reason its docstring gives.
    """
    return await append(
        connection,
        tenant_id=tenant_id,
        trace_id=trace_id,
        kind="DECISION",
        payload=record.model_dump(mode="json"),
        created_at=created_at,
        timeout_s=timeout_s,
    )


async def read_chain(
    connection: Conn, tenant_id: TenantId, *, timeout_s: float
) -> list[AuditEvent]:
    """Every link in one tenant's chain, oldest first.

    Args:
        connection: A connection with `app.tenant_id` set - `audit_event` is
            RLS-protected, so one without it reads zero rows rather than
            raising, and an empty chain verifies vacuously.
        tenant_id: Whose chain.
        timeout_s: Per-statement ceiling, as `append`. It matters more here than
            anywhere else in this module: the statement reads the *whole* chain,
            so the one call whose cost grows without bound is also the one a
            caller is most likely to leave running.

    Returns:
        The links, in `seq` order.

    Reads the whole chain rather than paging it. That is honest for now and will
    not be for a busy tenant; the incremental form has to checkpoint a verified
    prefix, which needs somewhere to record the checkpoint, and inventing that
    before there is a chain long enough to need it would be a guess.
    """
    rows = await connection.fetch(SELECT_CHAIN, tenant_id, timeout=timeout_s)
    return [event_from_row(row) for row in rows]


async def verify_tenant_chain(
    connection: Conn, tenant_id: TenantId, *, timeout_s: float
) -> ChainVerification:
    """`verify_chain(tenant_id)` as S5.5 names it.

    Args:
        connection: As `read_chain`.
        tenant_id: Whose chain to verify.
        timeout_s: As `read_chain`, which this forwards to.

    Returns:
        `{verified, broken_at, checked}`.

    The two halves are separate functions because only this one needs a
    database: `verify_chain` is the arithmetic and is unit-tested without one.
    """
    return verify_chain(await read_chain(connection, tenant_id, timeout_s=timeout_s))


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
