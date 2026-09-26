"""The token bucket, against the server that runs it.  BUILD_NOTEBOOK.md S8.3

    "Token bucket in Redis keyed by `tenant:api_key`."

`tests/unit/test_gateway_limits.py` covers everything around the bucket - the 429,
the headers, fail-open, the key. What it cannot cover is the bucket itself: the
refill, the saturation, the atomic decrement all live in a Lua script that Redis
executes, and emulating that in Python would mean writing a second implementation
and asserting the two agree.

So this file is the arithmetic, on a real Redis. Every test here starts from an
empty database - see the `flushed` fixture - because a bucket is stateful and a
leaked key from a previous test is a test that passes depending on what ran first.

**The concurrency test is the one that matters most.** A read-modify-write in
Python looks correct in a single-threaded test and fails the moment two requests
interleave, which under load is the normal case rather than an edge - and the
failure is a limiter that does not limit while appearing to. One script per check
is the reason that cannot happen, and `test_concurrent_checks_never_oversubscribe`
is the assertion that says so.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

import pytest
from gateway.auth import Principal
from gateway.limits import TokenBucket

from guardmem_core.types import TenantId

if TYPE_CHECKING:
    import redis.asyncio as aioredis

TENANT = TenantId("33333333-3333-4333-8333-333333333333")
PRINCIPAL: Final = Principal(tenant_id=TENANT, key_id="abcd1234", scopes=frozenset())
OTHER: Final = Principal(tenant_id=TENANT, key_id="efgh5678", scopes=frozenset())


def _bucket(redis: aioredis.Redis, *, per_minute: int, burst: int) -> TokenBucket:
    """A bucket over the test's Redis.

    Args:
        redis: The flushed client.
        per_minute: Sustained refill.
        burst: Depth.

    Returns:
        The bucket.
    """
    return TokenBucket(redis, per_minute=per_minute, burst=burst)


class TestTheBucketAllowsItsBurstAndThenRefuses:
    """Depth is depth: a full bucket spends exactly `burst` before refusing."""

    async def test_the_first_burst_requests_are_all_allowed(self, flushed: aioredis.Redis) -> None:
        """A new key starts full. Anything else would make a client's first burst
        fail, which is the shape every batching client has."""
        bucket = _bucket(flushed, per_minute=60, burst=5)

        decisions = [await bucket.check(PRINCIPAL) for _ in range(5)]

        assert all(decision.allowed for decision in decisions)

    async def test_the_request_after_the_burst_is_refused(self, flushed: aioredis.Redis) -> None:
        """And the refusal carries a positive wait, so `Retry-After` means
        something.

        `per_minute=6` makes one token take ten seconds, which is long enough that
        the assertion is not racing the refill.
        """
        bucket = _bucket(flushed, per_minute=6, burst=2)
        for _ in range(2):
            await bucket.check(PRINCIPAL)

        refused = await bucket.check(PRINCIPAL)

        assert not refused.allowed
        assert refused.retry_after_s > 0

    async def test_remaining_counts_down(self, flushed: aioredis.Redis) -> None:
        """The number a client paces itself by, so it has to be real."""
        bucket = _bucket(flushed, per_minute=60, burst=10)

        first = await bucket.check(PRINCIPAL)
        second = await bucket.check(PRINCIPAL)

        assert second.remaining < first.remaining


class TestTheBucketRefills:
    """Continuously, which is what distinguishes a bucket from a window."""

    async def test_waiting_restores_a_token(self, flushed: aioredis.Redis) -> None:
        """A drained bucket becomes usable again without being reset.

        `per_minute=600` refills ten tokens a second, so a tenth of a second is
        one token - short enough for a test and long enough not to be flaky.
        """
        bucket = _bucket(flushed, per_minute=600, burst=1)
        assert (await bucket.check(PRINCIPAL)).allowed
        assert not (await bucket.check(PRINCIPAL)).allowed

        await asyncio.sleep(0.25)

        assert (await bucket.check(PRINCIPAL)).allowed

    async def test_refill_saturates_at_the_burst_depth(self, flushed: aioredis.Redis) -> None:
        """An idle key must not accrue an unbounded credit.

        Without the `math.min` in the script, a key idle for an hour would arrive
        with an hour of tokens and could spend them all at once - which is the
        thundering-herd shape a limiter is supposed to prevent.
        """
        bucket = _bucket(flushed, per_minute=6000, burst=3)
        await bucket.check(PRINCIPAL)

        await asyncio.sleep(0.2)  # far more than 3 tokens' worth of refill

        allowed = [(await bucket.check(PRINCIPAL)).allowed for _ in range(5)]

        assert allowed[:3] == [True, True, True]
        assert allowed[3:] == [False, False]


class TestTheKeyIsolatesCallers:
    """`tenant:api_key` - the composite S8.3 names."""

    async def test_two_keys_in_one_tenant_have_separate_buckets(
        self, flushed: aioredis.Redis
    ) -> None:
        """One key exhausting its budget must not refuse another.

        The reverse - a shared per-tenant bucket - would let one noisy integration
        take down every other client the tenant runs.
        """
        bucket = _bucket(flushed, per_minute=6, burst=1)
        assert (await bucket.check(PRINCIPAL)).allowed
        assert not (await bucket.check(PRINCIPAL)).allowed

        assert (await bucket.check(OTHER)).allowed


class TestTheScriptIsAtomic:
    """The reason the bucket is a script and not three commands."""

    async def test_concurrent_checks_never_oversubscribe(self, flushed: aioredis.Redis) -> None:
        """Twenty simultaneous checks against a bucket of five allow exactly five.

        This is the test a `GET`/compute/`SET` implementation fails. Both requests
        in an interleaved pair read the same token count and both allow, so the
        limiter leaks - and under real load the interleaving is the normal case,
        not a rare one. Redis runs a script atomically against one key, which is
        what makes "exactly five" true rather than "about five".

        `per_minute=6` keeps refill at one token per ten seconds, so nothing
        accrues during the gather and the count is unambiguous.
        """
        bucket = _bucket(flushed, per_minute=6, burst=5)

        decisions = await asyncio.gather(*(bucket.check(PRINCIPAL) for _ in range(20)))

        assert sum(1 for decision in decisions if decision.allowed) == 5


class TestTheBucketDoesNotLeak:
    """An idle key is eventually forgotten."""

    async def test_a_bucket_carries_an_expiry(self, flushed: aioredis.Redis) -> None:
        """Without one, every key a deployment ever sees is a key Redis keeps -
        and API keys rotate, so the set grows without bound.

        Asserted as "has a TTL" rather than as an exact number: the value is
        derived from the burst and refill, and pinning the arithmetic here would
        duplicate the expression in `TokenBucket.__init__` rather than check it.
        """
        bucket = _bucket(flushed, per_minute=60, burst=10)
        await bucket.check(PRINCIPAL)

        ttl = await flushed.ttl(f"rl:{TENANT}:{PRINCIPAL.key_id}")

        assert ttl > 0


class TestTheBucketRejectsAnUnreachableServer:
    """Fail-open is the middleware's decision, not the bucket's."""

    async def test_the_bucket_raises_rather_than_guessing(self, flushed: aioredis.Redis) -> None:
        """`TokenBucket.check` propagates a `RedisError`, deliberately.

        Whether an unavailable limiter means allow or refuse is a policy question,
        and the bucket is not where policy lives - `middleware.RateLimit` decides,
        because it knows what the route does. A bucket that returned
        `allowed=True` on an outage would make that choice for every caller and
        hide it.
        """
        await flushed.aclose()
        bucket = _bucket(flushed, per_minute=60, burst=10)

        with pytest.raises(Exception, match=r"(?i)closed|connection"):
            await bucket.check(PRINCIPAL)
