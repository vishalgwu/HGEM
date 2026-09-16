"""How an assertion's object becomes a node key.  S3.4, shared at S7.1

Extracted from `networkx_store.py` when S7.1 added a second backend. It is the
one piece of the graph model both must compute *identically*, because it decides
two things a contract test can see:

- **whether a two-hop walk can follow an object.** A string object keeps its own
  value as the key, so a subject and a reference to that subject converge on one
  node. That is what makes `neighbors(hops=2)` reach an entity's own edges, and
  it works without an ontology - which is the assumption `Edge.object`
  documents.
- **whether two writes of the same value land on one node.** A literal's key is
  canonical JSON with sorted keys, so `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}`
  are one node rather than two, and `degree()` counts what a reader would.

Two copies of this function would be two graph models that agree until somebody
changes one, and the disagreement would surface as a blast-radius score that
differs by backend - a number nobody can check by eye.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue

__all__ = ["LITERAL_PREFIX", "object_key"]

# Prefix for the node standing in for a non-string object. `ARCHITECTURE.md` §5
# ends an `ASSERTS` edge at an entity *or* a literal, and a literal still has to
# be a node for the edge to exist at all.
#
# The prefix is also what makes the key un-collidable with an `EntityId`: an id
# is a UUID string and cannot start with `literal:`, so a literal can never be
# mistaken for an entity reference by a walk.
LITERAL_PREFIX: Final = "literal:"


def object_key(value: ObjectValue) -> str:
    """Return the node key an assertion's object is stored under.

    Args:
        value: The stored value.

    Returns:
        The string itself when the object is one, and a canonical
        `literal:`-prefixed key otherwise.

    A string keeps its own value as the key, which is what makes `degree()`
    agree with `FakeGraphStore`'s `edge.object == entity` comparison and what
    lets a multi-hop walk follow an entity reference without an ontology.
    Everything else - a number, a boolean, a structured object - can never be an
    entity reference, so it gets a key that is deterministic (two writes of the
    same value land on one node) and cannot collide with an `EntityId`.
    """
    if isinstance(value, str):
        return value
    return f"{LITERAL_PREFIX}{json.dumps(value, sort_keys=True)}"
