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

from pydantic import Field

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.types import AssertionId, Namespace

__all__ = ["Claim", "Embedder", "ScoredAssertion", "VectorStore", "embed_text"]


class ScoredAssertion(GMModel):
    """One search hit and how near it was.

    Attributes:
        assertion: The fact.
        cosine: Cosine **similarity** in [-1, 1], not pgvector's distance.
            `1 - (embedding <=> query)`, converted at the edge so nothing above
            the store has to remember which direction the operator runs in - a
            threshold compared against the wrong one passes silently and
            inverts the ranking.

    Added at S4.3, and the reason is a number that was being thrown away.
    `PgVectorStore.search` has always computed `embedding <=> $n AS distance` to
    order by it, then dropped it; `MEMORY_ENGINE.md` §2.3's resolution table
    keys three of its six rows on cosine (DUPLICATE at >= 0.95, REFINEMENT at
    >= 0.80, NONE below 0.80) and `ConflictReport.cosine` is a required field.

    Recomputing it above the store was the alternative and it is subtly wrong:
    the stored vector came from whatever embedder was configured *at write
    time*, so re-embedding an incumbent today compares the query against a
    vector the database does not hold. The number has to come from the database
    that measured it.
    """

    assertion: StoredAssertion
    cosine: float = Field(ge=-1.0, le=1.0)


class Claim(Protocol):
    """Anything with a predicate and an object, which is all `embed_text` reads.

    A `StoredAssertion` on the write side and a `MemoryCandidate` on the read
    side. The Protocol is what lets both use the *same* renderer, and S4.2 is
    the step that made that necessary: incumbent retrieval embeds a candidate
    and compares it by cosine against vectors computed from assertions, so if
    the two sides rendered their text differently the distances would be
    meaningless and nothing would say so. A second renderer beside this one is
    the single most dangerous duplication this module could grow.
    """

    @property
    def predicate(self) -> str:
        """Ontology predicate."""
        ...

    @property
    def object(self) -> ObjectValue:
        """The claimed or stored value."""
        ...


def embed_text(claim: Claim) -> str:
    """Render the text a claim's vector is computed from.

    Args:
        claim: The fact being written, or the candidate being matched against
            what is already there.

    Returns:
        A canonical rendering of the predicate and object.

    The subject is deliberately absent. `StoredAssertion.subject_id` is an
    `EntityId` - entity resolution has already happened - and a UUID contributes
    nothing an embedding model can use. The canonical name lives on `entity`,
    and joining it in would make the vector depend on a row this store does not
    own and cannot re-embed when it changes.

    `provenance[*].verbatim` is the other candidate, and it was considered: it is
    natural language, so it would very likely retrieve better than
    `"allergy: penicillin"` does. It is rejected because it is a *list*. A fact
    corroborated by three sources would have three texts and one vector slot, so
    the store would have to pick one or average them - and either way the same
    fact embeds differently depending on how many times it happened to be said,
    which is exactly the axis `MEMORY_ENGINE.md` §3.2 wants `S_cor` to carry and
    retrieval not to. Revisit with the eval harness at S22, which is the first
    point there is a number to compare.
    """
    return f"{claim.predicate}: {_render(claim.object)}"


def _render(value: ObjectValue) -> str:
    """Flatten an `ObjectValue` to something worth embedding.

    `json.dumps` on a bare string would embed the quotes, and on a dict it gives
    the model braces and colons to spend attention on. Structured objects are
    rendered as their values, which is what carries the meaning.
    """
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value)


class Embedder(Protocol):
    """Turns text into a vector.  S3.2

    Injected rather than constructed, so `guardmem-core` never imports a
    provider SDK - the same reason `LLMClient` is a Protocol. The store owns
    write-side embedding (see the module docstring) but not the *choice* of
    model: `settings.embed_model` names the pin, `llm/providers/` implements it
    from S9.1, and the eval harness and the unit suite pass deterministic
    doubles.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed `texts`, returning one vector per input in the same order.

        Args:
            texts: What to embed. Batched because a write is usually several
                assertions and one round trip is cheaper than several.

        Returns:
            One vector per text, in input order. Every vector must have the
            dimension the store expects - `assertion.embedding` is
            `VECTOR(1024)` per `ARCHITECTURE.md` §5, and a mismatch is a
            configuration error rather than a row that fails to write.

        Raises:
            ProviderUnavailable: the embedding provider is unreachable.
            BudgetExceeded: the tenant's cap is reached.
        """
        ...


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
        as_of: datetime | None = None,
    ) -> list[ScoredAssertion]:
        """Return the `k` nearest live assertions in a namespace, with their scores.

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
                incumbent lookup in §2.2. An implementation must treat the keys
                as a closed vocabulary rather than as column names to
                interpolate - `RULES.md` §4 requires parameterised SQL, and a
                caller-supplied key reaching a query string is how that rule
                gets broken by accident.
            as_of: Point-in-time query. `None` - the default and the common case
                - means "believed now": live rows only. A datetime means "true
                at that instant" in **world** time, so an assertion superseded
                since still appears. Added at S3.2, because
                `MCP_INTEGRATION.md` §2.1 already publishes `as_of` on
                `memory.search` and `ADR-0002` exists to make the question
                answerable; leaving it off the protocol would have meant a
                second retrieval path later.

                Note which axis this is. Supersession sets `valid_to`
                (`MEMORY_ENGINE.md` §2.3), so valid time is what makes a
                superseded assertion recoverable. Reconstructing what the system
                *believed* on a date - `recorded_at`/`retracted_at`, the system
                axis - is a different query and belongs with `memory.timeline`.

        Returns:
            Up to `k` `ScoredAssertion`s, nearest first. **Never a tombstoned or
            invisible one** - invariant I6, and the reason it is stated on the
            protocol rather than left to each backend: a store that leaks a
            superseded fact into retrieval breaks the product's central claim
            while every test that does not look for it still passes.

            The score is cosine *similarity*, not distance - see
            `ScoredAssertion`. Returning it is S4.3's requirement: §2.3's
            resolution table reads it and the store is the only thing that
            knows it.

        Raises:
            StoreUnavailable: The store is unreachable. Retryable.
        """
        ...

    async def retired(
        self,
        *,
        namespace: Namespace,
        filters: dict[str, object],
        limit: int,
    ) -> list[StoredAssertion]:
        """Return assertions this namespace once believed and has since retired.

        Args:
            namespace: Isolation scope, a separate required argument for the
                reason `search` gives.
            filters: The same closed vocabulary `search` takes, and an
                implementation must reject an unknown key the same way. Narrow
                by `subject_id` and `predicate` to ask "what did we retire *in
                place of* this?"; pass none to ask "what has this namespace
                retired lately?".
            limit: How many, most recently retired first.

        Returns:
            Assertions with `valid_to` set, newest retirement first, each
            carrying its full provenance and its `superseded_by`. Empty when the
            namespace has retired nothing matching.

        Raises:
            StoreUnavailable: The store is unreachable. Retryable.

        **This is the one read path that is allowed to return what `search` must
        not.** Invariant I6 says a tombstoned assertion never appears in
        *retrieval* results, and that is what `search` enforces - a retired fact
        leaking into the context an agent reasons from is the product's central
        failure. This answers a different question, asked deliberately and
        returned in its own field: `MCP_INTEGRATION.md` §2.1 publishes an
        `excluded` array next to the results precisely so "we have no record"
        and "we retired that record" stop being indistinguishable, and §2.5's
        `memory.timeline` is the same capability asked over one subject.

        Added at S6.2, the step that first serves §2.1. Before it there was no
        way to read a retired row at all: `search(as_of=...)` selects rows whose
        validity *contains* an instant, so asking it about now returns exactly
        the live set, and `valid_to IS NOT NULL` is not a filter it can express.
        `excluded` would have had to be a permanently empty array.

        **Not vector-ranked, and that is the deliberate part.** Ordering by
        retirement time rather than by similarity to a query makes the result
        explainable - "here is what this namespace stopped believing, most
        recent first" - and it is what the caller can act on. It also avoids a
        sequential scan with a distance computation over every dead row: the
        `assertion_hnsw` index is partial on `valid_to IS NULL AND visible`, so
        nothing retired is in it, by construction.
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
        typed and would silently retire the wrong row. Nothing static will catch
        it. `UPDATE ... WHERE valid_to IS NULL` is the only guard, and S3.2 made
        its zero-row result raise `ConcurrencyConflict` rather than pass
        quietly - which is what turns a transposition into a visible failure
        instead of a write that vanished. Still pass these by keyword.
        """
        ...
