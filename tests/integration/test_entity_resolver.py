"""`NamespaceEntityResolver` against a real Postgres.  ADR-0008

Integration rather than unit because the whole of what this class does that is
hard is the write: `assertion.subject_id` is `UUID NOT NULL REFERENCES
entity(id)`, so the row has to be there before a fact can commit, and `entity`
carries row-level security. A fake would assert that the code calls `INSERT`,
which is not the property that matters. The property that matters is that after
`resolve` returns, the foreign key is satisfiable.

**What is deliberately not tested here: that similar names resolve together.**
They do not, by design. ADR-0008's resolver compares no strings, so the tests
below are about *bindings* - the namespace, the declared type, and the id being
a pure function of the two.
"""

from __future__ import annotations

import asyncpg
import pytest

from fixtures.pgvector import TIMEOUT_S
from guardmem_core.errors import ValidationRejected
from guardmem_core.memory.entities import (
    NamespaceEntityResolver,
    derive_entity_id,
    subject_type_of,
)
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.types import Namespace, TenantId

NS = Namespace("patient:7781")


@pytest.fixture
def resolver(pool: asyncpg.Pool) -> NamespaceEntityResolver:
    """The subject, on the least-privilege role's pool."""
    return NamespaceEntityResolver(pool, timeout_s=TIMEOUT_S)


class TestItBindsRatherThanMatching:
    async def test_the_same_namespace_always_gives_the_same_entity(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """Two different surface forms, one namespace, one entity.

        This is the whole decision in one assertion. "Joan Ellery" and "the
        patient" are not compared, judged or scored - they land on one entity
        because the *namespace* is one, which is why a false merge is
        unreachable rather than merely unlikely.
        """
        tenant = TenantId(tenancy["tenant"])

        first = await resolver.resolve(
            "Joan Ellery", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )
        second = await resolver.resolve(
            "the patient", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )

        assert first == second

    async def test_one_namespace_under_two_tenants_is_two_entities(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """The isolation property the whole product exists to keep. `patient:1`
        at one clinic is not `patient:1` at another, and the tenant is part of
        the derivation rather than a filter applied afterwards."""
        mine = derive_entity_id(TenantId(tenancy["tenant"]), NS)
        theirs = derive_entity_id(TenantId(tenancy["other"]), NS)

        assert mine != theirs

    async def test_the_id_is_a_pure_function_of_tenant_and_namespace(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """Derived rather than looked up, which is what makes creation
        idempotent without a read and race-free without a lock."""
        tenant = TenantId(tenancy["tenant"])

        resolved = await resolver.resolve(
            "Joan Ellery", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )

        assert resolved == derive_entity_id(tenant, NS)


class TestTheRowIsThereAfterwards:
    async def test_the_entity_row_exists_and_carries_the_declared_type(
        self, resolver: NamespaceEntityResolver, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`entity.type` is `NOT NULL`, and `expected_type` is the only thing in
        the signature that could supply it - the gap that made this protocol
        unimplementable before ADR-0008."""
        tenant = TenantId(tenancy["tenant"])

        entity_id = await resolver.resolve(
            "Joan Ellery", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )

        # Read back through `tenant_transaction`, not a bare `acquire`. The
        # third argument to `set_config` is `is_local`, which only means
        # anything inside a transaction block - outside one it is a no-op, RLS
        # sees no tenant, and the row comes back as `None`. That was the first
        # version of this test, and it looked exactly like a failed write.
        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            row = await conn.fetchrow(
                "SELECT type, canonical_name FROM entity WHERE id = $1::uuid", str(entity_id)
            )
        assert row is not None, "the assertion's foreign key would have nothing to point at"
        assert row["type"] == "Patient"
        assert row["canonical_name"] == "Joan Ellery"

    async def test_a_second_resolve_does_not_rewrite_the_canonical_name(
        self, resolver: NamespaceEntityResolver, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """`ON CONFLICT DO NOTHING`, not an upsert. The first surface form seen
        is as good as the fourth, and rewriting would make the display name
        depend on which proposal happened to arrive last."""
        tenant = TenantId(tenancy["tenant"])
        await resolver.resolve(
            "Joan Ellery", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )

        entity_id = await resolver.resolve(
            "she", tenant_id=tenant, namespace=NS, expected_type="Patient"
        )

        async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
            name = await conn.fetchval(
                "SELECT canonical_name FROM entity WHERE id = $1::uuid", str(entity_id)
            )
        assert name == "Joan Ellery"


class TestItRefusesRatherThanGuessing:
    async def test_a_namespace_that_names_no_subject_is_refused(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """No binding, no guess. The message names both remedies because a
        caller who is told only "cannot resolve" has nothing to act on."""
        with pytest.raises(ValidationRejected, match="does not name a subject"):
            await resolver.resolve(
                "Joan Ellery",
                tenant_id=TenantId(tenancy["tenant"]),
                namespace=Namespace("scratch"),
                expected_type="Patient",
            )

    async def test_a_namespace_of_the_wrong_type_is_refused(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """An `org:` namespace cannot carry a predicate whose subject is a
        Patient. Writing it anyway would attach a real fact to the wrong kind of
        thing, which is worse than refusing."""
        with pytest.raises(ValidationRejected, match="names a 'org'"):
            await resolver.resolve(
                "Acme",
                tenant_id=TenantId(tenancy["tenant"]),
                namespace=Namespace("org:acme"),
                expected_type="Patient",
            )

    async def test_the_type_check_is_case_insensitive(
        self, resolver: NamespaceEntityResolver, tenancy: dict[str, str]
    ) -> None:
        """The namespace convention is lowercase (`patient:7781`) and the
        ontology declares `Patient`. Requiring them to match exactly would make
        the documented convention unusable with the shipped pack."""
        resolved = await resolver.resolve(
            "Joan Ellery",
            tenant_id=TenantId(tenancy["tenant"]),
            namespace=NS,
            expected_type="Patient",
        )

        assert resolved


class TestSubjectTypeOf:
    """The parsing, which needs no database."""

    @pytest.mark.parametrize(
        ("namespace", "expected"),
        [
            ("patient:7781", "patient"),
            ("org:acme", "org"),
            # `<type>:<id>` is the documented shape, and an id may contain a
            # colon - only the first segment is the type.
            ("patient:7781:v2", "patient"),
            ("scratch", None),
            (":7781", None),
            ("patient:", None),
        ],
    )
    def test_it_reads_the_prefix_or_says_there_is_none(
        self, namespace: str, expected: str | None
    ) -> None:
        assert subject_type_of(Namespace(namespace)) == expected
