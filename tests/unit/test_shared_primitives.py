"""Three facts the S3.6 audit moved into the package so they could not drift.

Each had been written twice: once in the document that owns it, and once as a
literal in a caller. None of the duplicates was checked against its original,
and two of them are safety orderings - the kind of thing that is wrong for
months without raising.

- `ImpactLevel.risk_floor` was prose in the enum's docstring and a `dict` of the
  same four numbers in the seed script.
- `SourceTier.at_least` was prose in *that* enum's docstring and a tuple in the
  seed's data module. A `StrEnum` compares alphabetically, so the question
  "is this source good enough?" answers *wrongly* rather than raising if anyone
  reaches for `<=` directly - which is the test below that looks pointless and
  is not.
- `libpq_dsn` / `sqlalchemy_dsn` were the same magic prefix written inline at
  three call sites, where a typo produces a connection error that reads like a
  network fault.

They are tested here rather than incidentally through their callers because the
callers that exercised them are a subprocess and a fixture, neither of which
coverage can see and neither of which would say *which* number was wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.vector.pool import (
    libpq_dsn,
    sqlalchemy_dsn,
    tenant_transaction,
    transaction,
)
from guardmem_core.schemas import ImpactLevel, SourceTier
from guardmem_core.types import TenantId

if TYPE_CHECKING:
    import asyncpg

LIBPQ = "postgresql://guardmem:guardmem@localhost:5433/guardmem"  # pragma: allowlist secret
SQLALCHEMY = (
    "postgresql+asyncpg://guardmem:guardmem@localhost:5433/guardmem"  # pragma: allowlist secret
)


class TestTheImpactFloors:
    @pytest.mark.parametrize(
        ("impact", "floor"),
        [
            (ImpactLevel.LOW, 0.15),
            (ImpactLevel.MEDIUM, 0.35),
            (ImpactLevel.HIGH, 0.60),
            (ImpactLevel.CRITICAL, 0.80),
        ],
    )
    def test_each_level_floors_risk_where_the_spec_says(
        self, impact: ImpactLevel, floor: float
    ) -> None:
        """`MEMORY_ENGINE.md` §3.3, verbatim. `R = max(R_raw, floor[impact])`, so
        these four numbers are what keep a confidently-scored write to a critical
        field out of the auto-write path."""
        assert impact.risk_floor == pytest.approx(floor)

    def test_every_level_has_one(self) -> None:
        """A level added without a floor would raise `KeyError` inside the risk
        scorer rather than at the point somebody extended the enum."""
        assert all(isinstance(level.risk_floor, float) for level in ImpactLevel)

    def test_the_floors_rise_with_the_level(self) -> None:
        """The property, rather than the four values: a floor that did not
        increase would let a more dangerous predicate score as safer."""
        floors = [level.risk_floor for level in ImpactLevel]

        assert floors == sorted(floors)


class TestTheSourceTierOrdering:
    def test_a_tier_is_at_least_itself(self) -> None:
        assert all(tier.at_least(tier) for tier in SourceTier)

    @pytest.mark.parametrize(
        ("tier", "floor"),
        [
            (SourceTier.TRUSTED_SYSTEM, SourceTier.VERIFIED_USER),
            (SourceTier.VERIFIED_USER, SourceTier.UNVERIFIED_USER),
            (SourceTier.UNVERIFIED_USER, SourceTier.TOOL_OUTPUT),
            (SourceTier.TOOL_OUTPUT, SourceTier.RETRIEVED_WEB),
        ],
    )
    def test_each_tier_outranks_the_next(self, tier: SourceTier, floor: SourceTier) -> None:
        """`RULES.md` §4's chain, one link at a time."""
        assert tier.at_least(floor)
        assert not floor.at_least(tier)

    def test_retrieved_web_satisfies_nothing_above_it(self) -> None:
        """The clause §4 states outright: retrieved web content can never
        auto-write a HIGH-impact predicate regardless of confidence."""
        weakest = SourceTier.RETRIEVED_WEB

        assert not any(weakest.at_least(tier) for tier in SourceTier if tier is not weakest)

    def test_string_comparison_would_get_this_wrong(self) -> None:
        """Why the method exists at all.

        `SourceTier` is a `StrEnum`, so `<=` compares alphabetically and is
        perfectly happy to say a tool output outranks a trusted system. Nothing
        raises; the answer is simply wrong, in the direction that lets a weak
        source write a dangerous predicate. Pinned so that a future reader who
        thinks the method is ceremony can see what it replaced.
        """
        assert SourceTier.TOOL_OUTPUT <= SourceTier.TRUSTED_SYSTEM
        assert not SourceTier.TOOL_OUTPUT.at_least(SourceTier.TRUSTED_SYSTEM)


class TestTheDsnSpellings:
    def test_libpq_strips_the_driver_marker(self) -> None:
        """asyncpg rejects the SQLAlchemy form outright."""
        assert libpq_dsn(SQLALCHEMY) == LIBPQ

    def test_sqlalchemy_adds_it(self) -> None:
        """`GM_DATABASE_URL` is specified to carry it, because Alembic reads it."""
        assert sqlalchemy_dsn(LIBPQ) == SQLALCHEMY

    @pytest.mark.parametrize("convert", [libpq_dsn, sqlalchemy_dsn])
    def test_both_are_idempotent(self, convert: object) -> None:
        """Applying either twice must be the same as applying it once.

        Call sites convert defensively - the seed does not know whether it was
        handed a `.env` value or a container's DSN - so a converter that mangled
        already-correct input would fail only on the path nobody tested.
        """
        assert callable(convert)
        for dsn in (LIBPQ, SQLALCHEMY):
            assert convert(convert(dsn)) == convert(dsn)

    def test_only_the_scheme_is_touched(self) -> None:
        """A blanket `replace` would rewrite a password or a database name that
        happened to contain the scheme text."""
        odd = "postgresql://u:postgresql+asyncpg://@host:5432/db"  # pragma: allowlist secret

        assert libpq_dsn(odd) == odd


class _ExhaustedPool:
    """A pool whose `max_size` connections are all checked out.

    Models asyncpg rather than reimplementing it. `Pool._acquire` awaits
    `self._queue.get()` over `max_size` holders and wraps it in `wait_for` only
    when a timeout is given, so the two behaviours worth pinning are: with a
    timeout, exhaustion surfaces as `TimeoutError`; without one, the await never
    returns and there is nothing for a test to observe. This fake raises, which
    is the case the code has to handle.
    """

    def __init__(self, max_size: int = 10) -> None:
        self.max_size = max_size
        self.timeouts: list[float | None] = []

    def get_max_size(self) -> int:
        return self.max_size

    def acquire(self, *, timeout: float | None = None) -> _ExhaustedAcquire:
        self.timeouts.append(timeout)
        return _ExhaustedAcquire()


class _ExhaustedAcquire:
    """What `Pool.acquire()` returns: a context manager, not a coroutine."""

    async def __aenter__(self) -> object:
        raise TimeoutError  # what `compat.wait_for` raises, carrying no message

    async def __aexit__(self, *_: object) -> bool:
        return False  # pragma: no cover - __aenter__ always raises


def _pool(max_size: int = 10) -> asyncpg.Pool:
    """The fake, typed as the real thing. `transaction` takes an `asyncpg.Pool`
    and a structural stand-in cannot satisfy a concrete class under
    `mypy --strict`, so this is the same `cast` the MCP tests use."""
    return cast("asyncpg.Pool", _ExhaustedPool(max_size))


class TestThePoolBoundsTheWaitForAConnection:
    """ADR-0012. `acquire()` with no timeout waits forever.

    The failure this prevents has no symptom to assert on directly - a hung
    `await` produces no exception, no log line and no return - so what is pinned
    here is the two observable consequences: that a bound is passed at all, and
    that exceeding it is reported as exhaustion rather than as a dead database.
    """

    async def test_transaction_bounds_the_acquire(self) -> None:
        """The bug was a missing argument, so this asserts the argument."""
        pool = _ExhaustedPool()

        with pytest.raises(StoreUnavailable):
            async with transaction(cast("asyncpg.Pool", pool), timeout_s=2.5):
                pass  # pragma: no cover - the acquire never yields

        assert pool.timeouts == [2.5], (
            "transaction() must pass its timeout to acquire(); without it asyncpg "
            "awaits queue.get() with nothing bounding it and the process hangs."
        )

    async def test_tenant_transaction_bounds_it_too(self) -> None:
        """It delegates to `transaction`, and delegating to the *unbounded*
        helper is how every tenant-scoped call came to be unbounded despite
        `tenant_transaction` having taken a `timeout_s` since S3.3."""
        pool = _ExhaustedPool()

        with pytest.raises(StoreUnavailable):
            async with tenant_transaction(cast("asyncpg.Pool", pool), TenantId("t"), timeout_s=1.5):
                pass  # pragma: no cover - the acquire never yields

        assert pool.timeouts == [1.5]

    async def test_exhaustion_is_reported_as_exhaustion(self) -> None:
        """`TimeoutError` is a subclass of `OSError`.

        So the generic `except (OSError, ...)` clause below it would catch this
        first if the order were wrong, and `wait_for`'s instance carries no
        message - the operator would get "postgres connection failed: " with
        nothing after the colon, for an incident that is not a connection
        failure. Exhaustion means "you are over capacity" and wants a different
        first responder than "the database is gone".
        """
        with pytest.raises(StoreUnavailable) as caught:
            async with transaction(_pool(max_size=7), timeout_s=0.5):
                pass  # pragma: no cover - the acquire never yields

        message = str(caught.value)
        assert "0.5" in message, "the message must name the wait that was exceeded"
        assert "7" in message, "the message must name max_size, which is the knob"
        assert "postgres connection failed" not in message, (
            "caught by the OSError branch - TimeoutError subclasses OSError, so "
            "the specific clause has to come first"
        )

    async def test_exhaustion_stays_retryable(self) -> None:
        """A full pool empties. `ARCHITECTURE.md` §4 degrades a store fault
        toward human review rather than dropping the write, and that rests on
        the error carrying the flag."""
        with pytest.raises(StoreUnavailable) as caught:
            async with transaction(_pool(), timeout_s=0.1):
                pass  # pragma: no cover - the acquire never yields

        assert caught.value.retryable is True
