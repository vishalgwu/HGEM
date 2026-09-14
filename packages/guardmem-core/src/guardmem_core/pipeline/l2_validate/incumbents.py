"""Incumbent retrieval.  BUILD_NOTEBOOK.md S4.2

`MEMORY_ENGINE.md` §2.2's first half: "Retrieve incumbents: top-k (k=10) by
cosine within `(namespace, subject, predicate)` plus graph neighbors 1 hop
out." The three checks that compare a candidate against what comes back are
`conflict.py`.

**S4.2 put this in `conflict.py` and S4.3 took it out again.** Both halves
together came to 409 lines against `RULES.md` §2.4's cap of 400, and the
cap's own error message says to split along a real seam rather than shave.
The seam is the one the notebook already draws between the two steps: this
talks to two stores and an embedder, `conflict.py` talks to an ontology and a
judge, and neither needs to read the other. `pgvector_store.py` was split the
same way at S3.2 for the same reason.

The step names the dependency outright: *this is where L2 depends on day 3 -
you cannot detect a contradiction without the incumbent.* So this is the first
code in the pipeline to call both stores, and the first caller of
`assertion_live_idx`, the partial index S3.1 built for exactly this query.

**The candidate and the incumbents must be embedded by the same renderer.**
`base.embed_text` computes the text a stored vector comes from; this embeds the
candidate with that same function, through the `Claim` protocol S4.2 added to
it. A second renderer here would produce cosine distances between texts shaped
differently on each side - numbers that look like similarity, order the results,
and mean nothing. Nothing would fail.

That renderer moved from `rowmap.py` to `base.py` at this step, and the import
contract is what found it: `rowmap` imports `asyncpg` for one annotation, so
reaching for the renderer from `pipeline/` pulled a database driver in behind
it. The contract `guardmem_core.pipeline` never imports a concrete store said
no, correctly - and the right answer was not to relax it but to notice that what
text a fact embeds as is a decision about meaning, not about column order.

**Retrieval takes a resolved `EntityId`, and nothing in this repository
produces one.** `MemoryCandidate.subject` is a surface form; `VectorStore`
filters on `subject_id`, a UUID. Entity resolution is the bridge, and it is
specified nowhere - not in the notebook, not in `MEMORY_ENGINE.md`, not in
`PROJECT_TREE.md`. Rather than invent one inside a retrieval function, this
takes the resolved id as an argument and the gap is written down where it will
be read: `DAILY_LOG.md`, and the `subject_id` docstring below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core.memory.vector.base import ScoredAssertion, embed_text
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.entity import Edge
from guardmem_core.types import CandidateId

if TYPE_CHECKING:
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder, VectorStore
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.types import EntityId

__all__ = ["IncumbentSet", "retrieve_incumbents"]

# §2.2's k, stated once. Ten is a retrieval decision rather than a tuning knob:
# it is how many live facts about the same subject and predicate the NLI pass at
# S4.3 is willing to pay for, and widening it multiplies that cost by the same
# factor. `GM_DEFAULT_K` is a different number entirely - that is extraction
# samples (§1.2) - so this deliberately does not read it.
_INCUMBENT_K: Final = 10

# One hop, which is what both callers of the graph want: §2.2 expands incumbents
# "plus graph neighbors 1 hop out" and the read path does the same. Two hops on
# a dense entity is a different cost class.
_HOPS: Final = 1


class IncumbentSet(GMModel):
    """What is already believed about a candidate's subject.

    Attributes:
        candidate_id: Which candidate this was retrieved for. Carried so a
            batch's results cannot be attributed to the wrong one after they
            are collected.
        nearest: Up to `k` live assertions sharing the candidate's namespace,
            subject and predicate, nearest first, each with the cosine
            similarity the index ordered it by. These are what `detect` runs
            against - each carries its provenance, so the NLI pass can compare
            `incumbent.provenance[*].verbatim` with the candidate's, and the
            score is what §2.3's resolution table reads.
        neighbours: Live edges out of the subject under **other** predicates.
            The "plus 1 hop" half of §2.2, and see `retrieve_incumbents` for why
            they are edges rather than assertions and why the overlap with
            `nearest` is removed.
    """

    candidate_id: CandidateId
    nearest: list[ScoredAssertion]
    neighbours: list[Edge]


async def retrieve_incumbents(
    candidate: MemoryCandidate,
    *,
    subject_id: EntityId,
    vector: VectorStore,
    graph: GraphStore,
    embedder: Embedder,
    k: int = _INCUMBENT_K,
) -> IncumbentSet:
    """Fetch what is already believed about this candidate's subject.

    Args:
        candidate: The proposed fact, after the schema gate has admitted it. A
            quarantined or rejected candidate has no business here - its
            predicate is not in the vocabulary these incumbents are indexed by.
        subject_id: The **resolved** entity. Not derived from
            `candidate.subject`, which is still a surface form: Layer 1 extracts
            what the speaker said and nothing in this repository turns "Joan
            Ellery" into an entity id. See the module docstring - the absence is
            a specification gap, not an oversight here, and passing the id in
            keeps the gap visible instead of burying a guess in this function.
        vector: The store to search. Bound to the tenant already, which is why
            no tenant is passed.
        graph: The graph to expand from.
        embedder: Embeds the candidate. The same `Embedder` the store was
            constructed with, or the distances are between vectors from two
            different models.
        k: How many nearest to return. §2.2's ten by default.

    Returns:
        The incumbent set, `nearest` ordered by cosine and `neighbours` in the
        graph's own order.

    Raises:
        StoreUnavailable: either store is unreachable. Retryable, and
            deliberately not caught: an incumbent set retrieved from a store
            that failed halfway is an *empty* incumbent set, and an empty one is
            indistinguishable from "this is a novel fact" - which is the reading
            that lets a contradiction through.

    Live rows only - `search` with no `as_of` filters `valid_to IS NULL` and
    `visible`, which is what "incumbent" means. A superseded fact is not
    something a new one can contradict; it has already lost.

    Sequential rather than concurrent, like the S3.3 relay's dispatch and for
    the same reason. `RULES.md` §2.2 wants fan-out through a `TaskGroup`, and
    there is nothing here to overlap: the graph backend is in-process until
    S7.1, so a task group would add machinery to save the cost of a dictionary
    lookup. S7.1 makes the graph a network hop and is the step that should
    revisit this.
    """
    embedding = (await embedder.embed([embed_text(candidate)]))[0]
    nearest = await vector.search(
        namespace=candidate.namespace,
        embedding=embedding,
        k=k,
        filters={"subject_id": subject_id, "predicate": candidate.predicate},
    )
    edges = await graph.neighbors(subject_id, hops=_HOPS)
    return IncumbentSet(
        candidate_id=candidate.candidate_id,
        nearest=nearest,
        neighbours=_widening(edges, nearest),
    )


def _widening(edges: list[Edge], nearest: list[ScoredAssertion]) -> list[Edge]:
    """Keep only the edges `nearest` does not already account for.

    Args:
        edges: Every live edge out of the subject.
        nearest: The assertions the vector search returned.

    Returns:
        The edges whose assertions are not already in `nearest`.

    §2.2 says "**plus** graph neighbors", and without this filter the two
    overlap heavily rather than adding: `neighbors(subject)` returns every live
    edge the subject asserts, which includes the very assertions the vector
    search just found under that predicate. What is left after the filter is the
    part that genuinely widens the picture - what else this subject says, under
    predicates the candidate did not claim.

    They stay `Edge` rather than becoming assertions because turning one back
    into a `StoredAssertion` means fetching it by id, and `VectorStore` has no
    such method - it searches. Adding one is a protocol change, and S4.3 is the
    step that will know whether the checks need the provenance or only the
    shape.
    """
    seen = {hit.assertion.assertion_id for hit in nearest}
    return [edge for edge in edges if edge.assertion_id not in seen]
