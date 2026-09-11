"""The vector store contract.  BUILD_NOTEBOOK.md S1.7

Semantic recall over assertions: `ADR-0001` keeps this separate from the graph
because nearest-neighbour search and relational traversal "pull in opposite
directions", and `ARCHITECTURE.md` §2.4 makes the backend an operator decision
rather than an architectural one - pgvector by default, Qdrant above 10M
vectors, and `PRD.md` FR-4.1 says so in as many words.

**The store owns write-side embedding, and `search` does not.** That asymmetry
is deliberate and it is the notebook's, not an oversight here: §0.4's data table
names `memory/vector/pgvector_store.py` as the owner module for embeddings, and
`GM_EMBED_MODEL` is introduced at S3.2 - the step that builds this store. So
`upsert` takes assertions and derives their vectors, while `search` takes a
vector already computed, because the read path fuses dense with BM25 and a 1-hop
graph expansion (`ARCHITECTURE.md` read path) and holds the query embedding
across all three. Handing it back to this store to recompute would embed the
same query up to three times per recall.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.types import AssertionId, Namespace

__all__ = ["VectorStore"]


class VectorStore(Protocol):
    """Durable semantic storage for assertions.

    Two rules bind every implementation, and both are invariants rather than
    conventions:

    - **Nothing is ever deleted.** `RULES.md` non-negotiable #2 revokes `DELETE`
      at the database role level; retirement is `valid_to` plus `superseded_by`.
      S3.2 states it flatly: "Never `DELETE`. Ever."
    - **A partial write is never visible.** Rows land with `visible=false` and
      only the outbox relay flips them true once the graph side has landed
      (`ARCHITECTURE.md` §2.4). Readers filter `visible AND valid_to IS NULL`.
    """

    async def upsert(self, assertions: Sequence[StoredAssertion]) -> None:
        """Write assertions, invisibly, and embed them.

        Args:
            assertions: What to persist. Each arrives with `visible=False` by
                default and the implementation must keep it that way - making a
                row visible is the outbox relay's decision, taken after the
                graph side lands, and never this method's.

        Raises:
            StoreUnavailable: The store is unreachable. Retryable.

        Idempotent by `assertion_id`: S3.3 restarts the relay mid-flight and
        requires the assertion become visible "exactly once", which is only
        possible if a replayed write is a no-op rather than a duplicate row.
        """
        ...

    async def search(
        self,
        *,
        namespace: Namespace,
        embedding: list[float],
        k: int,
        filters: dict[str, object],
    ) -> list[StoredAssertion]:
        """Return the `k` nearest live assertions in a namespace.

        Args:
            namespace: Isolation scope. Not a filter among filters - tenant
                isolation is defence in depth (`RULES.md` §4: RLS *and*
                namespace prefixing *and* an app-layer check), so it is a
                separate required argument that no caller can forget to pass.
            embedding: The query vector, computed by the caller. See the module
                docstring for why this direction differs from `upsert`.
            k: How many to return. `MEMORY_ENGINE.md` §2.2 retrieves the top 10
                incumbents for conflict detection.
            filters: Extra predicates, e.g. subject and predicate for the
                incumbent lookup in §2.2.

        Returns:
            Up to `k` assertions, nearest first. **Never a tombstoned or
            invisible one** - invariant I6, and the reason it is stated on the
            protocol rather than left to each backend: a store that leaks a
            superseded fact into retrieval breaks the product's central claim
            while every test that does not look for it still passes.

        Raises:
            StoreUnavailable: The store is unreachable. Retryable.
        """
        ...

    async def supersede(self, old_id: AssertionId, new_id: AssertionId, at: datetime) -> None:
        """Retire `old_id` in favour of `new_id`, with a tombstone.

        Args:
            old_id: The incumbent being retired.
            new_id: What replaces it.
            at: World-time the supersession takes effect. Becomes
                `old.valid_to`, which `MEMORY_ENGINE.md` §2.3 sets to the
                candidate's `valid_from` so the two intervals abut exactly and a
                point-in-time query has no gap and no overlap.

        Raises:
            StoreUnavailable: The store is unreachable. Retryable.
            ConcurrencyConflict: `old_id` was already retired by someone else.

        Positional, unlike the rest of this protocol, because that is the
        signature S1.7 specifies. Worth knowing what that costs: `AssertionId`
        stops a `CandidateId` or a raw `str` reaching either slot, but both
        slots are the same type, so `supersede(new, old)` transposed is well
        typed and silently retires the wrong row. Nothing static will catch it -
        S3.2's `UPDATE ... WHERE valid_to IS NULL` is the only guard, and it
        turns the transposition into a no-op rather than an error. Pass these by
        keyword at every call site.
        """
        ...
