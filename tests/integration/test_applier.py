"""The applier, against a real Postgres.  ADR-0010

**This is the first caller the audit chain has ever had.**
`observability/audit.py` and `audit_store.append` were built, tested and
tamper-evident at S5.5, and no write path appended an event - open item #18,
carried for eleven steps. Everything here exercises that.

Integration rather than unit, and not by preference: the property under test is
that the assertion row and its audit event **commit together**, which is a fact
about a Postgres transaction and about nothing else. A fake store can be made to
assert that two methods were called; only a database can be asked whether a
rollback took both away.

What is deliberately not here is the decision itself. The records below are
built by hand, because `run()` needs a model and this is about what happens
*after* it - `tests/integration/test_mcp_memory_tools.py` covers the seam where
the two meet.
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from fixtures.assertions import WHEN, stored_assertion
from fixtures.conflict import candidate
from fixtures.decisions import confidence, conflict, risk
from fixtures.pgvector import TIMEOUT_S
from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.memory.applier import apply
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.observability.audit_store import read_chain
from guardmem_core.pipeline.per_candidate import GovernedCandidate
from guardmem_core.schemas.verdict import Decision, DecisionRecord
from guardmem_core.types import AssertionId, EntityId, TenantId, TraceId

TRACE = TraceId("tr_applier")


def governed(
    *,
    decision: Decision = Decision.AUTO_WRITE,
    hint: str = "coexist",
    incumbent_id: AssertionId | None = None,
    entity: str = "e-1",
    predicate: str = "allergy",
) -> GovernedCandidate:
    """One decided candidate, built without a model."""
    return GovernedCandidate(
        candidate=candidate(predicate=predicate, obj="penicillin"),
        subject_id=EntityId(entity),
        record=DecisionRecord(
            decision=decision,
            reason_codes=["TEST"],
            confidence=confidence(),
            risk=risk(),
            conflict=conflict(hint=hint, incumbent_assertion_id=incumbent_id),
            thresholds_version="v1",
            policy_version="none",
        ),
    )


@pytest.fixture
def store(pool: asyncpg.Pool, tenancy: dict[str, str]) -> PgVectorStore:
    """The subject's store, bound to this test's tenant."""
    return PgVectorStore(
        pool, HashEmbedder(), tenant_id=TenantId(tenancy["tenant"]), timeout_s=TIMEOUT_S
    )


async def chain(pool: asyncpg.Pool, tenant: TenantId) -> list[str]:
    """Every audit event kind on this tenant's chain, in order."""
    async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
        return [event.kind for event in await read_chain(conn, tenant, timeout_s=TIMEOUT_S)]


class TestAnApprovedCandidateBecomesARowAndAnEvent:
    async def test_the_assertion_is_written(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """S6.3's DONE WHEN, in the half that is code: the row exists, with the
        confidence the decision was taken on."""
        tenant = TenantId(tenancy["tenant"])

        applied = await apply(
            governed(entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        assert applied.assertion_id is not None
        assert applied.reason is None
        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            row = await conn.fetchrow(
                "SELECT predicate, confidence, visible FROM assertion WHERE id = $1::uuid",
                str(applied.assertion_id),
            )
        assert row is not None
        assert row["predicate"] == "allergy"
        assert row["visible"] is False, "the relay reveals a row, never the applier"

    async def test_the_citation_is_written_with_it(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`assertion_requires_provenance` is DEFERRABLE INITIALLY DEFERRED and
        fires at COMMIT, so a row that got this far has one - but asserting it
        is asserting `RULES.md` non-negotiable #1, which is worth its own line.
        """
        tenant = TenantId(tenancy["tenant"])

        applied = await apply(
            governed(entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            verbatim = await conn.fetchval(
                "SELECT verbatim FROM provenance WHERE assertion_id = $1::uuid",
                str(applied.assertion_id),
            )
        assert verbatim, "an assertion with no citation is RULES.md non-negotiable #1"

    async def test_the_chain_records_the_decision_and_the_write(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """**Open item #18, closed.** The chain has existed since S5.5 with no
        caller; this is the first thing that ever extends it."""
        tenant = TenantId(tenancy["tenant"])

        await apply(
            governed(entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        assert await chain(pool, tenant) == ["DECISION", "WRITE"]


class TestTheDecisionsThatWriteNothingAreStillAudited:
    @pytest.mark.parametrize("decision", [Decision.REJECT, Decision.HITL_REVIEW, Decision.ESCALATE])
    async def test_no_row_but_a_decision_event(
        self,
        decision: Decision,
        store: PgVectorStore,
        pool: asyncpg.Pool,
        tenancy: dict[str, str],
    ) -> None:
        """A `REJECT` that leaves no trace cannot be explained to the person
        whose fact was dropped, reviewed, or learned from - and
        `threshold_tuner.py` refits §3.4's cut points from exactly these."""
        tenant = TenantId(tenancy["tenant"])

        applied = await apply(
            governed(decision=decision, entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        assert applied.assertion_id is None
        assert applied.reason == f"decision_{decision.value}"
        assert await chain(pool, tenant) == ["DECISION"]

    async def test_a_merge_is_audited_and_not_applied(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """§2.4's merge is an `UPDATE` of `corroboration_count`, not an insert,
        and a replayed increment is not idempotent the way a replayed insert is.
        Deferred deliberately rather than approximated - the decision is still
        recorded."""
        tenant = TenantId(tenancy["tenant"])

        applied = await apply(
            governed(hint="merge", entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        assert applied.assertion_id is None
        assert applied.reason == "merge_not_implemented"
        assert await chain(pool, tenant) == ["DECISION"]


class TestSupersession:
    async def test_it_retires_the_incumbent_and_records_both(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """§2.3's supersession: the successor lands, the incumbent stops being
        live, and nothing is deleted."""
        tenant = TenantId(tenancy["tenant"])
        incumbent = stored_assertion(
            subject=tenancy["entity"], predicate="home_address", obj="old", visible=True
        )
        await store.upsert([incumbent])

        applied = await apply(
            governed(
                hint="supersede",
                incumbent_id=incumbent.assertion_id,
                entity=tenancy["entity"],
                predicate="home_address",
            ),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        assert applied.assertion_id is not None
        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            retired = await conn.fetchrow(
                "SELECT valid_to, superseded_by FROM assertion WHERE id = $1::uuid",
                str(incumbent.assertion_id),
            )
        assert retired is not None, "nothing is ever deleted"
        assert retired["valid_to"] is not None
        assert await chain(pool, tenant) == ["DECISION", "WRITE", "SUPERSEDE"]

    async def test_a_lost_race_rolls_the_whole_apply_back(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """**The property the single transaction exists for.** Superseding an
        assertion that is already retired must not leave the successor stored
        beside it - that is two live values for a `ONE` predicate, invariant I2
        broken by a retry rather than by a bug. And the `DECISION` event that
        was appended first must go with it.
        """
        tenant = TenantId(tenancy["tenant"])
        gone = AssertionId("00000000-0000-4000-8000-00000000dead")
        decided = governed(
            hint="supersede",
            incumbent_id=gone,
            entity=tenancy["entity"],
            predicate="home_address",
        )

        with pytest.raises(ConcurrencyConflict):
            await apply(
                decided,
                store=store,
                pool=pool,
                tenant_id=tenant,
                trace_id=TRACE,
                timeout_s=TIMEOUT_S,
            )

        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            rows = await conn.fetchval(
                "SELECT count(*) FROM assertion WHERE trace_id = $1", str(TRACE)
            )
        assert rows == 0, "the successor must not survive a failed supersession"
        assert await chain(pool, tenant) == [], "and neither may the decision event"


class TestReplay:
    async def test_applying_the_same_decision_twice_writes_one_row(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """The id is a `uuid5` of the trace and the candidate, so a retried
        apply is a no-op rather than a second copy of the fact."""
        tenant = TenantId(tenancy["tenant"])
        decided = governed(entity=tenancy["entity"])

        first = await apply(
            decided, store=store, pool=pool, tenant_id=tenant, trace_id=TRACE, timeout_s=TIMEOUT_S
        )
        second = await apply(
            decided, store=store, pool=pool, tenant_id=tenant, trace_id=TRACE, timeout_s=TIMEOUT_S
        )

        assert first.assertion_id == second.assertion_id
        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM assertion WHERE id = $1::uuid", str(first.assertion_id)
            )
        assert count == 1


class TestValidFromIsWorldTimeNotTheClock:
    async def test_it_comes_from_the_citation(
        self, store: PgVectorStore, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """A fact proposed today about a conversation last week became true last
        week. Using `now()` would make a point-in-time query answer wrongly for
        the window in between."""
        tenant = TenantId(tenancy["tenant"])

        applied = await apply(
            governed(entity=tenancy["entity"]),
            store=store,
            pool=pool,
            tenant_id=tenant,
            trace_id=TRACE,
            timeout_s=TIMEOUT_S,
        )

        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            valid_from = await conn.fetchval(
                "SELECT valid_from FROM assertion WHERE id = $1::uuid",
                str(applied.assertion_id),
            )
        assert valid_from == WHEN, f"expected the citation's capture time, got {valid_from}"
        assert valid_from != datetime.now(UTC)
