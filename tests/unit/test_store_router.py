"""`StoreRouter` over the fakes.  BUILD_NOTEBOOK.md S3.3

`RULES.md` §5 singles this module out by name: `memory/router.py` is one of the
three places that need "100% branch coverage on decision branches". There are
only three branches here and they are all refusals, which is the point - the
router's whole contribution at this step is the app-layer half of §4's defence
in depth, and a guard nobody tests is a guard nobody has.

The tenant check is the one worth reading twice. It looks redundant next to RLS
and it is not: `rowmap.assertion_params` writes the *store's* tenant over the
model's, so a foreign assertion reaching Postgres is already wearing the right
label and the policy correctly lets it through. This is the only layer that sees
the mismatch at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fixtures.fakes import FakeVectorStore
from guardmem_core.errors import ValidationRejected
from guardmem_core.memory.router import GRAPH, VECTOR, StoreRouter
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

# Local rather than imported from `fixtures.pgvector`: this suite is the unit
# one, and borrowing the integration scaffolding's constants would couple it to
# a module that exists to start a database.
NS = Namespace("patient:8812")
WHEN = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)

TENANT = TenantId("11111111-1111-1111-1111-111111111111")
OTHER = TenantId("22222222-2222-2222-2222-222222222222")


def assertion(
    *, tenant_id: TenantId = TENANT, visible: bool = False, obj: str = "penicillin"
) -> StoredAssertion:
    """A well-formed, sourced assertion, invisible unless a test says otherwise."""
    return StoredAssertion(
        assertion_id=AssertionId(f"a-{obj}-{tenant_id[:8]}"),
        tenant_id=tenant_id,
        namespace=NS,
        subject_id=EntityId("e-1"),
        predicate="allergy",
        object=obj,
        confidence=0.9,
        risk=0.5,
        valid_from=WHEN,
        recorded_at=WHEN,
        provenance=[
            Provenance(
                source_hash="sha256:abc",
                source_span=(0, 10),
                source_tier=SourceTier.VERIFIED_USER,
                verbatim=f"allergic to {obj}",
                alignment=0.99,
                captured_at=WHEN,
            )
        ],
        trace_id=TraceId("tr_s33"),
        visible=visible,
    )


@pytest.fixture
def vector() -> FakeVectorStore:
    """Where the router writes."""
    return FakeVectorStore()


@pytest.fixture
def router(vector: FakeVectorStore) -> StoreRouter:
    """The subject, bound to `TENANT`."""
    return StoreRouter(vector, tenant_id=TENANT)


class TestRouting:
    def test_every_assertion_goes_to_both_stores_for_now(self, router: StoreRouter) -> None:
        """`ARCHITECTURE.md` §2.4's "most facts -> both", which is all of them
        until `S3.5` loads the ontology that could say otherwise."""
        assert router.route(assertion()) == {VECTOR, GRAPH}

    def test_the_destination_set_cannot_be_mutated_by_a_caller(self, router: StoreRouter) -> None:
        """A shared module constant is handed out, so it had better be frozen -
        otherwise one caller's `.add()` would re-route every later write."""
        with pytest.raises(AttributeError):
            router.route(assertion()).add("neo4j")  # type: ignore[attr-defined]


class TestTheWrite:
    async def test_a_written_assertion_reaches_the_store(
        self, router: StoreRouter, vector: FakeVectorStore
    ) -> None:
        written = assertion()

        await router.write([written])

        assert list(vector.assertions) == [written.assertion_id]

    async def test_an_empty_batch_writes_nothing_and_does_not_raise(
        self, router: StoreRouter, vector: FakeVectorStore
    ) -> None:
        """A proposal whose candidates were all rejected is a normal outcome."""
        await router.write([])

        assert not vector.assertions

    async def test_what_was_written_is_still_not_readable(
        self, router: StoreRouter, vector: FakeVectorStore
    ) -> None:
        """The contract, and the thing a caller is most likely to be surprised
        by: the write has committed and the fact is not retrievable until the
        relay has run."""
        await router.write([assertion()])

        found = await vector.search(namespace=NS, embedding=[], k=10, filters={})
        assert found == []


class TestTheTenantCheck:
    async def test_another_tenants_assertion_is_refused(self, router: StoreRouter) -> None:
        """The hole RLS cannot see - see the module docstring."""
        with pytest.raises(ValidationRejected, match="another tenant"):
            await router.write([assertion(tenant_id=OTHER)])

    async def test_a_mixed_batch_writes_nothing_at_all(
        self, router: StoreRouter, vector: FakeVectorStore
    ) -> None:
        """Checked before anything is written, so a bad batch is not a partial
        one. A router that validated per assertion as it wrote would leave the
        good half committed and the caller with no way to know which half."""
        with pytest.raises(ValidationRejected):
            await router.write([assertion(), assertion(tenant_id=OTHER, obj="latex")])

        assert not vector.assertions

    async def test_the_refusal_names_the_proposal_it_came_from(self, router: StoreRouter) -> None:
        """`RULES.md` §2.3: every raise inside the pipeline attaches `trace_id`
        and `candidate_id`."""
        with pytest.raises(ValidationRejected) as raised:
            await router.write([assertion(tenant_id=OTHER)])

        assert raised.value.trace_id == "tr_s33"
        assert raised.value.candidate_id is not None


class TestTheVisibilityCheck:
    async def test_an_assertion_that_arrives_visible_is_refused(self, router: StoreRouter) -> None:
        """`INSERT_ASSERTION` writes the literal `false`, so the flag would be
        ignored - and a caller who set it believes it has published a fact. A
        silent no-op is the worse answer."""
        with pytest.raises(ValidationRejected, match="visible=true"):
            await router.write([assertion(visible=True)])

    async def test_it_is_checked_even_when_the_tenant_is_right(
        self, router: StoreRouter, vector: FakeVectorStore
    ) -> None:
        """The two guards are independent; passing one must not skip the other."""
        with pytest.raises(ValidationRejected, match="visible=true"):
            await router.write([assertion(tenant_id=TENANT, visible=True)])

        assert not vector.assertions
