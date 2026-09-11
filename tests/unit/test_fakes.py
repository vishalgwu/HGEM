"""The fakes must keep the contracts the real stores keep.  S1.7

S1.7 only asks that the fakes exist and typecheck. That is not enough to trust
them: every unit test for the next four days runs on these, so a fake that
permits what pgvector forbids turns the whole week-1 suite into a measurement of
the wrong system - and it would do so silently, with every test green.

So the behavioural guarantees get tested here, at the point they are cheap to
fix. Three of them are invariants rather than conveniences:

- `search` never returns a tombstoned or invisible assertion (invariant I6),
- `supersede` retires rather than deletes (`RULES.md` non-negotiable #2),
- `upsert` is idempotent by id, because S3.3's outbox relay replays.

The conformance check itself - that these satisfy `LLMClient`, `VectorStore`
and `GraphStore` - is not here, because it cannot be. `Protocol` is structural,
so no runtime assertion sees a signature mismatch; `fakes.py` carries typed
bindings and `make typecheck` is what verifies them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fixtures.fakes import FakeGraphStore, FakeLLM, FakeVectorStore
from guardmem_core.llm.base import LLMResponse, Tier
from guardmem_core.schemas import Provenance, SourceTier, StoredAssertion
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

_WHEN = datetime(2026, 3, 12, 14, 31, 2, tzinfo=UTC)
_NS = Namespace("patient:8812")

_PROVENANCE = Provenance(
    source_hash="sha256:9c1",
    source_span=(212, 271),
    source_tier=SourceTier.VERIFIED_USER,
    verbatim="...penicillin - it gives me hives...",
    captured_at=_WHEN,
)


def _assertion(
    assertion_id: str,
    *,
    subject: str = "e_8812",
    predicate: str = "allergy",
    obj: str = "penicillin",
    visible: bool = True,
    valid_to: datetime | None = None,
    namespace: Namespace = _NS,
) -> StoredAssertion:
    """A live, visible assertion unless a keyword says otherwise."""
    return StoredAssertion(
        assertion_id=AssertionId(assertion_id),
        tenant_id=TenantId("t_acme"),
        namespace=namespace,
        subject_id=EntityId(subject),
        predicate=predicate,
        object=obj,
        confidence=0.94,
        risk=0.21,
        valid_from=_WHEN,
        valid_to=valid_to,
        recorded_at=_WHEN,
        provenance=[_PROVENANCE],
        trace_id=TraceId("tr_9f2a3c"),
        visible=visible,
    )


def _response(sample: str = "{}") -> LLMResponse:
    """A minimal scripted completion."""
    return LLMResponse(
        samples=[sample],
        model="claude-haiku-4-5",
        temperature=0.0,
        seed=None,
        tokens_in=10,
        tokens_out=5,
        cache_hit=False,
        latency_ms=12.0,
        cost_usd=0.0001,
    )


# --- FakeLLM ---------------------------------------------------------------


async def test_the_llm_returns_scripted_responses_in_order() -> None:
    """Determinism is the whole point - `RULES.md` §5 bans live calls in unit."""
    llm = FakeLLM(responses=[_response("first"), _response("second")])

    first = await llm.complete(prompt="p", tier=Tier.FAST)
    second = await llm.complete(prompt="p", tier=Tier.FAST)

    assert first.samples == ["first"]
    assert second.samples == ["second"]


async def test_the_llm_repeats_its_last_response_once_the_script_runs_out() -> None:
    """So a test asserting on the first call need not script every later one."""
    llm = FakeLLM(responses=[_response("only")])

    await llm.complete(prompt="p", tier=Tier.FAST)
    third = await llm.complete(prompt="p", tier=Tier.FAST)

    assert third.samples == ["only"]


async def test_the_llm_records_the_tier_and_sample_count() -> None:
    """The two fields routing tests assert on.

    `MEMORY_ENGINE.md` §1.2 ties K to the risk hint and §3.5 budgets FRONTIER at
    <=6% of candidates, so "did this escalate, and did it draw five samples?" is
    the question the next four days keep asking.
    """
    llm = FakeLLM(responses=[_response()])

    await llm.complete(prompt="adjudicate", tier=Tier.FRONTIER, temperature=0.7, n=5)

    assert len(llm.calls) == 1
    assert llm.calls[0].tier is Tier.FRONTIER
    assert llm.calls[0].n == 5
    assert llm.calls[0].temperature == 0.7


async def test_an_unscripted_llm_fails_loudly() -> None:
    """Silence here would surface four modules away as an empty extraction."""
    with pytest.raises(IndexError, match="no scripted responses"):
        await FakeLLM().complete(prompt="p", tier=Tier.FAST)


# --- FakeVectorStore -------------------------------------------------------


async def test_search_returns_a_live_visible_assertion() -> None:
    """The base case the three exclusions below are exclusions from."""
    store = FakeVectorStore()
    await store.upsert([_assertion("a_1")])

    found = await store.search(namespace=_NS, embedding=[0.1], k=10, filters={})

    assert [a.assertion_id for a in found] == ["a_1"]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"visible": False}, "the dual write has not landed"),
        ({"valid_to": _WHEN + timedelta(days=1)}, "the fact has been retired"),
    ],
    ids=["invisible", "tombstoned"],
)
async def test_search_never_returns_an_assertion_that_is_not_live(
    kwargs: dict[str, object], reason: str
) -> None:
    """Invariant I6, and `ARCHITECTURE.md` §2.4's visibility gate.

    A store that leaks either one breaks the product's central claim while every
    test that is not looking for it still passes.
    """
    store = FakeVectorStore()
    await store.upsert([_assertion("a_1", **kwargs)])  # type: ignore[arg-type]

    found = await store.search(namespace=_NS, embedding=[0.1], k=10, filters={})

    assert found == [], f"search returned an assertion where {reason}"


async def test_search_is_scoped_to_its_namespace() -> None:
    """`RULES.md` §4 makes isolation defence in depth; this is the app layer."""
    store = FakeVectorStore()
    await store.upsert([_assertion("a_1", namespace=Namespace("patient:4410"))])

    found = await store.search(namespace=_NS, embedding=[0.1], k=10, filters={})

    assert found == []


async def test_search_applies_filters_and_honours_k() -> None:
    """The incumbent lookup in `MEMORY_ENGINE.md` §2.2 is exactly this call."""
    store = FakeVectorStore()
    await store.upsert(
        [
            _assertion("a_1", predicate="allergy"),
            _assertion("a_2", predicate="allergy"),
            _assertion("a_3", predicate="preferred_pharmacy"),
        ]
    )

    allergies = await store.search(
        namespace=_NS, embedding=[0.1], k=10, filters={"predicate": "allergy"}
    )
    capped = await store.search(namespace=_NS, embedding=[0.1], k=1, filters={})

    assert {a.assertion_id for a in allergies} == {"a_1", "a_2"}
    assert len(capped) == 1


async def test_upsert_is_idempotent_by_assertion_id() -> None:
    """S3.3 kills the relay mid-flight and restarts it; a replay must not
    duplicate the row, or the assertion becomes visible more than once."""
    store = FakeVectorStore()
    await store.upsert([_assertion("a_1")])
    await store.upsert([_assertion("a_1", obj="amoxicillin")])

    found = await store.search(namespace=_NS, embedding=[0.1], k=10, filters={})

    assert len(found) == 1
    assert found[0].object == "amoxicillin"


async def test_supersede_retires_without_deleting() -> None:
    """`RULES.md` non-negotiable #2. The row must remain for audit and for
    point-in-time queries; it just stops being returned by retrieval."""
    store = FakeVectorStore()
    await store.upsert([_assertion("a_old")])
    at = _WHEN + timedelta(days=30)

    await store.supersede(AssertionId("a_old"), AssertionId("a_new"), at)

    retired = store.assertions[AssertionId("a_old")]
    assert retired.valid_to == at
    assert retired.superseded_by == "a_new"
    assert await store.search(namespace=_NS, embedding=[0.1], k=10, filters={}) == []


async def test_superseding_an_already_retired_assertion_is_a_no_op() -> None:
    """S3.2's `UPDATE ... WHERE valid_to IS NULL` matches nothing the second
    time. The fake behaves the same way, so a transposed or replayed call
    cannot rewrite a tombstone that is already correct."""
    store = FakeVectorStore()
    await store.upsert([_assertion("a_old")])
    first = _WHEN + timedelta(days=30)
    await store.supersede(AssertionId("a_old"), AssertionId("a_new"), first)

    await store.supersede(AssertionId("a_old"), AssertionId("a_other"), _WHEN)

    retired = store.assertions[AssertionId("a_old")]
    assert retired.valid_to == first
    assert retired.superseded_by == "a_new"


async def test_superseding_an_unknown_assertion_raises() -> None:
    """Loud on purpose. The real `UPDATE` would match no rows and pass; in a
    unit test that is always a broken test rather than a race."""
    with pytest.raises(KeyError):
        await FakeVectorStore().supersede(AssertionId("a_missing"), AssertionId("a_new"), _WHEN)


# --- FakeGraphStore --------------------------------------------------------


async def test_the_graph_materialises_an_assertion_as_an_edge() -> None:
    """`ARCHITECTURE.md` §5's `(:Entity)-[:ASSERTS]->(...)`."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1"))

    edges = await graph.neighbors(EntityId("e_8812"))

    assert [e.assertion_id for e in edges] == ["a_1"]
    assert edges[0].predicate == "allergy"


async def test_the_graph_follows_multiple_hops() -> None:
    """One hop is the default because both callers want one; two must work, or
    the parameter is a lie."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1", subject="e_a", obj="e_b"))
    await graph.upsert_assertion(_assertion("a_2", subject="e_b", obj="e_c"))

    one_hop = await graph.neighbors(EntityId("e_a"))
    two_hops = await graph.neighbors(EntityId("e_a"), hops=2)

    assert [e.assertion_id for e in one_hop] == ["a_1"]
    assert [e.assertion_id for e in two_hops] == ["a_1", "a_2"]


async def test_the_graph_terminates_on_a_cycle() -> None:
    """Each edge is visited once, so a loop cannot hang the risk scorer."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1", subject="e_a", obj="e_b"))
    await graph.upsert_assertion(_assertion("a_2", subject="e_b", obj="e_a"))

    edges = await graph.neighbors(EntityId("e_a"), hops=5)

    assert {e.assertion_id for e in edges} == {"a_1", "a_2"}


async def test_neighbors_excludes_retired_edges() -> None:
    """A retired edge describes a belief that is no longer held; returning it
    would feed superseded state into conflict detection."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1", valid_to=_WHEN + timedelta(days=1)))

    assert await graph.neighbors(EntityId("e_8812")) == []


async def test_degree_counts_both_directions() -> None:
    """`MEMORY_ENGINE.md` §3.3 asks how much depends on this node. An entity
    forty assertions point *at* has that blast radius with no outgoing edges."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1", subject="e_a", obj="e_target"))
    await graph.upsert_assertion(_assertion("a_2", subject="e_target", obj="x"))

    assert await graph.degree(EntityId("e_target")) == 2


async def test_degree_of_an_unknown_entity_is_zero() -> None:
    """Not an error: an unknown subject is a novel one, and §3.3 already prices
    novelty separately through its own feature."""
    assert await FakeGraphStore().degree(EntityId("e_never_seen")) == 0


async def test_graph_upsert_is_idempotent_by_assertion_id() -> None:
    """Same reason as the vector side: the outbox relay replays."""
    graph = FakeGraphStore()
    await graph.upsert_assertion(_assertion("a_1"))
    await graph.upsert_assertion(_assertion("a_1", obj="amoxicillin"))

    edges = await graph.neighbors(EntityId("e_8812"))

    assert len(edges) == 1
    assert edges[0].object == "amoxicillin"
