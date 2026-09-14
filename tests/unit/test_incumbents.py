"""`incumbents.py`, against the fakes.  BUILD_NOTEBOOK.md S4.2

Named for the module it covers. Deliberately not `test_incumbent_retrieval.py`:
that name belongs to the integration suite, and `tests/` has no `__init__.py`,
so two files sharing a basename are two modules with the same name - pytest
aborts collection for the whole run and `mypy` refuses the pair outright. The
same trap took the test tree out of `make typecheck` at S3.2 with two
`conftest.py` files.


S4.2's DONE WHEN is an integration test - `tests/integration/
test_incumbent_retrieval.py`, against the seeded tenant - because the query it
makes is a real one: a partial index, a cosine ordering and an RLS policy, none
of which a fake has an opinion about.

What is here is everything the fakes *can* answer, which is the composition:
that the filter names the subject and the predicate rather than one of them,
that the query is embedded through the same renderer the stored vectors came
from, and that the graph half adds rather than repeats. Those are decisions in
this module, not in the stores, and a database would only make them slower to
check.

`FakeVectorStore.search` orders by insertion rather than by cosine and says so,
so no test here asserts an *ordering* - that claim belongs to the integration
suite and is made there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final

import pytest

from fixtures.assertions import NS, WHEN, stored_assertion
from fixtures.fakes import FakeGraphStore, FakeVectorStore
from guardmem_core.errors import StoreUnavailable
from guardmem_core.memory.vector.base import ScoredAssertion, embed_text
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.pipeline.l2_validate import retrieve_incumbents
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import CandidateId, EntityId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue

SUBJECT: Final = EntityId("e-1")
OTHER_SUBJECT: Final = EntityId("e-2")
TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")


def candidate(*, predicate: str = "allergy", obj: ObjectValue = "latex") -> MemoryCandidate:
    """A candidate claiming something about `SUBJECT`."""
    return MemoryCandidate(
        candidate_id=CandidateId("c_1"),
        tenant_id=TENANT,
        namespace=NS,
        subject="Joan Ellery",
        predicate=predicate,
        object=obj,
        valid_from=WHEN,
        provenance=Provenance(
            source_hash="sha256:abc",
            source_span=(0, 10),
            source_tier=SourceTier.VERIFIED_USER,
            verbatim="allergic to latex",
            captured_at=WHEN,
        ),
        extracted_by="claude-haiku-4-5",
        prompt_version="extract_memories@v1",
        trace_id=TraceId("tr_s42"),
    )


def visible(
    *,
    predicate: str = "allergy",
    obj: ObjectValue = "penicillin",
    subject: str = str(SUBJECT),
    valid_to: datetime | None = None,
) -> StoredAssertion:
    """A live, visible assertion - the only kind that can be an incumbent."""
    return stored_assertion(
        tenant_id=TENANT,
        subject=subject,
        predicate=predicate,
        obj=obj,
        valid_to=valid_to,
        visible=True,
    )


# `@dataclass` without `slots=True`, unlike the fake it extends, and that is not
# a style choice. `slots=True` builds a *new* class object and rebinds the name,
# so the zero-argument `super()` in a method below closes over the pre-slots
# class while `self` is an instance of the post-slots one - and Python raises
# `TypeError: super(type, obj): obj must be an instance or subtype of type` at
# the call rather than at the definition.
@dataclass
class RecordingStore(FakeVectorStore):
    """A `FakeVectorStore` that remembers what it was asked.

    The filter is the whole point of this step - §2.2 retrieves "within
    `(namespace, subject, predicate)`" - and a result set cannot show which of
    the three were actually applied, because the fake would return the same
    assertions if the module passed only one of them.
    """

    calls: list[dict[str, object]] = field(default_factory=list)

    async def search(
        self,
        *,
        namespace: Namespace,
        embedding: list[float],
        k: int,
        filters: dict[str, object],
        as_of: datetime | None = None,
    ) -> list[ScoredAssertion]:
        """Record the call, then answer it."""
        self.calls.append({"namespace": namespace, "k": k, "filters": filters, "as_of": as_of})
        return await super().search(
            namespace=namespace, embedding=embedding, k=k, filters=filters, as_of=as_of
        )


class TestTheQuery:
    async def test_it_filters_on_both_the_subject_and_the_predicate(self) -> None:
        """§2.2 retrieves within `(namespace, subject, predicate)`.

        Dropping either one is the failure that produces *more* incumbents and
        therefore looks like it is working - a cardinality check that compared
        an allergy against an address would simply never fire.
        """
        store = RecordingStore()

        await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        call = store.calls[0]
        assert call["namespace"] == NS
        assert call["filters"] == {"subject_id": SUBJECT, "predicate": "allergy"}

    async def test_it_asks_for_ten_by_default(self) -> None:
        """§2.2's k. Stated as a test because the number is a cost decision the
        S4.3 NLI pass pays per incumbent."""
        store = RecordingStore()

        await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert store.calls[0]["k"] == 10

    async def test_it_does_not_ask_for_a_point_in_time(self) -> None:
        """An incumbent is what is believed *now*. A superseded fact has already
        lost and is not something a new one can contradict."""
        store = RecordingStore()

        await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert store.calls[0].get("as_of") is None

    async def test_the_candidate_is_embedded_by_the_renderer_the_store_writes_with(
        self,
    ) -> None:
        """The correctness property this whole module rests on.

        Both sides of every cosine comparison go through `embed_text`. If this
        embedded the candidate's `verbatim`, or its subject, or a JSON dump, the
        distances would still be numbers and would still order the results.
        """
        embedder = HashEmbedder(record=True)

        await retrieve_incumbents(
            candidate(predicate="allergy", obj="latex"),
            subject_id=SUBJECT,
            vector=FakeVectorStore(),
            graph=FakeGraphStore(),
            embedder=embedder,
        )

        assert embedder.texts == ["allergy: latex"]
        assert embedder.texts == [embed_text(visible(predicate="allergy", obj="latex"))]


class TestWhatComesBack:
    async def test_a_matching_incumbent_is_returned(self) -> None:
        store = FakeVectorStore()
        incumbent = visible(predicate="allergy", obj="penicillin")
        await store.upsert([incumbent])

        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert [h.assertion.assertion_id for h in found.nearest] == [incumbent.assertion_id]

    async def test_another_subjects_fact_is_not_an_incumbent(self) -> None:
        """The filter, observed through the result rather than the call."""
        store = FakeVectorStore()
        await store.upsert([visible(subject=str(OTHER_SUBJECT))])

        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert found.nearest == []

    async def test_it_carries_the_candidate_it_was_retrieved_for(self) -> None:
        """A batch's results are collected before they are used, and attributing
        one candidate's incumbents to another is a contradiction check run
        against the wrong fact."""
        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=FakeVectorStore(),
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert found.candidate_id == "c_1"

    async def test_a_novel_fact_returns_an_empty_set_rather_than_raising(self) -> None:
        """Nothing about this subject yet is the ordinary case for a new
        patient, not an error."""
        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=FakeVectorStore(),
            graph=FakeGraphStore(),
            embedder=HashEmbedder(),
        )

        assert (found.nearest, found.neighbours) == ([], [])


class TestTheGraphWidensRatherThanRepeats:
    async def test_an_edge_already_in_nearest_is_not_repeated(self) -> None:
        """§2.2 says "**plus** graph neighbors".

        `neighbors(subject)` returns every live edge the subject asserts, which
        includes the very assertions the vector search just found. Without the
        filter the two overlap instead of adding.
        """
        incumbent = visible(predicate="allergy", obj="penicillin")
        store, graph = FakeVectorStore(), FakeGraphStore()
        await store.upsert([incumbent])
        await graph.upsert_assertion(incumbent)

        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=graph,
            embedder=HashEmbedder(),
        )

        assert [h.assertion.assertion_id for h in found.nearest] == [incumbent.assertion_id]
        assert found.neighbours == []

    async def test_a_fact_under_another_predicate_does_widen_it(self) -> None:
        """What the graph half is actually for: what else this subject says."""
        incumbent = visible(predicate="allergy", obj="penicillin")
        elsewhere = visible(predicate="home_address", obj="14 Ashfield Road")
        store, graph = FakeVectorStore(), FakeGraphStore()
        await store.upsert([incumbent])
        await graph.upsert_assertion(incumbent)
        await graph.upsert_assertion(elsewhere)

        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=store,
            graph=graph,
            embedder=HashEmbedder(),
        )

        assert [edge.predicate for edge in found.neighbours] == ["home_address"]

    async def test_a_retired_edge_is_not_a_neighbour(self) -> None:
        """`GraphStore.neighbors` returns live edges only, and the reason is
        invariant I6 by another route: a superseded belief fed into conflict
        detection is a retired fact re-entering the system."""
        graph = FakeGraphStore()
        await graph.upsert_assertion(visible(predicate="home_address", obj="old", valid_to=WHEN))

        found = await retrieve_incumbents(
            candidate(),
            subject_id=SUBJECT,
            vector=FakeVectorStore(),
            graph=graph,
            embedder=HashEmbedder(),
        )

        assert found.neighbours == []


@dataclass
class UnreachableStore(FakeVectorStore):
    """A store whose `search` is down. `StoreUnavailable` is what the protocol
    documents and what a caller may retry on."""

    async def search(
        self,
        *,
        namespace: Namespace,
        embedding: list[float],
        k: int,
        filters: dict[str, object],
        as_of: datetime | None = None,
    ) -> list[ScoredAssertion]:
        """Fail the way a dead Postgres does."""
        raise StoreUnavailable("postgres is unreachable")


class TestFailureIsNotAnEmptySet:
    async def test_a_store_outage_propagates(self) -> None:
        """Deliberately not caught.

        An incumbent set from a store that failed is an *empty* incumbent set,
        and an empty one is indistinguishable from "this is a novel fact" -
        which is the reading that lets a contradiction through. Retrying is the
        caller's decision; `StoreUnavailable` is retryable and says so.
        """

        with pytest.raises(StoreUnavailable):
            await retrieve_incumbents(
                candidate(),
                subject_id=SUBJECT,
                vector=UnreachableStore(),
                graph=FakeGraphStore(),
                embedder=HashEmbedder(),
            )
