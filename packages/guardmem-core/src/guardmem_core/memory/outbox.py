"""How a pending dual write is spelled as an `outbox` row, and read back.  S3.3

The same seam `rowmap.py` draws for `assertion`, drawn again for `outbox`: this
module knows the table's shape from `0001_initial` - its columns, its JSONB
payload, and the statements that fill and drain it - and nothing about
connections, graphs or retry policy. `relay.py` knows those and nothing about
column order.

**The row commits with the assertion or not at all.** That is the whole point of
the pattern (`ARCHITECTURE.md` §2.4), and it is why `INSERT_OUTBOX` is executed
by `PgVectorStore.upsert` inside the transaction that writes the assertion
rather than by the router afterwards. Two transactions would leave a window in
which an assertion exists with `visible = false` and no event to ever flip it -
a fact written, durable, and permanently unreadable.

**The id is derived, not random.** `upsert` is replayed after a relay restart
and answers `ON CONFLICT DO NOTHING`; a `uuid4` here would mean every replay
enqueued a *second* event for the same assertion, and the relay would dispatch
it twice. Deriving the id from the assertion id makes the enqueue idempotent by
exactly the key S3.3's flow says retries are idempotent by - the same argument
that fixed the provenance ids one step earlier.

**The payload carries what a failure has to be logged with, and no more.**
`RULES.md` §6 requires every log line to carry `trace_id`, `tenant_id` and
`namespace`; the relay may have to report a failure it hit *before* it could
read the assertion, so those three cannot live only on the row it failed to
read. `tenant_id` is load-bearing for a second reason - `outbox` is not
tenant-scoped and has no RLS, so it is the only thing telling the relay which
tenant to set before it touches `assertion`, which is. Nothing else is copied:
a duplicate of the fact would be a second source of truth, free to drift from
the row it describes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, uuid5

from guardmem_core.types import AssertionId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from asyncpg import Record

    from guardmem_core.schemas.entity import StoredAssertion

__all__ = [
    "CLAIM_OUTBOX",
    "EVENT_KIND",
    "INSERT_OUTBOX",
    "MARK_DISPATCHED",
    "REVEAL_ASSERTION",
    "PendingEvent",
    "outbox_params",
    "pending_from_row",
]

# The only `kind` written today. Recorded on every row rather than assumed,
# because the outbox is the natural home for the supersession and retraction
# events that `MEMORY_ENGINE.md` §2.3 and §2.5 will need, and a table whose rows
# do not say what they are cannot grow a second kind without a migration.
EVENT_KIND: Final = "assertion.written"


@dataclass(frozen=True, slots=True)
class PendingEvent:
    """One undispatched outbox row, as the relay needs it.

    Attributes:
        outbox_id: The row to mark dispatched.
        assertion_id: The fact whose dual write is still incomplete.
        tenant_id: Which tenant to set before touching `assertion`. Not a
            convenience - without it the RLS policy on that table matches
            nothing, and the relay would read zero rows and conclude the
            assertion had vanished.
        namespace: For the log line, per `RULES.md` §6.
        trace_id: The proposal this write came from, for the same reason.
    """

    outbox_id: str
    assertion_id: AssertionId
    tenant_id: TenantId
    namespace: Namespace
    trace_id: TraceId


def outbox_params(assertion: StoredAssertion, tenant_id: TenantId) -> tuple[object, ...]:
    """Flatten one pending dual write into `INSERT_OUTBOX`'s parameters.

    Args:
        assertion: The fact being written, already carrying its `trace_id` and
            `namespace`.
        tenant_id: The store's tenant, authoritative for the same reason it is
            in `assertion_params` - it came from an authenticated request and
            the model's came from whatever built it.

    Returns:
        The row id, the assertion it refers to, and the JSONB payload.
    """
    return (
        str(uuid5(NAMESPACE_URL, f"outbox/{assertion.assertion_id}")),
        assertion.assertion_id,
        json.dumps(
            {
                "kind": EVENT_KIND,
                "tenant_id": tenant_id,
                "namespace": assertion.namespace,
                "trace_id": assertion.trace_id,
            }
        ),
    )


def pending_from_row(row: Record) -> PendingEvent:
    """Rebuild a `PendingEvent` from a claimed outbox row.

    Args:
        row: A row returned by `CLAIM_OUTBOX`.

    Returns:
        The event, with the `NewType` ids reapplied.

    Raises:
        KeyError: the payload is missing a field. Every row in this table was
            written by `outbox_params` above, so that is a schema change that
            forgot a backfill, and it deserves to stop the relay rather than be
            defaulted past.

    `json.loads` rather than a codec: asyncpg returns `jsonb` as text unless one
    is registered, which is what `rowmap.assertion_from_row` already does with
    `object_json`. The `str()` calls on the ids are there for the reason that
    module gives - asyncpg hands back `uuid.UUID` for a UUID column while the
    domain ids are `NewType(..., str)`, so a bare cast would satisfy `mypy` and
    compare unequal to every id the pipeline holds.
    """
    payload = json.loads(row["event"])
    return PendingEvent(
        outbox_id=str(row["id"]),
        assertion_id=AssertionId(str(row["assertion_id"])),
        tenant_id=TenantId(payload["tenant_id"]),
        namespace=Namespace(payload["namespace"]),
        trace_id=TraceId(payload["trace_id"]),
    )


# --- the statements those tuples fill -------------------------------------

INSERT_OUTBOX: Final = """
    INSERT INTO outbox (id, assertion_id, event)
    VALUES ($1::uuid, $2::uuid, $3::jsonb)
    ON CONFLICT (id) DO NOTHING
"""

# Claim and record the attempt in one statement, so a relay that dies before it
# reaches the graph has still left evidence that it tried. Two statements would
# put the increment either before the claim, where it is racy, or after it,
# where it is lost on exactly the crash this column exists to count.
#
# `FOR UPDATE SKIP LOCKED` is what lets two relays run at once: the second skips
# the rows the first is holding rather than blocking behind them. It is not what
# makes the dispatch safe - the claim commits and releases its locks, so a slow
# relay's row is fair game for the next pass. Safety comes from the effects
# being idempotent, which is `GraphStore.upsert_assertion`'s contract and
# `MARK_DISPATCHED`'s own `dispatched_at IS NULL`.
#
# `attempts < $1` is `RULES.md` §2.3's "hard attempt cap". A row that reaches it
# stops being claimed and stays pending, which is the deliberate choice: it is a
# poison event, its assertion is invisible and therefore harmless, and an
# operator finds it with `SELECT * FROM outbox WHERE dispatched_at IS NULL AND
# attempts >= n`. Dropping it would lose the write; retrying it forever would
# starve every healthy event queued behind it.
CLAIM_OUTBOX: Final = """
    WITH claimed AS (
        SELECT id FROM outbox
        WHERE dispatched_at IS NULL AND attempts < $1
        ORDER BY created_at
        LIMIT $2
        FOR UPDATE SKIP LOCKED
    )
    UPDATE outbox SET attempts = attempts + 1
    WHERE id IN (SELECT id FROM claimed)
    RETURNING id, assertion_id, event
"""

# `dispatched_at IS NULL` makes completion idempotent in the same way the claim
# is: a second pass over an already-finished row updates nothing rather than
# moving its timestamp forward, so the column keeps saying when the graph side
# actually landed.
MARK_DISPATCHED: Final = """
    UPDATE outbox SET dispatched_at = $1
    WHERE id = $2::uuid AND dispatched_at IS NULL
"""

# The relay's half of the dual write, and the only statement in the system that
# may set this column true (`ARCHITECTURE.md` §2.4). Deliberately unconditional
# on `valid_to`: an assertion superseded between its write and its relay is
# still owed its visibility, because every read filters on `visible` *and*
# `valid_to`, so leaving it false would also hide the row from the point-in-time
# query that exists to recover it.
REVEAL_ASSERTION: Final = """
    UPDATE assertion SET visible = true WHERE id = $1::uuid
"""
