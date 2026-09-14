"""Fixtures and builders for exercising `PgVectorStore`.  BUILD_NOTEBOOK.md S3.2

The scaffolding the S3.2 integration suite runs on, kept out of the test module
so that module is assertions and nothing else. `tests/fixtures/postgres.py`
supplies the migrated database this builds on; this supplies a tenant, an
entity, a pool as the least-privilege role, and a store bound to that tenant.

Registered as a plugin from `tests/conftest.py`, for the reason given there: two
files named `conftest` in a tree without `__init__.py` are one module name too
many for `mypy`. Nothing here runs until a test asks for a fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Final
from uuid import uuid4

import asyncpg
import pytest

from fixtures.assertions import NS, WHEN, citation, stored_assertion
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import create_pool
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.types import TenantId

__all__ = [
    "LATER",
    "NS",
    "TIMEOUT_S",
    "WHEN",
    "assertion",
    "reveal",
    "search",
    "write_and_reveal",
]

# `NS` and `WHEN` are re-exported from `fixtures.assertions`, which is where the
# builder that uses them lives; this module owns only the times derived from it.
LATER: Final = WHEN + timedelta(days=30)
TIMEOUT_S: Final = 10.0


@pytest.fixture
async def owner(postgres_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """The table owner. Seeds, reveals and cleans up - never the subject."""
    connection = await asyncpg.connect(postgres_dsn)
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def tenancy(owner: asyncpg.Connection) -> AsyncIterator[dict[str, str]]:
    """A tenant, a second tenant, and an entity - removed afterwards.

    Fresh ids per test, so runs never collide and a failure leaves nothing for
    the next run to trip over. `guardmem_app` holds `SELECT` on `tenant` and no
    more, which is why these are the owner's to create.
    """
    ids = {"tenant": str(uuid4()), "other": str(uuid4()), "entity": str(uuid4())}
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", ids["tenant"])
    await owner.execute(
        "INSERT INTO tenant (id, slug) VALUES ($1::uuid, $2), ($3::uuid, $4)",
        ids["tenant"],
        f"t-{ids['tenant'][:8]}",
        ids["other"],
        f"o-{ids['other'][:8]}",
    )
    await owner.execute(
        "INSERT INTO entity (id, tenant_id, type, canonical_name)"
        " VALUES ($1::uuid, $2::uuid, 'Patient', 'Patient 8812')",
        ids["entity"],
        ids["tenant"],
    )
    try:
        yield ids
    finally:
        await _drop_tenant(owner, ids)


async def _drop_tenant(owner: asyncpg.Connection, ids: dict[str, str]) -> None:
    """Remove one test's rows, children first.

    The `superseded_by` reset is not cleanup pedantry: that column is a
    self-reference on `assertion`, so a supersession chain makes the delete
    order depend on which row points at which. Clearing the pointers first turns
    an ordering problem into a single statement.

    `outbox` joined the list at S3.3, and it has to: `upsert` now enqueues an
    event in the same transaction as the assertion, and `outbox.assertion_id` is
    a foreign key. Leaving those rows behind would not leak a little state - it
    would make every `DELETE FROM assertion` below fail on the reference, so the
    *next* test's tenant would inherit this one's.

    **Both tenants, one at a time, each behind its own `app.tenant_id`** - also
    from S3.3, and the loop is not stylistic. `0001_initial` applies `FORCE ROW
    LEVEL SECURITY`, so the owner is subject to its own policies and a `DELETE`
    only reaches rows the current setting makes visible. A single statement over
    `tenant_id = ANY(...)` would therefore delete the first tenant's rows,
    silently skip the second's, and fail on the foreign key three lines later.
    The second tenant used to hold no rows at all - it existed for a store to be
    refused access to - but the relay is not tenant-bound, so its tests write
    under both.

    `audit_event` joined at S5.5 for the same reason `outbox` did: it carries a
    foreign key to `tenant`, so leaving its rows behind made the final
    `DELETE FROM tenant` fail and every later test inherit this one's tenant.
    Note it is *only* deletable here - `0001_initial` revokes both UPDATE and
    DELETE on it from `guardmem_app`, which is what makes the chain worth
    verifying.

    That `DELETE` is available here at all and nowhere in the store is the
    point: this connection is the *owner*, and `RULES.md` non-negotiable #2
    revokes the same statement from `guardmem_app`, which is the role everything
    under test uses.
    """
    owned = [ids["tenant"], ids["other"]]
    for tenant in owned:
        await owner.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
        await owner.execute(
            "DELETE FROM outbox WHERE assertion_id IN"
            " (SELECT id FROM assertion WHERE tenant_id = $1::uuid)",
            tenant,
        )
        await owner.execute(
            "DELETE FROM provenance WHERE assertion_id IN"
            " (SELECT id FROM assertion WHERE tenant_id = $1::uuid)",
            tenant,
        )
        await owner.execute(
            "UPDATE assertion SET superseded_by = NULL WHERE tenant_id = $1::uuid", tenant
        )
        await owner.execute("DELETE FROM assertion WHERE tenant_id = $1::uuid", tenant)
        await owner.execute("DELETE FROM entity WHERE tenant_id = $1::uuid", tenant)
        await owner.execute("DELETE FROM audit_event WHERE tenant_id = $1::uuid", tenant)
    await owner.execute("DELETE FROM tenant WHERE id = ANY($1::uuid[])", owned)


@pytest.fixture
async def pool(app_role_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """A pool as the least-privilege role, with the `vector` codec registered."""
    created = await create_pool(app_role_dsn, min_size=1, max_size=4)
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture
def embedder() -> HashEmbedder:
    """Deterministic vectors at the dimension the column declares.

    `record=True` because `test_pgvector_store.py` asserts on `texts` to prove
    the store embeds the assertion's rendered text rather than its id. The flag
    is off in the package for the reason `HashEmbedder`'s docstring gives - an
    unbounded recording in a long-lived process - and a test fixture is exactly
    the short-lived caller it is meant for.
    """
    return HashEmbedder(record=True)


@pytest.fixture
def store(pool: asyncpg.Pool, embedder: HashEmbedder, tenancy: dict[str, str]) -> PgVectorStore:
    """The subject, bound to this test's tenant."""
    return PgVectorStore(pool, embedder, tenant_id=TenantId(tenancy["tenant"]), timeout_s=TIMEOUT_S)


def assertion(
    tenancy: dict[str, str],
    *,
    predicate: str = "allergy",
    obj: str = "penicillin",
    valid_from: datetime = WHEN,
) -> StoredAssertion:
    """A well-formed, sourced assertion in this test's tenant.

    A thin wrapper over `fixtures.assertions.stored_assertion`, kept because the
    integration suite addresses tenants through the `tenancy` dict its fixture
    hands out - the shared builder takes ids, and translating them at every call
    site would be the duplication this wrapper removes.
    """
    return stored_assertion(
        tenant_id=TenantId(tenancy["tenant"]),
        namespace=NS,
        subject=tenancy["entity"],
        predicate=predicate,
        obj=obj,
        confidence=0.94,
        risk=0.82,
        valid_from=valid_from,
        provenance=[citation(verbatim=f"allergic to {obj}")],
        trace_id="tr_s32",
    )


async def reveal(owner: asyncpg.Connection, *ids: str) -> None:
    """Flip `visible`, the way the outbox relay does once both sides land.

    Still an owner `UPDATE` rather than a call to the real `OutboxRelay`, even
    though S3.3 built one. That is deliberate: these are the *store's* tests,
    and routing them through the relay would mean a relay bug failed them - so a
    suite about `search` filtering on `visible` would start reporting on
    dispatch instead. `tests/integration/test_outbox_relay.py` exercises the
    real path.

    The relay remains the only thing that may do this in production, which is
    why it is a statement in a fixture and never a method on the store.
    """
    await owner.execute("UPDATE assertion SET visible = true WHERE id = ANY($1::uuid[])", list(ids))


async def write_and_reveal(
    store: PgVectorStore, owner: asyncpg.Connection, *assertions: StoredAssertion
) -> None:
    """Persist assertions and make them retrievable, in the two steps §2.4 wants."""
    await store.upsert(list(assertions))
    await reveal(owner, *(a.assertion_id for a in assertions))


async def search(
    store: PgVectorStore,
    embedder: HashEmbedder,
    text: str,
    *,
    as_of: datetime | None = None,
    k: int = 10,
    filters: dict[str, object] | None = None,
) -> list[str]:
    """Search by the embedding of `text` and return the ids that came back."""
    vector = (await embedder.embed([text]))[0]
    found = await store.search(
        namespace=NS, embedding=vector, k=k, filters=filters or {}, as_of=as_of
    )
    return [hit.assertion.assertion_id for hit in found]
