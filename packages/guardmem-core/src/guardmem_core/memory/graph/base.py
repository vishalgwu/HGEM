"""The graph store contract.  BUILD_NOTEBOOK.md S1.7

Relational entity state: `(:Entity)-[:ASSERTS {...}]->(:Entity|:Literal)` per
`ARCHITECTURE.md` §5. `ADR-0001` keeps this separate from the vector store
because "which of these facts contradict entity X's current 1:1 `primary_dx`
predicate?" is a traversal problem and nearest-neighbour search answers a
different one.

The protocol exists before either backend does, which is the point: S3.4 starts
on NetworkX so that "L2 and the risk scorer can call `degree()` today", and S7.1
swaps Neo4j in behind the same three methods via `GM_GRAPH_BACKEND`. S3.4's DONE
WHEN states the constraint plainly - "the pipeline never imports the concrete
class".
"""

from __future__ import annotations

from typing import Protocol

from guardmem_core.schemas.entity import Edge, StoredAssertion
from guardmem_core.types import EntityId

__all__ = ["GraphStore"]


class GraphStore(Protocol):
    """The entity graph, as the pipeline sees it.

    Two of these three methods exist to serve scoring rather than storage, which
    is why the protocol is this small: `MEMORY_ENGINE.md` §3.3 computes the
    `graph_fanout` risk feature as ``min(1, log(1+deg(subject))/log(1+50))``,
    and §2.2 expands incumbent retrieval by one hop. Anything wider is a
    retrieval concern and belongs in `memory/retrieval.py`.
    """

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Materialise an assertion as graph state.

        Args:
            a: The assertion. Its `subject_id` is a node and its `object` is a
                node or a literal, as the ontology declares for that predicate.

        Raises:
            StoreUnavailable: The graph is unreachable. Retryable - and note
                `ARCHITECTURE.md` §4 does not make this fatal: on a graph
                outage the write proceeds vector-only with a `graph_pending`
                flag and the outbox retries. What must never happen is dropping
                the assertion.

        Idempotent by `assertion_id`, for the same reason as the vector side:
        the outbox relay replays.
        """
        ...

    async def neighbors(self, entity: EntityId, hops: int = 1) -> list[Edge]:
        """Return edges reachable from `entity` within `hops`.

        Args:
            entity: The node to expand from.
            hops: How far. One by default, and one is what both callers want -
                `MEMORY_ENGINE.md` §2.2 expands incumbents "plus graph
                neighbors 1 hop out" and the read path does a "1-hop graph
                expand". Two hops on a dense entity is a different cost class,
                so the default is the safe one.

        Returns:
            The edges, live ones only - an edge whose `valid_to` is set
            describes a belief that has been retired, and returning it here
            would feed superseded state into conflict detection, which is
            invariant I6 by another route.

        Raises:
            StoreUnavailable: The graph is unreachable. Retryable.
        """
        ...

    async def degree(self, entity: EntityId) -> int:
        """Return how many live edges touch `entity`.

        This is the blast-radius signal. `MEMORY_ENGINE.md` §3.3 feeds it
        through ``min(1, log(1+deg)/log(1+50))`` into the `graph_fanout` risk
        feature - the question "how much depends on this node?", which is what
        separates overwriting a hobby from overwriting an account owner.

        Args:
            entity: The node to measure.

        Returns:
            The count of live edges. Zero for an entity the graph has never
            seen, rather than an error: an unknown subject is a novel one, and
            §3.3 already prices novelty separately.

        Raises:
            StoreUnavailable: The graph is unreachable. Retryable.
        """
        ...
