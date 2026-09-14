"""Incumbent retrieval against the seeded tenant.  BUILD_NOTEBOOK.md S4.2

S4.2's DONE WHEN in one sentence - "integration test returns the seeded
incumbent for a matching candidate" - and `test_the_seeded_incumbent_comes_back`
is exactly that, against the database `make seed` writes rather than against
rows this module invented.

**Seeded, not hand-written, and the step says so.** The demo tenant holds
twenty-eight assertions written through the real path: embedded by
`HashEmbedder`, released by the relay, visible, with real provenance spans into
a real transcript. A test that wrote its own rows would be checking that
retrieval finds what retrieval just stored; this checks that it finds what the
*system* stored, through a partial index and an RLS policy, which is the half a
fake cannot have an opinion about.

The subject is read back from the database rather than recomputed from the
seed's `uuid5` derivation. Recomputing it would be a second copy of the seed's
id scheme, and the failure mode of a second copy is that it keeps agreeing right
up until it does not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import asyncpg
import pytest

from fixtures.seed import PATIENT_NAME
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.pipeline.l2_validate import retrieve_incumbents
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import CandidateId, EntityId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue

# `scripts/demo_tenant_data.py`'s namespace and intake time.
DEMO_NS: Final = Namespace("patient:7781")
CLAIMED_AT: Final = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)
TIMEOUT_S: Final = 10.0


@pytest.fixture
async def patient(demo: tuple[asyncpg.Connection, str]) -> EntityId:
    """The seeded patient's resolved entity id, read from the database.

    Entity resolution does not exist - `conflict.py`'s module docstring has the
    argument - so a caller supplies the id. Here the database is the caller: the
    seed created this row, and looking it up by canonical name is what a
    resolver would eventually do anyway.
    """
    owner, _ = demo
    entity = await owner.fetchval("SELECT id FROM entity WHERE canonical_name = $1", PATIENT_NAME)
    assert entity is not None, "the seed did not create its patient"
    return EntityId(str(entity))


def candidate(
    tenant: str, *, predicate: str = "allergy", obj: ObjectValue = "penicillin"
) -> MemoryCandidate:
    """A candidate claiming something about the seeded patient."""
    return MemoryCandidate(
        candidate_id=CandidateId("c_1"),
        tenant_id=TenantId(tenant),
        namespace=DEMO_NS,
        subject=PATIENT_NAME,
        predicate=predicate,
        object=obj,
        provenance=Provenance(
            source_hash="sha256:abc",
            source_span=(0, 10),
            source_tier=SourceTier.VERIFIED_USER,
            verbatim="allergic to penicillin",
            captured_at=CLAIMED_AT,
        ),
        extracted_by="claude-haiku-4-5",
        prompt_version="extract_memories@v1",
        trace_id=TraceId("tr_s42"),
    )


class TestTheDoneWhen:
    async def test_the_seeded_incumbent_comes_back(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """S4.2's DONE WHEN.

        The seed wrote `allergy: penicillin` for this patient. A candidate
        claiming the same predicate about the same subject must find it - and
        must find it through `assertion_live_idx`, the partial index S3.1 built
        for `(tenant, namespace, subject, predicate) WHERE valid_to IS NULL AND
        visible`. This is that index's first caller.
        """
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert "penicillin" in [a.object for a in found.nearest]
        # Nearest, not merely present: identical text embeds identically, so the
        # fact the candidate restates must outrank the patient's three other
        # live allergies. `allergy` is `cardinality: many` in the clinical pack,
        # which is why all four are incumbents and why the ordering matters.
        assert found.nearest[0].object == "penicillin"
        assert found.nearest[0].predicate == "allergy"
        assert found.nearest[0].visible is True

    async def test_the_incumbent_arrives_with_its_provenance(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """S4.3's NLI pass compares `incumbent.provenance[*].verbatim` against
        the candidate's, so an incumbent without its citation is an incumbent
        the next step cannot use.

        The seeded span is a real offset into a real transcript turn, located by
        `link_span` - which is why this asserts on the text rather than on a
        length.
        """
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        citation = found.nearest[0].provenance[0]
        assert citation.verbatim == "I'm allergic to penicillin"
        assert citation.source_tier is SourceTier.VERIFIED_USER


class TestTheFilterIsReal:
    async def test_another_predicate_returns_a_different_incumbent(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """The same subject, a different predicate. Retrieval within
        `(namespace, subject, predicate)` means these must not mix - a
        cardinality check that compared an allergy against a language would
        never fire."""
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant, predicate="preferred_language", obj="Welsh"),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert [a.object for a in found.nearest] == ["English"]

    async def test_a_predicate_with_several_live_values_returns_them_all(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """`allergy` is `cardinality: many` in the clinical pack and the seed
        wrote four. All four are incumbents: §2.2 retrieves top-k, and a MANY
        predicate is exactly where k earns its place."""
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant, obj="latex"),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert sorted(str(a.object) for a in found.nearest) == [
            "latex",
            "penicillin",
            "shellfish",
            "sulfa drugs",
        ]

    async def test_the_nearest_is_the_one_the_candidate_claims(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """Ordering, which only a real cosine can demonstrate.

        `FakeVectorStore` returns insertion order and says so, so this claim
        cannot be made in the unit suite. A candidate claiming `latex` must rank
        the seeded `latex` first among four live allergies - identical text
        embeds identically, which is the one property `HashEmbedder` guarantees.
        """
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant, obj="shellfish"),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert found.nearest[0].object == "shellfish"

    async def test_a_retired_fact_is_not_an_incumbent(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str], patient: EntityId
    ) -> None:
        """The seed supersedes the patient's address five months after intake.

        A superseded fact has already lost and cannot be contradicted by a new
        one, so only the successor comes back - which is `search`'s
        `valid_to IS NULL` filter, reached through this module.
        """
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant, predicate="home_address", obj="3 Calder Way, Leeds"),
            subject_id=patient,
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert [a.object for a in found.nearest] == ["3 Calder Way, Leeds"]


class TestAnUnknownSubject:
    async def test_a_subject_with_no_history_returns_an_empty_set(
        self, pool: asyncpg.Pool, demo: tuple[asyncpg.Connection, str]
    ) -> None:
        """A new patient is the ordinary case, not an error - and an empty set
        has to be reachable for `retrieve_incumbents` to mean anything when it
        is not."""
        _, tenant = demo
        store = PgVectorStore(pool, HashEmbedder(), tenant_id=TenantId(tenant), timeout_s=TIMEOUT_S)

        found = await retrieve_incumbents(
            candidate(tenant),
            subject_id=EntityId("00000000-0000-0000-0000-000000000000"),
            vector=store,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
        )

        assert (found.nearest, found.neighbours) == ([], [])
