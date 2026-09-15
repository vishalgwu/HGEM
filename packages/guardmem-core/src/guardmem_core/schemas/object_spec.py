"""What a predicate's object may be.  MEMORY_ENGINE.md §2.1

A discriminated union on `type`, and the three arms are the value *domains* the
ontology can declare: a scalar, a code from a named system, or a reference to
another entity. `schema_gate.py` owns the coercion table that turns a proposed
`ObjectValue` into one of these; this module owns what "one of these" means.

**Split out of `ontology.py` by `RULES.md` §2.4's line cap**, and the seam is
real rather than a line count: a value domain is a statement about *values* and
changes when the domain gains a new kind of object, while `PredicateSpec` and
`Ontology` are statements about a *pack* and change when §2.1 does. The two had
been one module since S3.5 only because they arrived in one step.

`entity_ref` is the arm that makes the graph work: §2.1 resolves the ambiguity
between "a literal string" and "an entity id" by declaration rather than by
inspection, so a predicate declared `{type: entity_ref}` means its object string
*is* an `EntityId` - which is also why ADR-0008 has nothing to say about
objects. Only subjects need resolving.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from guardmem_core.schemas.base import GMModel

__all__ = ["CodedObject", "EntityRefObject", "ObjectSpec", "ScalarObject"]


class ScalarObject(GMModel):
    """An object that is a bare value of a JSON type.

    Attributes:
        type: Which of `ObjectValue`'s scalar arms this predicate takes -
            `"text"` and `"coded"` and `"entity_ref"` are all `str` at the value
            level, and the three are distinguished because they mean different
            things to the schema gate, not because they serialise differently.

    `ObjectValue` also admits a `dict`, and no member here declares one. That is
    the ontology doing its job rather than an omission: a structured object is
    only writable once a predicate declares it, and no clinical predicate does.
    """

    type: Literal["text", "boolean", "number"]


class CodedObject(GMModel):
    """A value drawn from a controlled terminology.

    Attributes:
        type: Always `"coded"`.
        system: The terminology, e.g. `"RxNorm"`. Required, and that is the
            whole reason this is its own model rather than an optional field on
            `ScalarObject`: a coded value whose system nobody declared cannot be
            validated, deduplicated or shown to a reviewer, so `{type: coded}`
            on its own has to be a load error and not a shrug.
    """

    type: Literal["coded"]
    system: str = Field(min_length=1)


class EntityRefObject(GMModel):
    """A reference to another node in the entity graph.

    Attributes:
        type: Always `"entity_ref"`.
        entity: The entity type the reference must resolve to. Checked against
            the pack's declared `entities` at load, so a typo is a load error
            rather than a dangling edge discovered by a traversal.

    This is the declaration `Edge.object` refers to when it says the ontology
    decides whether a string is an entity reference - and, from S3.4, what will
    let a multi-hop `neighbors()` walk stop following literals.
    """

    type: Literal["entity_ref"]
    entity: str = Field(min_length=1)


# Discriminated on `type`, so an unknown value names the field rather than
# reporting three unrelated failures, and so `{type: coded}` with no `system`
# fails against the coded branch specifically.
type ObjectSpec = Annotated[
    ScalarObject | CodedObject | EntityRefObject, Field(discriminator="type")
]
