"""In-memory doubles for the three infrastructure protocols.  S1.7

S1.7: "They are how every unit test for the next four days runs without Docker."
`RULES.md` §5 makes that a rule rather than a convenience - unit tests are
"pure logic, no I/O, fakes only", with network blocked by a fixture and LLM
calls mocked.

**These are fakes, not mocks, and the difference matters here.** A mock asserts
that a call happened; these actually implement the contract, because three of
the protocol's guarantees are behavioural and a test that mocked them away would
pass while the real store broke them:

- `search` never returns a tombstoned or invisible assertion (invariant I6),
- `supersede` retires rather than deletes (`RULES.md` non-negotiable #2),
- `upsert` is idempotent by id, because the outbox relay replays (S3.3).

So the fakes enforce all three, and `tests/unit/test_fakes.py` holds them to it.
A fake that quietly permitted what pgvector forbids would make the entire week-1
unit suite a measurement of the wrong thing.

What they deliberately do **not** model: real embedding (`FakeLLM` has no
model and `FakeVectorStore` scores by insertion order, so "nearest" means
"most recently written" unless a test says otherwise), latency, cost, or
provider failure modes. Those belong to the integration suite, against
testcontainers, from S3.2.

**There is no `HashEmbedder` here, and there was until the S3.6 audit.** It is
`HashEmbedder` in `guardmem_core.memory.vector.hash_embedder` now - moved
because the S3.6 seed needed the same deterministic vectors, could not import
`tests/`, and had grown a fifteen-line twin that was free to drift. Renamed
rather than aliased, because it had stopped being a fake: it is the only
`Embedder` the package ships, and calling shipped code a fake in the one place
people look for doubles is how it ends up in production by accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.llm.base import LLMClient, LLMResponse, Tier
from guardmem_core.memory.graph.base import GraphStore
from guardmem_core.memory.vector.base import Embedder, ScoredAssertion, VectorStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.schemas.entity import Edge, StoredAssertion
from guardmem_core.types import AssertionId, EntityId, Namespace

__all__ = ["FakeGraphStore", "FakeLLM", "FakeVectorStore", "RecordedCall"]

# See `FakeVectorStore.search`: the fake holds no vectors, so it reports a
# constant rather than inventing a spread.
_FAKE_COSINE = 1.0


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One `FakeLLM.complete` invocation, kept so a test can assert on routing.

    The tier and the sample count are the two that tests actually check:
    `MEMORY_ENGINE.md` §1.2 ties K to the risk hint and §3.5 budgets FRONTIER at
    ≤6% of candidates, so "did this candidate escalate, and did it draw five
    samples?" is a question the unit suite asks repeatedly.
    """

    prompt: str
    tier: Tier
    temperature: float
    n: int
    schema: type[BaseModel] | None


@dataclass(slots=True)
class FakeLLM:
    """A scripted `LLMClient`. Deterministic, offline, and it records its calls.

    Hands back `responses` in order, one per `complete()` call. When they run
    out it repeats the last one rather than raising, so a test that only cares
    about the first call does not have to script the rest.

    Attributes:
        responses: The scripted replies, in call order.
        calls: What was asked, in order. Appended by `complete`.
    """

    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        """Return the next scripted response, recording the call.

        Args:
            prompt: Recorded, not interpreted.
            schema: Recorded, not enforced - a fake that validated its own
                scripted output would be testing the script, and the caller is
                what parses in the real path anyway.
            tier: Recorded. This is the field routing tests assert on.
            temperature: Recorded.
            n: Recorded. Note the fake does **not** trim or pad
                `response.samples` to match: a test that scripts three samples
                and asks for five should see the mismatch rather than have it
                silently repaired.

        Returns:
            The scripted `LLMResponse` for this call.

        Raises:
            IndexError: if nothing was scripted at all.
        """
        self.calls.append(
            RecordedCall(prompt=prompt, tier=tier, temperature=temperature, n=n, schema=schema)
        )
        if not self.responses:
            raise IndexError(
                "FakeLLM has no scripted responses; set `responses` before the "
                "code under test calls complete()"
            )
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


# The closed vocabulary `PgVectorStore._FILTER_COLUMNS` publishes. Duplicated
# here rather than imported, and the duplication is the lesser evil: importing it
# would make the unit suite's fake depend on the concrete Postgres store, which
# is the dependency direction `PROJECT_TREE.md` and the import-linter contract
# both exist to forbid. `test_the_fake_refuses_the_filter_keys_the_store_refuses`
# is what keeps the two lists in step.
_SEARCHABLE = frozenset({"subject_id", "predicate"})


def _check_filters(filters: dict[str, object]) -> None:
    """Refuse a filter key the real store would refuse.

    Raises:
        KeyError: the key is outside `_SEARCHABLE`.

    S1.7's rule about fakes, applied to a divergence that had gone unnoticed:
    `PgVectorStore` raises `KeyError` on an unknown filter key - deliberately,
    because a caller-supplied key reaching a query string is how `RULES.md` §4's
    parameterised-SQL rule gets broken by accident - while this fake matched it
    with `getattr(..., None)` and quietly returned nothing. A typo'd filter
    therefore passed the unit suite as "no results" and failed the integration
    suite as an error, which is the wrong way round: the cheap suite should
    catch it.
    """
    unknown = sorted(set(filters) - _SEARCHABLE)
    if unknown:
        raise KeyError(
            f"{unknown} is not a searchable column; allowed: {sorted(_SEARCHABLE)}. "
            "This fake refuses exactly what PgVectorStore refuses."
        )


def _is_valid_at(assertion: StoredAssertion, as_of: datetime | None) -> bool:
    """Is this assertion believed now, or was it true at `as_of`?

    The half-open convention matches the store's, which matches `supersede`:
    `prior.valid_to` is set to the successor's `valid_from`, so at exactly that
    instant the successor is true and the prior one is not.
    """
    if as_of is None:
        return assertion.valid_to is None
    return assertion.valid_from <= as_of and (
        assertion.valid_to is None or assertion.valid_to > as_of
    )


@dataclass(slots=True)
class FakeVectorStore:
    """An in-memory `VectorStore` that keeps the invariants pgvector keeps.

    Attributes:
        assertions: Everything ever written, by id, including retired rows -
            nothing is deleted here either, so a test can assert that a
            superseded assertion is still *present* and merely no longer
            *returned*.
    """

    assertions: dict[AssertionId, StoredAssertion] = field(default_factory=dict)

    async def upsert(self, assertions: Sequence[StoredAssertion]) -> None:
        """Store assertions, replacing any earlier row with the same id.

        Replacement rather than append is what makes this idempotent by
        `assertion_id`, which S3.3 requires because the outbox relay replays.
        """
        for assertion in assertions:
            self.assertions[assertion.assertion_id] = assertion

    async def search(
        self,
        *,
        namespace: Namespace,
        embedding: list[float],
        k: int,
        filters: dict[str, object],
        as_of: datetime | None = None,
    ) -> list[ScoredAssertion]:
        """Return up to `k` scored assertions in `namespace` matching `filters`.

        "Live" is the whole point of this method and it is three conditions,
        every one of which a real backend also applies: `visible` is true (the
        dual write landed), `valid_to` is unset (the fact is still believed),
        and `retracted_at` is unset. Invariant I6 says a tombstoned assertion
        never appears in retrieval results, and this is where the unit suite
        gets held to it.

        `as_of` moves the second of those conditions onto world time:
        `valid_from <= as_of < valid_to`, half-open, so a superseded assertion
        is recoverable at an instant it was still believed. The visibility and
        retraction conditions do not move - a partial write was never true at
        any time, and neither was a retracted one.

        `embedding` is accepted and ignored - ordering is by insertion, newest
        first. Tests that need a specific order should write in that order,
        which is more legible than scripting vectors.

        **Every hit scores `1.0`, and that is the same admission.** The fake
        holds no vectors, so it has no distance to report; a made-up spread
        would be a number tests could start asserting on and nothing would be
        measuring. Anything that turns on cosine - §2.3's resolution thresholds
        - belongs in the integration suite, against a store that actually
        computed one.
        """
        _check_filters(filters)
        matches = [
            assertion
            for assertion in reversed(list(self.assertions.values()))
            if assertion.namespace == namespace
            and assertion.visible
            and _is_valid_at(assertion, as_of)
            and assertion.retracted_at is None
            and all(
                getattr(assertion, attribute, None) == expected
                for attribute, expected in filters.items()
            )
        ]
        return [ScoredAssertion(assertion=match, cosine=_FAKE_COSINE) for match in matches[:k]]

    async def retired(
        self,
        *,
        namespace: Namespace,
        filters: dict[str, object],
        limit: int,
    ) -> list[StoredAssertion]:
        """Return retired assertions in `namespace`, newest retirement first.

        The exact complement of `search`'s temporal condition - `valid_to` set
        rather than unset - and deliberately **not** filtered on `visible`, which
        matches the store: a row superseded between its write and its relay pass
        is retired and was never readable, and somebody asking "what happened to
        that fact?" needs to see it.

        Raises:
            KeyError: `filters` named something outside the searchable set.

        The sort is by `valid_to` descending, as the store's `ORDER BY`. Ties
        keep insertion order, which `sorted` guarantees by being stable - two
        assertions retired at the same instant is exactly what a supersession of
        two facts in one transaction produces, so it is the normal case rather
        than a pathological one.
        """
        _check_filters(filters)
        matches = [
            assertion
            for assertion in self.assertions.values()
            if assertion.namespace == namespace
            and assertion.valid_to is not None
            and all(
                getattr(assertion, attribute, None) == expected
                for attribute, expected in filters.items()
            )
        ]
        newest_first = sorted(
            matches,
            key=lambda a: a.valid_to or datetime.min,
            reverse=True,
        )
        return newest_first[:limit]

    async def supersede(self, old_id: AssertionId, new_id: AssertionId, at: datetime) -> None:
        """Retire `old_id` in favour of `new_id`, without deleting anything.

        Sets `valid_to` and `superseded_by` on the incumbent, exactly as S3.2's
        `UPDATE assertion ... WHERE id = $3 AND valid_to IS NULL` does - and
        refuses in exactly the cases that statement matches zero rows.

        Raises:
            ConcurrencyConflict: `old_id` is not a live assertion here - it was
                already retired, or it was never written. S3.2 made the real
                store raise on a zero-row UPDATE rather than pass quietly,
                because that result is the only place a transposed
                `supersede(new, old)` can surface: both arguments are
                `AssertionId`, so nothing static tells them apart. A fake that
                stayed silent where the store raises would let a unit suite
                certify a call the integration suite then fails on.
        """
        incumbent = self.assertions.get(old_id)
        if incumbent is None or incumbent.valid_to is not None:
            raise ConcurrencyConflict(
                f"assertion {old_id} is not live; it was already superseded or "
                "never written. Re-read with search() and re-propose."
            )
        self.assertions[old_id] = incumbent.model_copy(
            update={"valid_to": at, "superseded_by": new_id}
        )


@dataclass(slots=True)
class FakeGraphStore:
    """An in-memory `GraphStore` over the edges implied by stored assertions.

    Attributes:
        edges: Every edge ever written, by `assertion_id`. Retired edges stay,
            for the same reason retired assertions do.
    """

    edges: dict[AssertionId, Edge] = field(default_factory=dict)

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Materialise `a` as one `ASSERTS` edge, idempotently by id."""
        self.edges[a.assertion_id] = Edge(
            assertion_id=a.assertion_id,
            subject_id=a.subject_id,
            predicate=a.predicate,
            object=a.object,
            confidence=a.confidence,
            valid_from=a.valid_from,
            valid_to=a.valid_to,
            trace_id=a.trace_id,
        )

    async def neighbors(self, entity: EntityId, hops: int = 1) -> list[Edge]:
        """Return live edges out of `entity`, following up to `hops` of them.

        Multi-hop resolves an edge's object back to an entity id by string
        equality, which is the same assumption `Edge.object` documents: the
        ontology decides whether a string is an entity ref, and a fake has no
        ontology. Cycles terminate because each edge is visited once.
        """
        seen: set[AssertionId] = set()
        found: list[Edge] = []
        frontier = {entity}
        for _ in range(max(hops, 0)):
            following: set[EntityId] = set()
            for edge in self._live_edges():
                if edge.subject_id in frontier and edge.assertion_id not in seen:
                    seen.add(edge.assertion_id)
                    found.append(edge)
                    if isinstance(edge.object, str):
                        following.add(EntityId(edge.object))
            if not following:
                break
            frontier = following
        return found

    async def degree(self, entity: EntityId) -> int:
        """Return how many live edges touch `entity`, in either direction.

        Both directions on purpose: `MEMORY_ENGINE.md` §3.3 asks "how much
        depends on this node?", and an entity that forty assertions point *at*
        has exactly the blast radius the feature is trying to price, even with
        no outgoing edges of its own.
        """
        return sum(
            1 for edge in self._live_edges() if edge.subject_id == entity or edge.object == entity
        )

    def _live_edges(self) -> list[Edge]:
        """Edges whose belief has not been retired."""
        return [edge for edge in self.edges.values() if edge.valid_to is None]


# Conformance, checked by the tool that can actually check it. `Protocol` is
# structural, so nothing above declares a base class and nothing would fail at
# import time if a signature drifted - `mypy --strict` is the only thing that
# notices, which is why `make typecheck` covers `tests/` as of this step. An
# `isinstance` check would not do: `@runtime_checkable` compares method *names*
# and ignores signatures, arity and async-ness entirely.
_llm: LLMClient = FakeLLM()
_embedder: Embedder = HashEmbedder()
_vectors: VectorStore = FakeVectorStore()
_graph: GraphStore = FakeGraphStore()
