"""`memory.get_entity`.  MCP_INTEGRATION.md §2.4, BUILD_NOTEBOOK.md S6.2

"Returns the graph view: an entity, its live assertions grouped by predicate,
and 1-hop neighbors with edge confidences. Optional `as_of` for point-in-time
reconstruction."

Two halves, two backends, and they are in very different states.

**The assertions are real.** They come from the vector store, filtered to the
entity, grouped by predicate - the same rows `memory.search` returns, asked by
subject instead of by similarity, so this tool is also the way a caller gets an
entity id it can then hand to `search`'s `subject` filter.

**The neighbours are empty in a fresh process, and this tool says so rather than
implying the entity has none.** `NetworkXGraphStore` holds the graph *in
memory*: `make seed` builds one and the script exits with it, and a
`guardmem-mcp` process starts with an empty graph that nothing repopulates - the
outbox relay rebuilds it by replaying, but only events that are still
undispatched, and a seeded database has none. So `neighbors: []` here means "this
process has no graph", not "this entity is isolated", and the two are worth
distinguishing when the field feeds `MEMORY_ENGINE.md` §3.3's blast-radius
score. **S7.1 is the fix** - Neo4j behind the same protocol is the first durable
backend - and until it lands the response carries `graph_backed` so a caller
cannot read a limitation as a finding.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any
from uuid import UUID

from guardmem_core.types import EntityId
from mcp_server.tools.arguments import parse_as_of
from mcp_server.tools.context import ToolRefusedError
from mcp_server.tools.search import assertion_view

if TYPE_CHECKING:
    from collections.abc import Iterable

    from guardmem_core.schemas.entity import Edge, StoredAssertion
    from mcp_server.tools.context import ToolContext

__all__ = ["run_get_entity"]

# How many of the entity's assertions to read. Generous relative to one
# subject's fact count - the seeded demo patient has 26 - and bounded because
# `RULES.md` §2.2 wants every query bounded, including the ones that look small.
_ASSERTION_LIMIT = 200


async def run_get_entity(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer §2.4 for one entity.

    Args:
        context: Tenant, namespace and a bound store.
        arguments: The tool call's arguments.

    Returns:
        The entity id, its live assertions grouped by predicate, its 1-hop
        neighbours, and `graph_backed` - see the module docstring on why that
        last field exists.

    Raises:
        ToolRefusedError: `entity_id` is missing or not a UUID.
        StoreUnavailable: Postgres is unreachable.

    The search is by `subject_id` filter rather than by vector: §2.4 asks for
    *this entity's* assertions, which is an exact question, and ranking them by
    similarity to a query nobody asked would reorder them by an accident of the
    embedder. `VectorStore.search` requires a query vector anyway, so the
    entity's own id is embedded to produce one - **not a zero vector**, which is
    the obvious choice and is wrong: cosine distance is undefined against a
    vector of zero magnitude, pgvector returns `NaN`, and `ScoredAssertion`
    rejects it. Over the wire that surfaced as "Invalid request parameters",
    which blames the caller for a server bug.

    What the resulting order *means* is nothing, and nothing here reads it:
    `_grouped` partitions by predicate. It matters only if an entity ever holds
    more than `_ASSERTION_LIMIT` assertions, in which case the limit selects an
    arbitrary subset of them rather than a meaningful one.
    """
    entity_id = _require_entity_id(arguments)
    as_of = parse_as_of(arguments)

    hits = await context.store.search(
        namespace=context.namespace,
        embedding=(await context.state.embedder.embed([entity_id]))[0],
        k=_ASSERTION_LIMIT,
        filters={"subject_id": entity_id},
        as_of=as_of,
    )
    neighbours = await context.state.graph.neighbors(EntityId(entity_id))
    return {
        "entity_id": entity_id,
        "namespace": context.namespace,
        "as_of": as_of.isoformat() if as_of else None,
        "assertions": _grouped(hit.assertion for hit in hits),
        "neighbors": [_edge_view(edge) for edge in neighbours],
        # See `ServerState.graph_durable`: an empty neighbour list from an
        # in-process graph is a limitation, not a finding, and a caller has no
        # other way to tell.
        "graph_backed": context.state.graph_durable,
    }


def _grouped(assertions: Iterable[StoredAssertion]) -> dict[str, list[dict[str, Any]]]:
    """Group §2.4's assertions by predicate.

    Returns:
        Predicate to the assertions holding it, each rendered exactly as
        `memory.search` renders one - reused rather than re-shaped, so an agent
        that has learned to read a search result reads this one too, and a
        change to the provenance shape cannot land in one tool and not the other.

    A `defaultdict` collapsed to a plain dict on the way out: a `defaultdict`
    serialises identically and then *invents* keys on any later lookup, which is
    a surprise to hand a caller.
    """
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for assertion in assertions:
        grouped[assertion.predicate].append(assertion_view(assertion))
    return dict(grouped)


def _edge_view(edge: Edge) -> dict[str, Any]:
    """One 1-hop neighbour, with §2.4's "edge confidences"."""
    return {
        "assertion_id": edge.assertion_id,
        "subject": edge.subject_id,
        "predicate": edge.predicate,
        "object": edge.object,
        "confidence": edge.confidence,
        "valid_from": edge.valid_from.isoformat(),
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
    }


def _require_entity_id(arguments: dict[str, Any]) -> str:
    """Read and validate §2.4's `entity_id`.

    Raises:
        ToolRefusedError: missing, not a string, or not a UUID.

    Same reasoning as `search`'s `subject`: `assertion.subject_id` is a `uuid`
    column, so a surface form reaches asyncpg and fails on the cast with a
    message about input syntax. Entity resolution - name to id - is specified in
    no document and implemented nowhere, so this cannot accept a name however
    much a caller wants it to.
    """
    entity_id = arguments.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise ToolRefusedError("`entity_id` is required and must be a string")
    try:
        UUID(entity_id)
    except ValueError as exc:
        raise ToolRefusedError(
            f"`entity_id` must be a resolved entity id (a UUID), got {entity_id!r}. "
            "Entity resolution - turning a name into an id - is specified in no "
            "document and implemented nowhere yet."
        ) from exc
    return entity_id
