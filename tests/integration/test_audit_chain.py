"""The audit chain against real Postgres.  BUILD_NOTEBOOK.md S5.5

S5.5's DONE WHEN: "invariant I5 test passes, and tampering with one payload row
makes `verify_chain` report the exact break point." The arithmetic half is
`tests/unit/test_i5_audit.py`; this is the half that needs a database, and there
are three separate reasons it does.

**The round trip through `JSONB` is the risk the unit tests cannot see.** The
digest is taken over canonical JSON and re-taken over whatever Postgres hands
back. `JSONB` does not store an object as text - it normalises key order,
whitespace and number formatting - so a payload that survives `json.loads(
json.dumps(x))` can still come back different from the column. Only a real
column proves otherwise.

**The tamper has to be a real `UPDATE`.** Editing a model in memory shows that
`verify_chain` compares digests; editing the row shows that what it compares
them *to* is what the database actually holds. The table's grants forbid the
application role from doing it at all, so the tamper is issued as the owner -
which is the threat model the chain exists for: someone with more access than
the app has.

**RLS makes "whose chain" a real question.** `audit_event` is tenant-scoped, so
a connection without `app.tenant_id` reads zero links and verifies vacuously.
That is worth pinning, because "verified" on an empty read looks identical to
"verified" on a good chain unless `checked` is consulted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.observability import GENESIS, ChainVerification, digest_for, verify_chain
from guardmem_core.observability.audit_store import append, read_chain, verify_tenant_chain
from guardmem_core.types import TenantId, TraceId

if TYPE_CHECKING:
    import asyncpg

TRACE: Final = TraceId("tr_audit_it")
WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
TIMEOUT: Final = 5.0

# A payload with something of every JSON shape in it, because the question this
# suite answers is what survives `JSONB` - a flat `{"n": 1}` would prove almost
# nothing.
RICH: Final[dict[str, object]] = {
    "decision": "auto_write",
    "reason_codes": ["C_AT_OR_ABOVE_TAU_HI", "R_BELOW_RHO_LO"],
    "confidence": {"confidence": 0.8534, "weights_version": "v1", "schema_fit": 1.0},
    "escalated_from": None,
    "already_escalated": False,
    "big": 10**18,
    "unicode": "Dr. Alvaréz — clinic",
}


async def write_links(pool: asyncpg.Pool, tenant: str, *payloads: dict[str, object]) -> list[int]:
    """Append each payload to the tenant's chain; return the assigned `seq`s."""
    written: list[int] = []
    async with tenant_transaction(pool, TenantId(tenant), timeout_s=TIMEOUT) as connection:
        for payload in payloads:
            link = await append(
                connection,
                tenant_id=TenantId(tenant),
                trace_id=TRACE,
                kind="DECISION",
                payload=payload,
                created_at=WHEN,
                timeout_s=TIMEOUT,
            )
            assert link.seq is not None
            written.append(link.seq)
    return written


async def verify(pool: asyncpg.Pool, tenant: str) -> ChainVerification:
    async with tenant_transaction(pool, TenantId(tenant), timeout_s=TIMEOUT) as connection:
        return await verify_tenant_chain(connection, TenantId(tenant), timeout_s=TIMEOUT)


class TestTheChainSurvivesPostgres:
    async def test_a_written_chain_verifies(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """I5 end to end: written through `JSONB`, read back, recomputed."""
        await write_links(pool, tenancy["tenant"], RICH, {"n": 2}, {"n": 3})

        result = await verify(pool, tenancy["tenant"])

        assert result.verified
        assert result.checked == 3

    async def test_the_payload_comes_back_unchanged(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """The round trip the digest depends on, asserted directly.

        If `JSONB` altered a number's formatting or dropped a key, the chain
        would fail to verify and the reason would be invisible in a digest
        mismatch. This says which value moved.
        """
        await write_links(pool, tenancy["tenant"], RICH)

        async with tenant_transaction(
            pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
        ) as connection:
            links = await read_chain(connection, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT)

        assert links[0].payload == RICH

    async def test_the_first_link_follows_genesis(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        await write_links(pool, tenancy["tenant"], {"n": 1})

        async with tenant_transaction(
            pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
        ) as connection:
            links = await read_chain(connection, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT)

        assert links[0].prev_digest == GENESIS

    async def test_two_tenants_keep_independent_chains(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`verify_chain(tenant_id)` is only a meaningful question if they are.

        Both chains start at genesis and neither link names the other's digest,
        even though `seq` is a single global `BIGSERIAL` and the rows interleave.
        """
        await write_links(pool, tenancy["tenant"], {"n": 1})
        await write_links(pool, tenancy["other"], {"n": 1})
        await write_links(pool, tenancy["tenant"], {"n": 2})

        first = await verify(pool, tenancy["tenant"])
        second = await verify(pool, tenancy["other"])

        assert first.verified and first.checked == 2
        assert second.verified and second.checked == 1

    async def test_an_untouched_tenant_verifies_vacuously(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """And `checked` is the only thing that distinguishes it from a real
        chain - which is why `ChainVerification` carries it."""
        result = await verify(pool, tenancy["other"])

        assert result.verified
        assert result.checked == 0


class TestTamperingIsCaughtAtTheRightRow:
    """S5.5's DONE WHEN, against rows rather than models."""

    async def test_an_edited_payload_names_its_own_seq(
        self, pool: asyncpg.Pool, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """The DONE WHEN, stated as the step states it.

        The `UPDATE` is issued as the table owner because the application role
        does not hold one - `0001_initial` revokes UPDATE and DELETE on
        `audit_event`. That is the threat this detects: somebody with more
        access than the application has.
        """
        seqs = await write_links(pool, tenancy["tenant"], {"n": 1}, {"n": 2}, {"n": 3})

        await owner.execute(
            "UPDATE audit_event SET payload = $1::jsonb WHERE seq = $2",
            json.dumps({"n": 99}),
            seqs[1],
        )
        result = await verify(pool, tenancy["tenant"])

        assert not result.verified
        assert result.broken_at == seqs[1]

    async def test_a_recomputed_digest_moves_the_break_to_the_next_row(
        self, pool: asyncpg.Pool, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """A tamperer who knows the scheme fixes the digest too.

        What they cannot fix without rewriting the rest of the chain is the
        *next* row's `prev_digest`, and that is where the break surfaces.
        """
        seqs = await write_links(pool, tenancy["tenant"], {"n": 1}, {"n": 2}, {"n": 3})
        forged: dict[str, object] = {"n": 99}

        async with tenant_transaction(
            pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
        ) as connection:
            links = await read_chain(connection, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT)
        await owner.execute(
            "UPDATE audit_event SET payload = $1::jsonb, digest = $2 WHERE seq = $3",
            json.dumps(forged),
            bytes.fromhex(digest_for(forged, links[1].prev_digest)),
            seqs[1],
        )
        result = await verify(pool, tenancy["tenant"])

        assert not result.verified
        assert result.broken_at == seqs[2]

    async def test_a_deleted_row_is_caught(
        self, pool: asyncpg.Pool, owner: asyncpg.Connection, tenancy: dict[str, str]
    ) -> None:
        """Append-only is enforced by the grants; this is what makes the grant
        a belt rather than the only defence."""
        seqs = await write_links(pool, tenancy["tenant"], {"n": 1}, {"n": 2}, {"n": 3})

        await owner.execute("DELETE FROM audit_event WHERE seq = $1", seqs[1])
        result = await verify(pool, tenancy["tenant"])

        assert not result.verified
        assert result.broken_at == seqs[2]

    async def test_the_application_role_cannot_tamper_at_all(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`RULES.md` non-negotiable #4, checked at the role rather than trusted.

        A chain whose links can be edited by the process that writes them
        verifies nothing, so this is the property the whole scheme rests on.
        """
        seqs = await write_links(pool, tenancy["tenant"], {"n": 1})

        with pytest.raises(Exception, match="permission denied"):
            async with tenant_transaction(
                pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
            ) as connection:
                await connection.execute(
                    "UPDATE audit_event SET payload = '{}'::jsonb WHERE seq = $1", seqs[0]
                )

    async def test_the_application_role_cannot_delete_either(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        seqs = await write_links(pool, tenancy["tenant"], {"n": 1})

        with pytest.raises(Exception, match="permission denied"):
            async with tenant_transaction(
                pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
            ) as connection:
                await connection.execute("DELETE FROM audit_event WHERE seq = $1", seqs[0])


class TestItCommitsWithTheStateChange:
    """`RULES.md` non-negotiable #4: "not after, not best-effort"."""

    async def test_a_rolled_back_transaction_leaves_no_link(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """The property `append(connection, ...)` exists to make structural.

        An audit module that acquired its own connection would commit the row
        independently, leaving a record of a state change that never happened -
        which is worse than no record, because it is a record that lies.
        """
        with pytest.raises(RuntimeError, match="deliberate"):
            async with tenant_transaction(
                pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
            ) as connection:
                await append(
                    connection,
                    tenant_id=TenantId(tenancy["tenant"]),
                    trace_id=TRACE,
                    kind="DECISION",
                    payload={"n": 1},
                    created_at=WHEN,
                    timeout_s=TIMEOUT,
                )
                raise RuntimeError("deliberate: the state change failed after the audit row")

        result = await verify(pool, tenancy["tenant"])

        assert result.checked == 0, "the audit row outlived the transaction that wrote it"

    async def test_sequential_appends_chain_correctly_across_transactions(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """Each `append` reads the head the previous transaction committed."""
        for index in range(4):
            await write_links(pool, tenancy["tenant"], {"n": index})

        async with tenant_transaction(
            pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
        ) as connection:
            links = await read_chain(connection, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT)

        assert verify_chain(links).verified
        assert [link.payload["n"] for link in links] == [0, 1, 2, 3]
