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
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid4

import asyncpg
import pytest

from fixtures.fakes import FakeEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import create_pool
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

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

NS: Final = Namespace("patient:8812")
WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
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

    That `DELETE` is available here at all and nowhere in the store is the
    point: this connection is the *owner*, and `RULES.md` non-negotiable #2
    revokes the same statement from `guardmem_app`, which is the role everything
    under test uses.
    """
    await owner.execute(
        "DELETE FROM provenance WHERE assertion_id IN"
        " (SELECT id FROM assertion WHERE tenant_id = $1::uuid)",
        ids["tenant"],
    )
    await owner.execute(
        "UPDATE assertion SET superseded_by = NULL WHERE tenant_id = $1::uuid", ids["tenant"]
    )
    await owner.execute("DELETE FROM assertion WHERE tenant_id = $1::uuid", ids["tenant"])
    await owner.execute("DELETE FROM entity WHERE tenant_id = $1::uuid", ids["tenant"])
    await owner.execute(
        "DELETE FROM tenant WHERE id = ANY($1::uuid[])", [ids["tenant"], ids["other"]]
    )


@pytest.fixture
async def pool(app_role_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """A pool as the least-privilege role, with the `vector` codec registered."""
    created = await create_pool(app_role_dsn, min_size=1, max_size=4)
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture
def embedder() -> FakeEmbedder:
    """Deterministic 1024-dimension vectors: same text in, same vector out."""
    return FakeEmbedder()


@pytest.fixture
def store(pool: asyncpg.Pool, embedder: FakeEmbedder, tenancy: dict[str, str]) -> PgVectorStore:
    """The subject, bound to this test's tenant."""
    return PgVectorStore(pool, embedder, tenant_id=TenantId(tenancy["tenant"]), timeout_s=TIMEOUT_S)


def assertion(
    tenancy: dict[str, str],
    *,
    predicate: str = "allergy",
    obj: str = "penicillin",
    valid_from: datetime = WHEN,
) -> StoredAssertion:
    """A well-formed, sourced assertion in this test's tenant."""
    return StoredAssertion(
        assertion_id=AssertionId(str(uuid4())),
        tenant_id=TenantId(tenancy["tenant"]),
        namespace=NS,
        subject_id=EntityId(tenancy["entity"]),
        predicate=predicate,
        object=obj,
        confidence=0.94,
        risk=0.82,
        valid_from=valid_from,
        recorded_at=WHEN,
        provenance=[
            Provenance(
                source_hash="sha256:abc",
                source_span=(13, 35),
                source_tier=SourceTier.VERIFIED_USER,
                verbatim=f"allergic to {obj}",
                alignment=0.97,
                captured_at=WHEN,
            )
        ],
        trace_id=TraceId("tr_s32"),
    )


async def reveal(owner: asyncpg.Connection, *ids: str) -> None:
    """Do what the S3.3 outbox relay will do: flip `visible` once both sides land.

    Standing in for it here rather than waiting for S3.3, because every read
    path filters on `visible` and a store whose writes are correctly invisible
    would otherwise have no observable search behaviour at all. The relay is the
    only thing that may do this in production, which is why it is an owner
    statement in a test rather than a method on the store.
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
    embedder: FakeEmbedder,
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
    return [assertion.assertion_id for assertion in found]
