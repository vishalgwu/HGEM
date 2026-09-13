"""The in-process `GraphStore`.  BUILD_NOTEBOOK.md S3.4

The first real backend behind the S1.7 protocol, and `requirements/stores.txt`
says to build this one first. Its job is to let Layer 2 and the risk scorer call
`degree()` *today*: `MEMORY_ENGINE.md` §3.3 feeds it through
`min(1, log(1+deg)/log(1+50))` into the `graph_fanout` risk feature, so without
a graph that feature is a constant and the blast-radius half of the decision
matrix cannot be evaluated at all. S7.1 swaps Neo4j in behind the same three
methods and nothing above changes.

**A `MultiDiGraph` keyed by `assertion_id`, which is not a detail.** Multi,
because a subject asserts many things about the same object and each is its own
edge. Di, because `ARCHITECTURE.md` §5's shape is directed -
`(:Entity)-[:ASSERTS]->(:Entity|:Literal)`. And keyed by `assertion_id` because
that is what makes `upsert_assertion` idempotent for free: `add_edge` with an
existing key replaces the attributes rather than adding a parallel edge, which
is exactly the replay semantics the outbox relay needs (S3.3) and the protocol
already promises.

**This backend is single-tenant, `PROJECT_TREE.md` says so, and here that is
enforced rather than assumed.** The protocol's read methods take an `EntityId`
and no tenant - so a store holding two tenants' subgraphs could not filter
`degree()` even if it wanted to, and would quietly report one tenant's blast
radius using another's edges. Entity ids are database-wide UUIDs, so the
practical exposure is nil; `RULES.md` §4 asks for defence in depth anyway, and a
property that holds only because ids are unguessable is not a property. So
`upsert_assertion` refuses a subject already held for a different tenant. Point
the multi-tenant relay at this and the second tenant's first write raises, its
event stays pending, and the assertion stays invisible - which is the loud,
safe failure, and is how you find out you wanted Neo4j.

**Nothing here is `to_thread`ed.** `RULES.md` §2.2 sends CPU-bound work to a
thread, and these methods are `async` because the protocol is - but the work is
a dict lookup and an adjacency walk over an in-process graph. A thread hop would
cost more than the traversal. The step that makes this a network call is S7.1,
and that one is genuinely I/O.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import networkx as nx

from guardmem_core.errors import ValidationRejected
from guardmem_core.schemas.entity import Edge
from guardmem_core.types import EntityId

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.entity import StoredAssertion
    from guardmem_core.types import AssertionId

__all__ = ["NetworkXGraphStore"]

# Node attribute names. Constants because three methods read them and a typo in
# one would be a silent `None` rather than an error.
_TENANT: Final = "tenant_id"
_EDGE: Final = "edge"

# Prefix for the node standing in for a non-string object. `ARCHITECTURE.md` §5
# ends an `ASSERTS` edge at an entity *or* a literal, and a literal still has to
# be a node for the edge to exist at all. Strings keep their own value as the
# key - see `_object_key`.
_LITERAL: Final = "literal:"


class NetworkXGraphStore:
    """One tenant's entity graph, in memory.

    Structurally a `GraphStore` and not a subclass of one, for the reason S1.7
    made the contract a `typing.Protocol`: an implementation satisfies it by
    shape and imports nothing to do so. Nothing in `pipeline/` may import this
    class - S3.4's DONE WHEN says so and the `import-linter` contract in
    `pyproject.toml` enforces it, because a rule that is only written down is a
    suggestion.

    Not durable, and not pretending to be. The process holds the graph; a
    restart loses it and the outbox is what rebuilds it, since every event
    replays idempotently.
    """

    def __init__(self) -> None:
        """Start an empty graph.

        No tenant argument, unlike `PgVectorStore`. The tenant arrives on the
        first `upsert_assertion` and every later write is held to it - which
        gives the same guarantee without inventing a constructor parameter the
        protocol's readers could not use anyway.
        """
        self._graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Materialise `a` as one `ASSERTS` edge, idempotently by id.

        Args:
            a: The assertion. `subject_id` is an entity node; `object` is an
                entity node or a literal one, as `_object_key` decides.

        Raises:
            ValidationRejected: the subject is already held for a different
                tenant. See the module docstring for why a single-tenant
                backend refuses rather than merging.

        Replaying this is a no-op by construction: the edge key is the assertion
        id, so `add_edge` overwrites the attributes of the edge that is already
        there. That is what lets the S3.3 relay retry a dispatch it is not sure
        completed.
        """
        subject = str(a.subject_id)
        self._reject_foreign_tenant(subject, a)
        self._graph.add_node(subject, **{_TENANT: a.tenant_id})
        self._graph.add_node(_object_key(a.object))
        self._graph.add_edge(
            subject,
            _object_key(a.object),
            key=a.assertion_id,
            **{
                _EDGE: Edge(
                    assertion_id=a.assertion_id,
                    subject_id=a.subject_id,
                    predicate=a.predicate,
                    object=a.object,
                    confidence=a.confidence,
                    valid_from=a.valid_from,
                    valid_to=a.valid_to,
                    trace_id=a.trace_id,
                )
            },
        )

    async def neighbors(self, entity: EntityId, hops: int = 1) -> list[Edge]:
        """Return live edges reachable from `entity` within `hops`.

        Args:
            entity: The node to expand from.
            hops: How far. One by default, which is what both callers want -
                `MEMORY_ENGINE.md` §2.2 expands incumbents one hop and the read
                path does the same.

        Returns:
            The edges, live ones only. A retired belief fed into conflict
            detection is invariant I6 by another route.

        Order within a hop is **not** specified by the protocol and is not
        specified here: this walks adjacency, while `FakeGraphStore` scans its
        insertion-ordered dict, and the two agree on the *set*. Tests that
        compare them compare unordered, deliberately - pinning an order here
        would pin one that Neo4j will not reproduce either.

        Multi-hop follows a string object back to an entity of that id, which is
        the assumption `Edge.object` documents and the same one the fake makes:
        the ontology decides whether a string is an entity reference, and there
        is no ontology until S3.5. Cycles terminate because each edge is visited
        once.
        """
        seen: set[AssertionId] = set()
        found: list[Edge] = []
        # An ordered set. A plain `set` would make the traversal order depend on
        # string hashing, so two runs of the same test could return the same
        # edges in different orders - the kind of flake that gets diagnosed
        # three steps later.
        frontier: dict[str, None] = {str(entity): None}
        for _ in range(max(hops, 0)):
            following: dict[str, None] = {}
            for node in frontier:
                self._expand(node, seen, found, following)
            if not following:
                break
            frontier = following
        return found

    async def degree(self, entity: EntityId) -> int:
        """Return how many live edges touch `entity`, in either direction.

        Args:
            entity: The node to measure.

        Returns:
            The count of live edges, or zero for an entity this graph has never
            seen - `MEMORY_ENGINE.md` §3.3 already prices novelty separately, so
            an unknown subject is a novel one and not an error.

        Both directions on purpose. §3.3 asks "how much depends on this node?",
        and an entity that forty assertions point *at* has exactly the blast
        radius the feature exists to price, even with no outgoing edges of its
        own. `FakeGraphStore` counts the same way, which is what makes a unit
        test written against the fake mean something here.
        """
        node = str(entity)
        if node not in self._graph:
            return 0
        touching = list(self._graph.out_edges(node, data=True)) + list(
            self._graph.in_edges(node, data=True)
        )
        return sum(1 for *_, data in touching if data[_EDGE].valid_to is None)

    # --- internals ----------------------------------------------------------

    def _reject_foreign_tenant(self, subject: str, a: StoredAssertion) -> None:
        """Refuse a write whose subject belongs to somebody else's graph.

        Args:
            subject: The subject node key.
            a: The assertion being written.

        Raises:
            ValidationRejected: the node exists under a different tenant.

        Checked on the **subject** only, never on the object. A subject is
        always a resolved `EntityId` and belongs to exactly one tenant; an
        object is frequently a literal, and two tenants recording an allergy to
        penicillin legitimately share the node `"penicillin"`. Guarding that one
        would refuse correct writes.
        """
        held = self._graph.nodes.get(subject, {}).get(_TENANT)
        if held is not None and held != a.tenant_id:
            raise ValidationRejected(
                f"entity {subject} is already held for tenant {held}; this graph "
                f"cannot also hold it for {a.tenant_id}. The NetworkX backend is "
                "single-tenant (PROJECT_TREE.md) because the GraphStore protocol "
                "gives degree() and neighbors() no tenant to filter on. Use one "
                "store per tenant, or the Neo4j backend from S7.1.",
                trace_id=a.trace_id,
                candidate_id=a.assertion_id,
            )

    def _expand(
        self,
        node: str,
        seen: set[AssertionId],
        found: list[Edge],
        following: dict[str, None],
    ) -> None:
        """Walk one node's live out-edges, collecting them and the next frontier.

        Args:
            node: The node to expand.
            seen: Assertion ids already collected; mutated.
            found: The edges collected so far; mutated.
            following: The next frontier, as an ordered set; mutated.
        """
        if node not in self._graph:
            return
        for _, target, key, data in self._graph.out_edges(node, keys=True, data=True):
            edge: Edge = data[_EDGE]
            if edge.valid_to is not None or key in seen:
                continue
            seen.add(key)
            found.append(edge)
            if isinstance(edge.object, str):
                following[target] = None


def _object_key(value: ObjectValue) -> str:
    """Return the node key an assertion's object is stored under.

    Args:
        value: The stored value.

    Returns:
        The string itself when the object is one, and a canonical `literal:`
        key otherwise.

    A string keeps its own value as the key, which is what makes `degree()`
    agree with `FakeGraphStore`'s `edge.object == entity` comparison and what
    lets a multi-hop walk follow an entity reference without an ontology.
    Everything else - a number, a boolean, a structured object - can never be an
    entity reference, so it gets a key that is deterministic (two writes of the
    same value land on one node) and cannot collide with an `EntityId`.
    """
    if isinstance(value, str):
        return value
    return f"{_LITERAL}{json.dumps(value, sort_keys=True)}"
