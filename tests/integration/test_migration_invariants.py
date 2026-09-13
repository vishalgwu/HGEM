"""The invariants `0001_initial` enforces, checked against a real Postgres. S3.1

S3.1's DONE WHEN is three manual commands: `make migrate` runs clean, `\\d
assertion` shows the bitemporal columns, and a DELETE as the app role raises a
permission error. Manual checks are run once, by the person who wrote the thing
they are checking. These are the same assertions as a test.

Four of them are `RULES.md` non-negotiables, and the point of putting them in
the *database* rather than in the application is that a future service, a
migration, a psql session or a bug cannot route around them:

- #1 no unsourced write - every assertion has at least one provenance row,
  enforced by a deferred constraint trigger that fires at COMMIT;
- #2 no destructive mutation - `DELETE` on `assertion` is revoked from the app
  role, and retirement is an UPDATE of `valid_to`;
- #4 the audit chain is append-only - INSERT only, no UPDATE, no DELETE;
- §4 tenant isolation - row-level security, failing closed when no tenant is set.

**Skipped, not failed, when no database is reachable.** `RULES.md` §5 wants the
integration suite green with no skips *on main*, which is a CI-with-a-service
statement; S3.2 is the step that brings testcontainers in and makes that
enforceable. Until then a developer without `make dev` running should get a
skip, not a wall of red that trains them to ignore the suite.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import uuid4

import asyncpg
import pytest

from guardmem_core.settings import get_settings

# The least-privilege role the application connects as, created by
# `infra/docker/initdb/02-app-role.sql`. A development credential for a
# container that listens on localhost, committed for the same reason the
# compose file's `POSTGRES_PASSWORD` is.
_APP_ROLE: Final = "guardmem_app"
_APP_PASSWORD: Final = "guardmem_app"


def _endpoint() -> dict[str, Any]:
    """Host, port, database and credentials from `GM_DATABASE_URL`.

    `PostgresDsn` is a `MultiHostUrl` - Postgres connection strings may carry
    several hosts for failover - so the fields live under `hosts()`, not on the
    URL itself. Only the first is used: the dev stack is one container, and a
    multi-host DSN is a production concern for whichever pooler S26.2 puts in
    front.
    """
    url = get_settings().database_url
    first = url.hosts()[0]
    return {
        "host": first["host"] or "localhost",
        "port": first["port"] or 5432,
        "user": first["username"],
        "password": first["password"],
        "database": (url.path or "/guardmem").lstrip("/"),
    }


def _dsn(user: str | None = None, password: str | None = None) -> str:
    """The settings DSN as asyncpg wants it, optionally as another role.

    `GM_DATABASE_URL` carries SQLAlchemy's `+asyncpg` driver marker, which
    asyncpg itself does not understand, so the string is rebuilt rather than
    passed through.
    """
    parts = _endpoint()
    if user is None:
        user, password = parts["user"], parts["password"]
    return f"postgresql://{user}:{password}@{parts['host']}:{parts['port']}/{parts['database']}"


def _database_reachable() -> bool:
    """Is anything listening where the settings say the database is?"""
    parts = _endpoint()
    try:
        with socket.create_connection((parts["host"], parts["port"]), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _database_reachable(),
        reason="no database at GM_DATABASE_URL; start it with `make dev`",
    ),
]


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    """A connection as the table owner, used to seed and to clean up."""
    connection = await asyncpg.connect(_dsn())
    try:
        yield connection
    finally:
        await connection.close()


async def _insert_fixture_rows(owner: asyncpg.Connection, ids: dict[str, str]) -> None:
    """Two tenants, one entity, and one properly sourced assertion.

    The assertion and its provenance go in one transaction because they have
    to: the deferred trigger looks for a citation at COMMIT, so an assertion
    inserted on its own is rejected - which is the invariant `TestNoUnsourcedWrite`
    exercises deliberately.
    """
    await owner.execute(
        "INSERT INTO tenant (id, slug) VALUES ($1::uuid, $2), ($3::uuid, $4)",
        ids["tenant"],
        f"t-{ids['tenant'][:8]}",
        ids["other_tenant"],
        f"o-{ids['other_tenant'][:8]}",
    )
    await owner.execute(
        "INSERT INTO entity (id, tenant_id, type, canonical_name)"
        " VALUES ($1::uuid, $2::uuid, 'Patient', 'Patient 8812')",
        ids["entity"],
        ids["tenant"],
    )
    async with owner.transaction():
        await owner.execute(
            "INSERT INTO assertion (id, tenant_id, namespace, subject_id, predicate,"
            " object_json, confidence, risk, valid_from, trace_id, visible)"
            " VALUES ($1::uuid, $2::uuid, 'patient:8812', $3::uuid, 'allergy',"
            " '\"penicillin\"'::jsonb, 0.94, 0.82, now(), 'tr_test', true)",
            ids["assertion"],
            ids["tenant"],
            ids["entity"],
        )
        await owner.execute(
            "INSERT INTO provenance (id, assertion_id, source_hash, source_span,"
            " source_tier, verbatim, alignment, captured_at)"
            " VALUES ($1::uuid, $2::uuid, 'sha256:abc', int4range(13, 35),"
            " 'verified_user', 'allergic to penicillin', 1.0, now())",
            ids["provenance"],
            ids["assertion"],
        )


async def _remove_fixture_rows(owner: asyncpg.Connection, ids: dict[str, str]) -> None:
    """Children first, because every foreign key here is enforced."""
    await owner.execute("DELETE FROM audit_event WHERE tenant_id = $1::uuid", ids["tenant"])
    await owner.execute("DELETE FROM provenance WHERE assertion_id = $1::uuid", ids["assertion"])
    await owner.execute("DELETE FROM assertion WHERE id = $1::uuid", ids["assertion"])
    await owner.execute("DELETE FROM entity WHERE id = $1::uuid", ids["entity"])
    await owner.execute(
        "DELETE FROM tenant WHERE id = ANY($1::uuid[])",
        [ids["tenant"], ids["other_tenant"]],
    )


@pytest.fixture
async def seeded(owner: asyncpg.Connection) -> AsyncIterator[dict[str, str]]:
    """One tenant, one entity, one sourced assertion - then remove them.

    Fresh UUIDs per run, so a failed run never poisons the next one and two
    runs can overlap. `FORCE ROW LEVEL SECURITY` applies to the owner too, so
    even the cleanup has to declare which tenant it is acting as.
    """
    ids = {
        "tenant": str(uuid4()),
        "other_tenant": str(uuid4()),
        "entity": str(uuid4()),
        "assertion": str(uuid4()),
        "provenance": str(uuid4()),
    }
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", ids["tenant"])
    await _insert_fixture_rows(owner, ids)
    try:
        yield ids
    finally:
        await _remove_fixture_rows(owner, ids)


@pytest.fixture
async def app_role(seeded: dict[str, str]) -> AsyncIterator[asyncpg.Connection]:
    """A connection as the application role, scoped to the seeded tenant."""
    connection = await asyncpg.connect(_dsn(_APP_ROLE, _APP_PASSWORD))
    await connection.execute("SELECT set_config('app.tenant_id', $1, false)", seeded["tenant"])
    try:
        yield connection
    finally:
        await connection.close()


class TestTheSchemaItself:
    async def test_the_assertion_table_is_bitemporal(self, owner: asyncpg.Connection) -> None:
        """S3.1's second DONE WHEN, as an assertion rather than as `\\d`."""
        columns = {
            row["column_name"]: row["is_nullable"]
            for row in await owner.fetch(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_name = 'assertion'"
            )
        }
        # World time and system time, which is what makes "what did the agent
        # believe on 2026-03-14?" a normal query (ADR-0002).
        assert columns["valid_from"] == "NO"
        assert columns["valid_to"] == "YES"
        assert columns["recorded_at"] == "NO"
        assert columns["retracted_at"] == "YES"
        assert "superseded_by" in columns
        assert "embedding" in columns

    async def test_the_partial_indexes_exclude_retired_and_invisible_rows(
        self, owner: asyncpg.Connection
    ) -> None:
        """Invariant I6, at the index level.

        Both retrieval indexes are partial on `valid_to IS NULL AND visible`. A
        full index would still be *correct* - the query filters anyway - but it
        would let a planner choose a scan that surfaces a superseded row, and it
        would keep tombstones in the HNSW graph forever.
        """
        definitions = {
            row["indexname"]: row["indexdef"]
            for row in await owner.fetch(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'assertion'"
            )
        }
        for name in ("assertion_live_idx", "assertion_hnsw"):
            assert "valid_to IS NULL" in definitions[name]
            assert "visible" in definitions[name]


class TestNoUnsourcedWrite:
    """`RULES.md` non-negotiable #1, and `MEMORY_ENGINE.md` §1.3."""

    async def test_an_assertion_without_provenance_cannot_commit(
        self, owner: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        with pytest.raises(asyncpg.RaiseError, match="no provenance"):
            async with owner.transaction():
                await owner.execute(
                    "INSERT INTO assertion (id, tenant_id, namespace, subject_id, predicate,"
                    " object_json, confidence, risk, valid_from, trace_id)"
                    " VALUES ($1::uuid, $2::uuid, 'patient:8812', $3::uuid, 'allergy',"
                    " '\"sulfa\"'::jsonb, 0.5, 0.5, now(), 'tr_unsourced')",
                    str(uuid4()),
                    seeded["tenant"],
                    seeded["entity"],
                )

    async def test_a_zero_width_span_is_refused(
        self, owner: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        """An empty range quotes nothing, and `int4range(5, 5)` is empty.

        The first draft of this constraint compared `lower()` and `upper()`
        without `NOT isempty(...)`. Both return NULL on an empty range, so the
        CHECK evaluated to NULL - which SQL treats as satisfied. It stored a
        span that cited nothing.
        """
        with pytest.raises(asyncpg.CheckViolationError, match="span_non_empty"):
            await owner.execute(
                "INSERT INTO provenance (id, assertion_id, source_hash, source_span,"
                " source_tier, verbatim, captured_at)"
                " VALUES ($1::uuid, $2::uuid, 'sha256:abc', int4range(5, 5),"
                " 'verified_user', 'x', now())",
                str(uuid4()),
                seeded["assertion"],
            )


class TestNoDestructiveMutation:
    """`RULES.md` non-negotiable #2. S3.1's third DONE WHEN."""

    async def test_delete_on_assertion_is_refused_for_the_app_role(
        self, app_role: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_role.execute("DELETE FROM assertion WHERE id = $1::uuid", seeded["assertion"])

    async def test_retirement_by_update_is_allowed(
        self, app_role: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        """The supported path: `valid_to = now()`, never a DELETE."""
        await app_role.execute(
            "UPDATE assertion SET valid_to = now() WHERE id = $1::uuid", seeded["assertion"]
        )
        retired = await app_role.fetchval(
            "SELECT valid_to FROM assertion WHERE id = $1::uuid", seeded["assertion"]
        )
        assert retired is not None


class TestTheAuditChainIsAppendOnly:
    """`RULES.md` non-negotiable #4. A chain whose links can be edited proves nothing."""

    async def test_insert_is_allowed(
        self, app_role: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        await app_role.execute(
            "INSERT INTO audit_event (tenant_id, trace_id, kind, payload, prev_digest, digest)"
            " VALUES ($1::uuid, 'tr_test', 'WRITE', '{}'::jsonb, '\\x00', '\\x01')",
            seeded["tenant"],
        )

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE audit_event SET payload = '{}'::jsonb WHERE trace_id = 'tr_test'",
            "DELETE FROM audit_event WHERE trace_id = 'tr_test'",
        ],
    )
    async def test_tampering_is_refused(self, app_role: asyncpg.Connection, statement: str) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_role.execute(statement)


class TestTenantIsolation:
    """`RULES.md` §4, the row-level-security half of defence in depth."""

    async def test_a_tenant_sees_its_own_rows(
        self, app_role: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        count = await app_role.fetchval(
            "SELECT count(*) FROM assertion WHERE id = $1::uuid", seeded["assertion"]
        )
        assert count == 1

    async def test_another_tenant_sees_nothing(
        self, app_role: asyncpg.Connection, seeded: dict[str, str]
    ) -> None:
        await app_role.execute(
            "SELECT set_config('app.tenant_id', $1, false)", seeded["other_tenant"]
        )
        assert await app_role.fetchval("SELECT count(*) FROM assertion") == 0

    async def test_an_unset_tenant_fails_closed(self, app_role: asyncpg.Connection) -> None:
        """Zero rows, not an exception.

        A session that has set `app.tenant_id` and then `RESET` it reads back
        the **empty string**, not NULL - so `''::uuid` raised `invalid input
        syntax` and isolation surfaced as a 500 instead of as no rows. `NULLIF`
        in the policy collapses both cases to NULL. Measured: the first version
        of the policy did exactly that.
        """
        await app_role.execute("SELECT set_config('app.tenant_id', '', false)")
        assert await app_role.fetchval("SELECT count(*) FROM assertion") == 0

    @pytest.mark.parametrize("value", ["   ", "not-a-uuid", "0"])
    async def test_a_malformed_tenant_raises_rather_than_returning_nothing(
        self, app_role: asyncpg.Connection, value: str
    ) -> None:
        """The other half of the decision, and it is a decision.

        A tenant id that is present but not a UUID is a bug in tenant
        propagation, not a session that has not set one yet. Widening the
        `NULLIF` to swallow anything uncastable would turn that bug into an
        empty result - which, in a product whose subject is remembered facts,
        reads as "this patient has no memories" rather than as "something is
        broken". Unset is silence; malformed is an error. Either way nothing
        leaks, so both are fail-closed; only one is diagnosable.
        """
        await app_role.execute("SELECT set_config('app.tenant_id', $1, false)", value)
        with pytest.raises(asyncpg.InvalidTextRepresentationError):
            await app_role.fetchval("SELECT count(*) FROM assertion")
