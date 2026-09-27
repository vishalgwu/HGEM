"""The idempotency cache.  BUILD_NOTEBOOK.md S8.3

    "Idempotency keys cached 24h; a repeat returns the stored result without
    re-running the pipeline."

Split from `test_gateway_limits.py` on 2026-09-26, when the two together passed
`RULES.md` 2.4's 400-line cap. The seam is real rather than convenient: that file
tests a middleware's behaviour through an HTTP client, and this tests a Redis-backed
store through its own methods. They share no fixture.

**A fake Redis holding a dict is the right double here**, unlike for the token
bucket. The bucket's arithmetic is a Lua script that only Redis can run - see
`tests/integration/test_rate_limit_bucket.py`. This store's logic is Python: key
construction, JSON, and the TTL. A dict exercises all of it and emulates nothing
Redis decides.
"""

from __future__ import annotations

import json

from gateway.auth import Principal
from gateway.limits import IdempotencyStore
from guardmem_core.types import TenantId

TENANT_A = TenantId("11111111-1111-4111-8111-111111111111")
TENANT_B = TenantId("22222222-2222-4222-8222-222222222222")

PRINCIPAL_A = Principal(tenant_id=TENANT_A, key_id="key-a-00", scopes=frozenset({"memory:read"}))


class _FakeRedis:
    """A dict with `get` and `set`, and the TTL it was asked for.

    Recording `ex` is the point of writing this rather than reaching for a mock:
    the 24h retention is part of S8.3's requirement, and a fake that discarded it
    would let the store pass while storing forever.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    async def get(self, name: str) -> str | None:
        """Read a key.

        Args:
            name: The key.

        Returns:
            The stored string, or None.
        """
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        """Write a key with an expiry.

        Args:
            name: The key.
            value: The payload.
            ex: Seconds to live.
        """
        self.values[name] = value
        self.ttls[name] = ex


class TestTheIdempotencyStore:
    """A replayed write returns the stored result without re-running anything."""

    def _store(self) -> tuple[IdempotencyStore, _FakeRedis]:
        """Build a store over a dict.

        Returns:
            The store and the fake it writes to.
        """
        fake = _FakeRedis()
        return IdempotencyStore(fake, ttl_s=86_400), fake  # type: ignore[arg-type]

    async def test_a_first_request_has_nothing_stored(self) -> None:
        """A miss is how the route knows to do the work."""
        store, _ = self._store()

        assert await store.get(PRINCIPAL_A, "k1", "hash1") is None

    async def test_a_replay_returns_exactly_what_was_stored(self) -> None:
        """S8.3's "two identical responses", at the level this component owns."""
        store, _ = self._store()
        payload = {"assertion_id": "a1", "decision": "auto_write"}

        await store.put(PRINCIPAL_A, "k1", "hash1", payload)

        assert await store.get(PRINCIPAL_A, "k1", "hash1") == payload

    async def test_the_stored_response_expires_after_24h(self) -> None:
        """ "Cached 24h" is a requirement, not a hint, and a store that wrote no
        expiry would pass every other test here while growing forever."""
        store, fake = self._store()

        await store.put(PRINCIPAL_A, "k1", "hash1", {"ok": True})

        assert set(fake.ttls.values()) == {86_400}

    async def test_the_same_key_with_a_different_body_does_not_replay(self) -> None:
        """Reusing one idempotency key for a different body is a client mistake,
        and replaying the first response would hide it.

        Including the body hash in the identity makes the second request miss and
        be handled on its own, which is the safe failure.
        """
        store, _ = self._store()
        await store.put(PRINCIPAL_A, "k1", "hash1", {"first": True})

        assert await store.get(PRINCIPAL_A, "k1", "hash2") is None

    async def test_two_tenants_cannot_read_each_others_stored_response(self) -> None:
        """The header value is client-chosen, so two tenants will both send `1`.

        This would be a cross-tenant disclosure through a cache rather than
        through SQL - which RLS would not catch, because Redis has no RLS. The
        tenant is in the key for exactly that reason.
        """
        store, _ = self._store()
        other = Principal(tenant_id=TENANT_B, key_id="other123", scopes=frozenset())
        await store.put(PRINCIPAL_A, "1", "hash1", {"tenant": "a"})

        assert await store.get(other, "1", "hash1") is None

    async def test_what_is_stored_is_json_and_not_a_repr(self) -> None:
        """A replay crosses a process boundary - possibly to a different replica -
        so the stored form has to be data rather than anything Python-specific."""
        store, fake = self._store()

        await store.put(PRINCIPAL_A, "k1", "hash1", {"n": 1})

        assert json.loads(next(iter(fake.values.values()))) == {"n": 1}
