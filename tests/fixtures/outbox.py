"""Fixtures and probes for exercising `OutboxRelay`.  BUILD_NOTEBOOK.md S3.2, S3.3

Builds on `fixtures/pgvector.py`, which supplies the migrated database, the
tenant, and the `guardmem_app` pool. This adds the other half of the dual write:
a graph backend to apply it to, a relay bound to both, and the three owner reads
that say what actually happened to a row.

Separate from that module rather than appended to it, for the reason the code
under test is separate: one is the write path's scaffolding and one is the
worker's, and a test that wants a relay does not want a store bound to a tenant.
Registered through `pytest_plugins` in `tests/conftest.py` for the reason given
there - there must be exactly one `conftest.py` in this tree.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import pytest

from fixtures.fakes import FakeGraphStore
from fixtures.pgvector import LATER, TIMEOUT_S
from guardmem_core.memory.relay import OutboxRelay

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.memory.graph.base import GraphStore

__all__ = [
    "RELAYED_AT",
    "attempts",
    "dispatched_at",
    "graph",
    "is_visible",
    "relay",
    "relay_over",
    "second_tenancy",
]

# When the relay is pretending to run. Distinct from `WHEN` and `LATER` so a
# test asserting on `dispatched_at` cannot pass against a world-time value by
# coincidence - the two axes are different clocks and this is the system one.
RELAYED_AT: Final = LATER + timedelta(hours=6)


@pytest.fixture
def graph() -> FakeGraphStore:
    """The graph half of the dual write, in memory and idempotent by id."""
    return FakeGraphStore()


@pytest.fixture
def relay(pool: asyncpg.Pool, graph: FakeGraphStore) -> OutboxRelay:
    """A relay over this test's pool, with its clock pinned to `RELAYED_AT`.

    Pinned rather than real, because `RULES.md` §5's test hygiene forbids
    `sleep` and wants fake clocks - and because "what did this write into
    `dispatched_at`?" is only a question worth asking if the answer is a value
    the test chose.
    """
    return OutboxRelay(pool, graph, timeout_s=TIMEOUT_S, clock=lambda: RELAYED_AT)


def relay_over(pool: asyncpg.Pool, graph: GraphStore, **kwargs: int) -> OutboxRelay:
    """A relay over an arbitrary graph backend, with the same pinned clock.

    The `relay` fixture covers the healthy path; this is for the tests that need
    a specific fault from `fixtures/graph_faults.py`, or a `max_attempts` low
    enough to reach the cap without five passes.
    """
    return OutboxRelay(pool, graph, timeout_s=TIMEOUT_S, clock=lambda: RELAYED_AT, **kwargs)


async def second_tenancy(owner: asyncpg.Connection, tenancy: dict[str, str]) -> dict[str, str]:
    """Give the *other* tenant an entity, and a `tenancy` dict of its own.

    Args:
        owner: The table owner.
        tenancy: This test's ids. Its `other` tenant becomes the returned dict's
            `tenant`, so `pgvector.assertion()` builds rows under it unchanged.

    Returns:
        A dict shaped like `tenancy`, pointing at the second tenant.

    The `set_config` either side is required, not hygiene. `entity` has `FORCE
    ROW LEVEL SECURITY` and its policy carries no explicit `WITH CHECK`, so
    Postgres reuses the `USING` expression for inserts - meaning the owner
    cannot write a row for a tenant other than the one currently set. Restoring
    the setting afterwards matters just as much: every other statement this test
    runs as the owner assumes the first tenant.
    """
    entity = str(uuid4())
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", tenancy["other"])
    await owner.execute(
        "INSERT INTO entity (id, tenant_id, type, canonical_name)"
        " VALUES ($1::uuid, $2::uuid, 'Patient', 'Patient 9001')",
        entity,
        tenancy["other"],
    )
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", tenancy["tenant"])
    return {"tenant": tenancy["other"], "other": tenancy["tenant"], "entity": entity}


async def is_visible(owner: asyncpg.Connection, assertion_id: str) -> bool:
    """Is the assertion row readable by a reader, as the relay left it?

    Read as the owner rather than through `search`, on purpose: `search` filters
    on `visible` *and* `valid_to` *and* `retracted_at`, so a test asserting
    invisibility through it cannot say which of the three did the work.
    """
    visible = await owner.fetchval(
        "SELECT visible FROM assertion WHERE id = $1::uuid", assertion_id
    )
    return bool(visible)


async def dispatched_at(owner: asyncpg.Connection, assertion_id: str) -> object:
    """When the outbox row for this assertion was closed, or `None` if pending."""
    return await owner.fetchval(
        "SELECT dispatched_at FROM outbox WHERE assertion_id = $1::uuid", assertion_id
    )


async def attempts(owner: asyncpg.Connection, assertion_id: str) -> int:
    """How many passes have claimed this event.

    The column a stalled event is found by, and the reason the claim increments
    before the work rather than after it: a relay that dies mid-flight has to
    leave a number behind, or a poison event looks exactly like one nobody has
    reached yet.
    """
    claimed = await owner.fetchval(
        "SELECT attempts FROM outbox WHERE assertion_id = $1::uuid", assertion_id
    )
    return int(claimed)
