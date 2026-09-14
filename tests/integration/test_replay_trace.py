"""S5.6's DONE WHEN, against real Postgres.  BUILD_NOTEBOOK.md S5.6

    uv run python scripts/replay_trace.py <trace_id> --tenant <uuid>

"prints 'identical' for a fresh trace". This drives the script's `main` the way
the command line does - argv in, exit code out, stdout captured - because that
is what the step asks for, and a test that called `replay()` directly would
prove the arithmetic while leaving the reading, the RLS scoping and the exit
code untested.

The decision is appended to a real chain and read back out of `JSONB`, so this
also covers the part `tests/unit/` cannot: that a `DecisionRecord` survives
being canonical-JSON hashed, stored, and revalidated. It has thirty-odd nested
fields across three reports, which is exactly the shape `canonical_json` refuses
when something in it does not round-trip.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from fixtures.decisions import confidence, conflict, risk, signals
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.observability.audit_store import append_decision
from guardmem_core.pipeline.l3_score import decide
from guardmem_core.schemas.verdict import Decision, Thresholds
from guardmem_core.settings import get_settings
from guardmem_core.types import TenantId, TraceId

if TYPE_CHECKING:
    import asyncpg

TRACE: Final = TraceId("tr_replay_it")
WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
TIMEOUT: Final = 5.0

# The defaults `Settings` carries, so the script's own `settings.thresholds()`
# reaches the same numbers the record was decided under.
DEFAULTS: Final = Thresholds(
    tau_lo=0.45, tau_mid=0.60, tau_hi=0.78, rho_lo=0.35, rho_hi=0.70, version="v1"
)


@pytest.fixture
def _script_env(app_role_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point `get_settings()` at the testcontainer, as the CLI would be.

    The script reads its DSN from settings rather than taking one, which is
    right for a command and awkward for a test - so the environment is set and
    the cache cleared, which is the same thing a fresh process does.
    """
    monkeypatch.setenv("GM_DATABASE_URL", app_role_dsn)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def record_a_decision(
    pool: asyncpg.Pool, tenant: str, *, c: float = 0.95, r: float = 0.10
) -> Decision:
    """Decide one candidate and append it to the tenant's chain."""
    decided = decide(
        confidence(c, corroboration=0.5507),
        risk(r),
        conflict(),
        DEFAULTS,
        False,
        signals(),
    )
    async with tenant_transaction(pool, TenantId(tenant), timeout_s=TIMEOUT) as connection:
        await append_decision(
            connection,
            decided,
            tenant_id=TenantId(tenant),
            trace_id=TRACE,
            created_at=WHEN,
        )
    return decided.decision


@pytest.mark.usefixtures("_script_env")
class TestTheDoneWhen:
    async def test_it_prints_identical_for_a_fresh_trace(
        self, pool: asyncpg.Pool, tenancy: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """S5.6's DONE WHEN, stated as the step states it."""
        from scripts.replay_trace import main

        await record_a_decision(pool, tenancy["tenant"])

        code = await main([TRACE, "--tenant", tenancy["tenant"]])

        assert code == 0
        assert "identical" in capsys.readouterr().out

    async def test_a_record_survives_the_round_trip_intact(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """The part the unit tests cannot reach.

        A `DecisionRecord` is thirty-odd fields across three nested reports. It
        is hashed as canonical JSON, stored as `JSONB`, read back and
        revalidated - and `canonical_json` refuses anything that would not
        survive that, loudly, rather than coercing it.
        """
        from guardmem_core.observability.audit_store import read_chain
        from scripts.replay_trace import _decisions_in

        expected = await record_a_decision(pool, tenancy["tenant"])

        async with tenant_transaction(
            pool, TenantId(tenancy["tenant"]), timeout_s=TIMEOUT
        ) as connection:
            links = await read_chain(connection, TenantId(tenancy["tenant"]))
        recovered = _decisions_in(links, TRACE)

        assert len(recovered) == 1
        assert recovered[0][1].decision is expected

    async def test_several_decisions_in_one_trace_are_all_replayed(
        self, pool: asyncpg.Pool, tenancy: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One proposal yields several candidates, so a trace holds several."""
        from scripts.replay_trace import main

        await record_a_decision(pool, tenancy["tenant"], c=0.95)
        await record_a_decision(pool, tenancy["tenant"], c=0.20)
        await record_a_decision(pool, tenancy["tenant"], c=0.50)

        code = await main([TRACE, "--tenant", tenancy["tenant"]])

        assert code == 0
        assert "identical (3 decisions)" in capsys.readouterr().out

    async def test_an_unknown_trace_is_not_reported_as_identical(
        self, pool: asyncpg.Pool, tenancy: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure mode worth guarding: a trace with no decisions would
        otherwise print "identical (0 decisions)" and exit 0, which reads as a
        pass and means nothing was checked."""
        from scripts.replay_trace import main

        await record_a_decision(pool, tenancy["tenant"])

        code = await main(["tr_does_not_exist", "--tenant", tenancy["tenant"]])

        assert code == 2
        assert "no DECISION events" in capsys.readouterr().out

    async def test_a_changed_threshold_is_reported_as_a_diff(
        self,
        pool: asyncpg.Pool,
        tenancy: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The whole point of replaying at all.

        `PRD.md` FR-3.3 makes a threshold change an audited event; this is what
        makes the audit useful. A candidate that auto-wrote under `tau_hi = 0.78`
        is rejected under 0.99, and the script names both decisions and both
        threshold versions rather than reporting a bare mismatch.
        """
        from scripts.replay_trace import main

        await record_a_decision(pool, tenancy["tenant"], c=0.95)

        monkeypatch.setenv("GM_TAU_LO", "0.97")
        monkeypatch.setenv("GM_TAU_MID", "0.98")
        monkeypatch.setenv("GM_TAU_HI", "0.99")
        monkeypatch.setenv("GM_THRESHOLDS_VERSION", "v2")
        get_settings.cache_clear()

        code = await main([TRACE, "--tenant", tenancy["tenant"]])

        output = capsys.readouterr().out
        assert code == 1
        assert "auto_write -> reject" in output
        assert "recorded v1, now v2" in output
