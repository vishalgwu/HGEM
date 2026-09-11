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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from guardmem_core.llm.base import LLMClient, LLMResponse, Tier
from guardmem_core.memory.graph.base import GraphStore
from guardmem_core.memory.vector.base import VectorStore
from guardmem_core.schemas.entity import Edge, StoredAssertion
from guardmem_core.types import AssertionId, EntityId, Namespace

__all__ = ["FakeGraphStore", "FakeLLM", "FakeVectorStore", "RecordedCall"]


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
    ) -> list[StoredAssertion]:
        """Return up to `k` live assertions in `namespace` matching `filters`.

        "Live" is the whole point of this method and it is three conditions,
        every one of which a real backend also applies: `visible` is true (the
        dual write landed), `valid_to` is unset (the fact is still believed),
        and `retracted_at` is unset. Invariant I6 says a tombstoned assertion
        never appears in retrieval results, and this is where the unit suite
        gets held to it.

        `embedding` is accepted and ignored - ordering is by insertion, newest
        first. Tests that need a specific order should write in that order,
        which is more legible than scripting vectors.
        """
        matches = [
            assertion
            for assertion in reversed(list(self.assertions.values()))
            if assertion.namespace == namespace
            and assertion.visible
            and assertion.valid_to is None
            and assertion.retracted_at is None
            and all(
                getattr(assertion, attribute, None) == expected
                for attribute, expected in filters.items()
            )
        ]
        return matches[:k]

    async def supersede(self, old_id: AssertionId, new_id: AssertionId, at: datetime) -> None:
        """Retire `old_id` in favour of `new_id`, without deleting anything.

        Sets `valid_to` and `superseded_by` on the incumbent, exactly as S3.2's
        `UPDATE` does, and like that statement it is a no-op when the row is
        already retired - which is what makes a transposed call harmless rather
        than corrupting.

        Raises:
            KeyError: if `old_id` was never written. The real store's `UPDATE`
                would match no rows and pass silently; here it is loud, because
                in a unit test a supersede against an id that does not exist is
                always a broken test rather than a race.
        """
        incumbent = self.assertions[old_id]
        if incumbent.valid_to is not None:
            return
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
_vectors: VectorStore = FakeVectorStore()
_graph: GraphStore = FakeGraphStore()
