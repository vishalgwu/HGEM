"""The Cypher the Neo4j backend sends, and the schema it needs.  S7.1

Separate from `neo4j_store.py` for the reason `memory/vector/queries.py` is
separate from `pgvector_store.py`: a query is a schema decision written in a
string, and a reviewer reading `ARCHITECTURE.md` §5's model should be able to
check it against one file rather than against the control flow around it.

**Every statement is parameterised, including the hop count.** `RULES.md` §4
requires it, and the temptation here is specific: Cypher's variable-length path
syntax (`-[:ASSERTS*1..3]->`) takes a *literal* bound, not a parameter, so the
obvious way to write `neighbors(hops=n)` is to interpolate `n` into the query
text. `neo4j_store.py` runs one `HOP` per hop instead, passing the frontier as a
list parameter - the same loop `NetworkXGraphStore.neighbors` runs, which is
also why the two produce the same set.

**One node keyspace, two labels.** `ARCHITECTURE.md` §5 ends an `ASSERTS` edge
at `(:Entity|:Literal)`, and the `key` property is what both are matched on. A
string object is an `:Entity` keyed by its own value, exactly as
`NetworkXGraphStore._object_key` keys it - which is what lets a two-hop walk
follow an entity reference, and what makes a subject and a string object naming
it the same node. Anything that cannot be an entity reference - a number, a
boolean, a structured object - is a `:Literal` under a canonical `literal:` key.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DEGREE",
    "HOP",
    "SCHEMA",
    "START_TENANT",
    "UPSERT_ENTITY_OBJECT",
    "UPSERT_LITERAL_OBJECT",
]

# Uniqueness on `key` is what makes `MERGE` both correct and fast: without it
# two concurrent relay passes can each create a node for the same subject, and
# every later MERGE scans. The relationship index serves the `assertion_id`
# lookup that makes an upsert idempotent.
#
# `IF NOT EXISTS` on every one, because this runs at every startup and a second
# process starting concurrently must not fail on the first one's constraint.
SCHEMA: Final[tuple[str, ...]] = (
    "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT literal_key_unique IF NOT EXISTS FOR (n:Literal) REQUIRE n.key IS UNIQUE",
    "CREATE INDEX asserts_assertion_id IF NOT EXISTS FOR ()-[r:ASSERTS]-() ON (r.assertion_id)",
    "CREATE INDEX asserts_tenant_id IF NOT EXISTS FOR ()-[r:ASSERTS]-() ON (r.tenant_id)",
)

# The properties every `ASSERTS` edge carries. Written out rather than passed as
# a map, so the set of stored properties is visible in the query a reviewer
# reads - and so a rename is a Cypher change rather than a silent key mismatch.
#
# `SET r.valid_to = $valid_to` with a null **removes** the property, which is
# what keeps a replay honest: an edge retired and then replayed from a live
# assertion goes back to live. Verified against Neo4j 5.26.
_SET_EDGE: Final = """
SET r.subject_id = $subject,
    r.predicate = $predicate,
    r.object_json = $object_json,
    r.confidence = $confidence,
    r.valid_from = $valid_from,
    r.valid_to = $valid_to,
    r.trace_id = $trace_id,
    r.tenant_id = $tenant
"""

# `ON CREATE SET` for the tenant, not a plain `SET`. The subject's tenant is
# written once, when the node is first seen; rewriting it on every upsert would
# let a second tenant's write silently re-home an entity that already belongs to
# somebody else. `neo4j_store.py` checks the mismatch and refuses instead.
_MERGE_SUBJECT: Final = """
MERGE (s:Entity {key: $subject})
  ON CREATE SET s.tenant_id = $tenant, s.canonical_name = $canonical_name
"""

# A string object is an `:Entity` under its own value, so a subject and a
# reference to it converge on one node. No tenant is set: an object node is
# shared by construction - two tenants may both assert an allergy to penicillin
# - which is the asymmetry `NetworkXGraphStore._reject_foreign_tenant` states.
UPSERT_ENTITY_OBJECT: Final = (
    _MERGE_SUBJECT
    + """
MERGE (o:Entity {key: $object_key})
MERGE (s)-[r:ASSERTS {assertion_id: $assertion_id}]->(o)
"""
    + _SET_EDGE
)

UPSERT_LITERAL_OBJECT: Final = (
    _MERGE_SUBJECT
    + """
MERGE (o:Literal {key: $object_key})
MERGE (s)-[r:ASSERTS {assertion_id: $assertion_id}]->(o)
"""
    + _SET_EDGE
)

# The tenant a read is scoped to, taken from the node it starts at. Null for a
# node that has only ever been an object - see `neo4j_store.neighbors` for what
# that means and why it is reachable from a test and not from the pipeline.
START_TENANT: Final = """
MATCH (n {key: $entity})
RETURN n.tenant_id AS tenant
"""

# Undirected on purpose. `MEMORY_ENGINE.md` §3.3 asks "how much depends on this
# node?", and an entity that forty assertions point *at* has exactly the blast
# radius the feature prices - `NetworkXGraphStore.degree` counts both directions
# for the same reason, and the two must agree.
#
# `count(r)` over an `OPTIONAL MATCH` is zero for an entity the graph has never
# seen, which the protocol requires: an unknown subject is a novel one, and
# §3.3 prices novelty separately.
DEGREE: Final = """
MATCH (n {key: $entity})
OPTIONAL MATCH (n)-[r:ASSERTS]-()
WHERE r.valid_to IS NULL
  AND (n.tenant_id IS NULL OR r.tenant_id = n.tenant_id)
RETURN count(r) AS degree
"""

# One hop of the breadth-first walk. Live out-edges only: an edge whose
# `valid_to` is set describes a retired belief, and returning it would feed
# superseded state into conflict detection - invariant I6 by another route.
#
# The whole frontier goes in one query as a list parameter, so a hop is one round
# trip however wide it is - and `$frontier` is a parameter rather than a rendered
# list for the reason at the top of this module.
#
# `labels(o)` is what decides whether the walk continues through this object.
# `NetworkXGraphStore` follows an object when it `isinstance(str)`; here that is
# exactly the objects stored as `:Entity`, so the two walks visit the same
# nodes.
HOP: Final = """
MATCH (s)-[r:ASSERTS]->(o)
WHERE s.key IN $frontier
  AND r.valid_to IS NULL
  AND ($tenant IS NULL OR r.tenant_id = $tenant)
RETURN r.assertion_id AS assertion_id,
       r.subject_id AS subject_id,
       r.predicate AS predicate,
       r.object_json AS object_json,
       r.confidence AS confidence,
       r.valid_from AS valid_from,
       r.valid_to AS valid_to,
       r.trace_id AS trace_id,
       o.key AS object_key,
       'Entity' IN labels(o) AS object_is_entity
"""
