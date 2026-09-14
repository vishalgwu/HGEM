"""`make seed`, run twice.  BUILD_NOTEBOOK.md S3.6

S3.6's DONE WHEN is one sentence - "`make seed` is idempotent: running it twice
leaves the same row count" - and `test_a_second_run_moves_no_row_count` is
exactly that.

**Run as a subprocess, not imported.** The claim is about `make seed`, which is
`python scripts/seed_demo_tenant.py`; importing `seed()` and awaiting it would
test a function that resembles the command. `tests/fixtures/postgres.py` runs
`alembic upgrade head` the same way and for the same reason - if it runs here,
the thing developers type runs.

The rest of the module is the END OF DAY 3 CHECK: "you can write an assertion to
Postgres and read it back with provenance." The seed is the only artifact that
exercises S3.2 through S3.5 together, so it is the right place to check that the
pieces actually compose.

The fixtures moved to `tests/fixtures/seed.py` at S4.2, when incumbent retrieval
became the second suite that wants a seeded incumbent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import asyncpg

from fixtures.seed import run_seed

if TYPE_CHECKING:
    pass

COUNTED: Final = ("entity", "assertion", "provenance", "outbox")


async def row_counts(owner: asyncpg.Connection, tenant: str) -> dict[str, int]:
    """Count the demo tenant's rows in every table the seed writes.

    `set_config` first: `FORCE ROW LEVEL SECURITY` binds the owner to its own
    policies, so a count taken under another tenant's setting is a count of
    nothing. `outbox` and `provenance` have no tenant column and are reached
    through the assertions that do.
    """
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
    scoped = {
        "entity": "SELECT count(*) FROM entity WHERE tenant_id = $1::uuid",
        "assertion": "SELECT count(*) FROM assertion WHERE tenant_id = $1::uuid",
        "provenance": (
            "SELECT count(*) FROM provenance WHERE assertion_id IN"
            " (SELECT id FROM assertion WHERE tenant_id = $1::uuid)"
        ),
        "outbox": (
            "SELECT count(*) FROM outbox WHERE assertion_id IN"
            " (SELECT id FROM assertion WHERE tenant_id = $1::uuid)"
        ),
    }
    return {table: int(await owner.fetchval(scoped[table], tenant)) for table in COUNTED}


class TestIdempotence:
    async def test_a_second_run_moves_no_row_count(
        self, postgres_dsn: str, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """S3.6's DONE WHEN.

        It holds without a single existence check in the script: every id is a
        `uuid5` of the demo slug and a stable key, so the second run collides at
        every insert and `ON CONFLICT DO NOTHING` does nothing. That is the same
        property S3.2 and S3.3 built for the relay's replay, reaching a second
        caller unchanged.
        """
        owner, tenant = demo
        before = await row_counts(owner, tenant)

        run_seed(postgres_dsn)

        assert await row_counts(owner, tenant) == before

    async def test_the_second_run_releases_and_retires_nothing(self, postgres_dsn: str) -> None:
        """The counts the script prints are the ones a person reads.

        A re-run that wrote nothing but claimed to have released twenty-eight
        facts would be idempotent and still misleading. "submitted" rather than
        "written" for the same reason: on a second run all twenty-eight are
        handed to the store and none of them land.
        """
        output = run_seed(postgres_dsn)

        assert "0 released this run" in output
        assert "superseded  0 this run" in output


class TestTheEndOfDayThreeCheck:
    async def test_every_seeded_assertion_is_visible(
        self, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """The relay ran. Without it the seed would leave a database in which
        nothing is retrievable, which is the state S3.3 exists to make safe and
        a useless demo."""
        owner, tenant = demo

        invisible = await owner.fetchval(
            "SELECT count(*) FROM assertion WHERE tenant_id = $1::uuid AND NOT visible", tenant
        )

        assert invisible == 0

    async def test_every_seeded_assertion_has_provenance_with_a_real_span(
        self, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """ "Read it back with provenance", and the span is not decoration.

        `verbatim` is the source text at the span (ADR-0007), so a citation
        whose range is empty is a fact quoting nothing - which `RULES.md` §1.1
        treats exactly as unsourced.
        """
        owner, tenant = demo

        unsourced = await owner.fetchval(
            "SELECT count(*) FROM assertion a WHERE a.tenant_id = $1::uuid AND NOT EXISTS ("
            " SELECT 1 FROM provenance p WHERE p.assertion_id = a.id"
            " AND upper(p.source_span) > lower(p.source_span) AND length(p.verbatim) > 0)",
            tenant,
        )

        assert unsourced == 0

    async def test_the_seeded_facts_span_four_impact_levels(
        self, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """`risk` is the impact floor from `MEMORY_ENGINE.md` §3.3, so a demo
        whose facts were all one impact would exercise one row of the decision
        matrix - and the matrix is the product."""
        owner, tenant = demo

        floors = await owner.fetch(
            "SELECT DISTINCT risk FROM assertion WHERE tenant_id = $1::uuid ORDER BY risk", tenant
        )

        assert [round(float(row["risk"]), 2) for row in floors] == [0.15, 0.35, 0.60, 0.80]

    async def test_the_moved_facts_are_retired_and_still_recoverable(
        self, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """`ADR-0002`, demonstrable on seeded data.

        Two facts are superseded, both `ONE_PER_TIME` predicates that a second
        call replaced. §2.3 abuts the intervals, so the retired row's `valid_to`
        is exactly its successor's `valid_from` - which is what makes a
        point-in-time query have no gap and no overlap.
        """
        owner, tenant = demo

        retired = await owner.fetch(
            "SELECT predicate, valid_to, superseded_by FROM assertion"
            " WHERE tenant_id = $1::uuid AND valid_to IS NOT NULL ORDER BY predicate",
            tenant,
        )

        assert [row["predicate"] for row in retired] == ["home_address", "preferred_pharmacy"]
        for row in retired:
            successor = await owner.fetchrow(
                "SELECT valid_from FROM assertion WHERE id = $1", row["superseded_by"]
            )
            assert successor is not None
            assert row["valid_to"] == successor["valid_from"]

    async def test_nothing_was_deleted(self, demo: tuple[asyncpg.Connection, str]) -> None:
        """`RULES.md` non-negotiable #2. The retired rows are still there, which
        is what a point-in-time query reads."""
        owner, tenant = demo

        total = await owner.fetchval(
            "SELECT count(*) FROM assertion WHERE tenant_id = $1::uuid", tenant
        )

        assert total == 28
