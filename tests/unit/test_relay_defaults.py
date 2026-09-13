"""The `OutboxRelay` defaults nothing else exercises.  BUILD_NOTEBOOK.md S3.3

Three module constants and a clock, all of which the integration suite replaces
with something a test chose - which is correct there and leaves the shipped
values unmeasured. These are the values a deployed worker actually runs on, and
each one is wrong in a way that would not fail a test.

The clock is the sharp one. `dispatched_at` is `TIMESTAMPTZ`, so a naive
datetime is not rejected: asyncpg sends it, Postgres interprets it in the
server's timezone, and the column quietly records an instant several hours from
the one that happened. Nothing anywhere would report an error.
"""

from __future__ import annotations

from datetime import UTC, datetime

from guardmem_core.memory.relay import (
    _DEFAULT_BATCH_SIZE,
    _DEFAULT_MAX_ATTEMPTS,
    _utc_now,
)


def test_the_default_clock_is_timezone_aware_and_utc() -> None:
    """A naive datetime into a `TIMESTAMPTZ` is silently reinterpreted."""
    now = _utc_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(UTC).utcoffset()


def test_the_batch_is_bounded() -> None:
    """`RULES.md` §2.2 wants bounded resources; an unbounded claim would lock
    every pending row in the table at once."""
    assert 0 < _DEFAULT_BATCH_SIZE <= 1000


def test_there_is_a_hard_attempt_cap() -> None:
    """`RULES.md` §2.3: "retries [...] with a hard attempt cap". Zero would mean
    no event was ever claimed; unbounded would mean a poison event starves the
    queue behind it forever."""
    assert 0 < _DEFAULT_MAX_ATTEMPTS <= 10
