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

import pytest

from guardmem_core.memory.vector.pool import libpq_dsn, sqlalchemy_dsn
from guardmem_core.schemas import ImpactLevel, SourceTier

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
