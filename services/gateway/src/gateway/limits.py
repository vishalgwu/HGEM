"""The token bucket and the idempotency cache.  BUILD_NOTEBOOK.md S8.3

S8.3: "Token bucket in Redis keyed by `tenant:api_key`. Idempotency keys cached
24h; a repeat returns the stored result without re-running the pipeline."

Both live here because both are Redis state owned by the ingress rather than by
the engine - `guardmem-core` must stay deployable in the worker and the eval
harness with no Redis to talk to, which is why `redis` is the gateway's dependency
and not the library's.

**The bucket is one Lua script, and that is the whole design.** A read-modify-write
in Python - `GET` the tokens, compute, `SET` them back - is a race: two requests
that interleave both read the same count and both allow. Under load that is not a
rare edge, it is the normal case, and the failure is a limiter that does not limit
while looking like it does. Redis runs a script atomically against one key, so the
refill, the test and the decrement are one operation.

**`tenant:api_key`, both parts.** The tenant alone would let one compromised key
exhaust a whole tenant's budget; the key alone would let a tenant multiply its
allowance by issuing keys. S8.3 names both and the composite is the only key that
bounds what each side controls.

**Idempotency is not a middleware.** S8.2 fixed the chain at five layers and
`tests/unit/test_gateway_middleware.py` asserts it, so a sixth would change a
documented order. It would also be wrong: only writes are idempotent, so a
middleware would have to know which routes those are, and the stored response has
to be the route's own model rather than bytes a generic layer happened to capture.
It is a helper a write route calls, and its first caller is S8.4's propose
endpoint - the step that decides what a write's response even is.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from gateway.auth import Principal

__all__ = ["Decision", "IdempotencyStore", "RateLimiter", "TokenBucket", "bucket_key"]

_LOGGER: Final = logging.getLogger(__name__)

# Namespaces, so the two features cannot collide on one Redis and neither can
# collide with S8.4's streams or the review leases. Short because every key pays
# the prefix on every request.
_RATE_PREFIX: Final = "rl"
_IDEM_PREFIX: Final = "idem"

# A token bucket in one atomic step.
#
# KEYS[1]  the bucket
# ARGV[1]  capacity (burst depth)
# ARGV[2]  refill per second
# ARGV[3]  now, as a float unix timestamp
# ARGV[4]  how long an idle bucket is kept
#
# Returns {allowed, tokens_remaining, retry_after_seconds}.
#
# **Time comes from the caller, not from Redis.** `TIME` inside a script makes it
# non-deterministic, which Redis historically refused to replicate; passing the
# clock in keeps the script pure and replayable. The caller is the only process
# that could disagree about `now`, and every gateway replica reads the same wall
# clock to within far less than one token.
#
# The bucket is stored as two fields rather than a counter with a TTL, because a
# counter expiring resets the *whole* allowance at an arbitrary instant - a client
# hammering a key would get a full bucket the moment the TTL lapsed. Tracking the
# last refill instead makes the recovery continuous, which is what a bucket means.
_BUCKET_SCRIPT: Final = """
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local idle_ttl = tonumber(ARGV[4])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = tonumber(state[1])
local updated = tonumber(state[2])

if tokens == nil or updated == nil then
  tokens = capacity
  updated = now
end

local elapsed = now - updated
if elapsed > 0 then
  tokens = math.min(capacity, tokens + elapsed * refill_per_second)
  updated = now
end

local allowed = 0
local retry_after = 0
if tokens >= 1 then
  allowed = 1
  tokens = tokens - 1
else
  retry_after = (1 - tokens) / refill_per_second
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', updated)
redis.call('EXPIRE', KEYS[1], idle_ttl)

return {allowed, tostring(tokens), tostring(retry_after)}
"""


def bucket_key(principal: Principal) -> str:
    """The Redis key one caller's bucket lives at.

    Args:
        principal: The authenticated caller.

    Returns:
        `rl:<tenant>:<key_id>`.

    **`key_id`, never the credential.** `Principal.key_id` is the first eight
    characters of the key and is already what reaches logs; a Redis key holding
    the whole credential would put it in `MONITOR` output, in `--bigkeys`, and in
    any dump an operator takes to debug a limit.
    """
    return f"{_RATE_PREFIX}:{principal.tenant_id}:{principal.key_id}"


@dataclass(frozen=True, slots=True)
class Decision:
    """What the bucket said.

    Attributes:
        allowed: Whether this request may proceed.
        remaining: Tokens left after it, floored - a client reading
            `X-RateLimit-Remaining` wants a count it can trust not to round up.
        retry_after_s: Seconds until one token exists. Zero when allowed.
    """

    allowed: bool
    remaining: int
    retry_after_s: float


class RateLimiter(Protocol):
    """Decides whether one caller may make one request."""

    async def check(self, principal: Principal) -> Decision:
        """Consume a token if one is available.

        Args:
            principal: The caller.

        Returns:
            The `Decision`.
        """
        ...


class TokenBucket:
    """A Redis token bucket, one script per check."""

    def __init__(self, redis: aioredis.Redis, *, per_minute: int, burst: int) -> None:
        """Register the script and hold the configuration.

        Args:
            redis: The process-wide client.
            per_minute: Sustained refill.
            burst: Bucket depth.

        `register_script` uses `EVALSHA` and falls back to `EVAL` once per
        connection, so the script body crosses the wire on first use rather than
        on every request.
        """
        self._script = redis.register_script(_BUCKET_SCRIPT)
        self._capacity = float(burst)
        self._refill_per_second = per_minute / 60.0
        # An idle bucket is worth keeping only until it would have refilled to
        # full, at which point its state is indistinguishable from absent. Keeping
        # it longer is memory spent to reach the same answer.
        self._idle_ttl = max(1, int(burst / max(self._refill_per_second, 1e-9)) + 1)

    async def check(self, principal: Principal) -> Decision:
        """Consume a token if one is available.

        Args:
            principal: The caller, whose tenant and key name the bucket.

        Returns:
            The `Decision`. `remaining` is floored and `retry_after_s` is what a
            `Retry-After` header should say.

        Raises:
            redis.RedisError: Redis is unreachable or the script failed. **Not
                caught here**, deliberately: whether an unavailable limiter means
                fail-open or fail-closed is a policy decision, and it belongs at
                the call site that knows what the route does. See
                `middleware.RateLimit`.
        """
        raw: list[Any] = await self._script(
            keys=[bucket_key(principal)],
            args=[self._capacity, self._refill_per_second, time.time(), self._idle_ttl],
        )
        allowed, tokens, retry_after = bool(int(raw[0])), float(raw[1]), float(raw[2])
        return Decision(allowed=allowed, remaining=int(tokens), retry_after_s=retry_after)


class IdempotencyStore:
    """Stored responses for replayed writes.

    S8.3: "a repeat returns the stored result **without re-running the
    pipeline**". That phrase is the requirement - the value is not a cache that
    saves time, it is what makes a retried write safe. A client that times out and
    retries must not create a second assertion, and the pipeline is the thing that
    must not run twice.
    """

    def __init__(self, redis: aioredis.Redis, *, ttl_s: int) -> None:
        """Hold the client and the retention.

        Args:
            redis: The process-wide client.
            ttl_s: How long a stored response is replayable. S8.3 says 24h.
        """
        self._redis = redis
        self._ttl_s = ttl_s

    async def get(self, principal: Principal, key: str, body_hash: str) -> dict[str, Any] | None:
        """The stored response for this key, if any.

        Args:
            principal: The caller. Part of the Redis key, so one tenant's
                idempotency key cannot collide with another's - the header value
                is client-chosen and two tenants will pick `1`.
            key: The `Idempotency-Key` header value.
            body_hash: `sha256` of the request body, from the `BodyHash` layer.

        Returns:
            The stored response, or None when this is the first time.

        **The body hash is part of the identity, not merely stored beside it.**
        A client that reuses one idempotency key for a *different* body has made a
        mistake, and replaying the first response would hide it. Including the
        hash in the key means the second request simply misses and is handled on
        its own, which is the safe failure - and `_conflicts` below is why that is
        the lesser of the two evils.
        """
        stored = await self._redis.get(self._key(principal, key, body_hash))
        if stored is None:
            return None
        decoded: dict[str, Any] = json.loads(stored)
        _LOGGER.info(
            "replayed an idempotent write",
            extra={"tenant": str(principal.tenant_id), "idempotency_key": key},
        )
        return decoded

    async def put(
        self, principal: Principal, key: str, body_hash: str, response: dict[str, Any]
    ) -> None:
        """Store a response for later replay.

        Args:
            principal: The caller.
            key: The `Idempotency-Key` header value.
            body_hash: `sha256` of the request body.
            response: What to return on a replay. Must be JSON-serialisable,
                which is the route's own response model dumped - not a
                `Response` object, whose headers and status would then be
                replayed too and could contradict the live ones.

        `SET ... EX` rather than `SET` then `EXPIRE`: two commands can be
        interrupted between, and a stored response with no expiry is a row that
        lives until somebody notices Redis growing.
        """
        await self._redis.set(
            self._key(principal, key, body_hash), json.dumps(response), ex=self._ttl_s
        )

    def _key(self, principal: Principal, key: str, body_hash: str) -> str:
        """The Redis key one stored response lives at.

        Args:
            principal: The caller.
            key: The client's idempotency key.
            body_hash: `sha256` of the body.

        Returns:
            `idem:<tenant>:<key>:<body hash>`.

        The tenant is in the key because the header value is client-chosen; two
        tenants both sending `Idempotency-Key: 1` must not read each other's
        stored response, and that would be a cross-tenant disclosure through a
        cache rather than through SQL - which RLS would not catch.
        """
        return f"{_IDEM_PREFIX}:{principal.tenant_id}:{key}:{body_hash}"
