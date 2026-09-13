"""Hypothesis strategies for the ontology layer.  S3.5

Its own module rather than more lines in `strategies.py`, which was thirteen
lines below `RULES.md` §2.4's cap when this step started. The seam is real
rather than a place to put the overflow: these five models are the only ones in
the layer that describe a *declaration* rather than a fact, and two of them have
generation constraints nothing else has.

**`Ontology` cannot be built from independent parts.** Its validator requires
every `subject` and every `entity_ref` target to name a declared entity type, so
a strategy drawing predicates and entities separately would fail that validator
on almost every example - and `hypothesis` would report it as a flaky filter
rather than as the constraint it is. `_ontologies` draws the entity types first
and builds the predicates from them, which is the same order a person writes a
pack in.
"""

from __future__ import annotations

from hypothesis import strategies as st

from guardmem_core.schemas import (
    Cardinality,
    CodedObject,
    EntityRefObject,
    GMModel,
    ImpactLevel,
    Ontology,
    PredicateSpec,
    ScalarObject,
    SourceTier,
)

__all__ = ["ONTOLOGY_STRATEGIES"]

# Predicate and entity-type names. Deliberately narrow: these are keys in a
# hand-written YAML file, not user input, and generating arbitrary text here
# would test pydantic's `str` handling rather than anything this layer does.
_NAME = st.text("abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=12)
_ENTITY_TYPE = st.text("ABCDEFGHIJabcdefghij", min_size=1, max_size=10)
_SYSTEM = st.sampled_from(["RxNorm", "ICD-10-CM", "SNOMED-CT", "LOINC"])

_SCALARS = st.builds(ScalarObject, type=st.sampled_from(["text", "boolean", "number"]))
_CODED = st.builds(CodedObject, type=st.just("coded"), system=_SYSTEM)


def _entity_refs(entities: list[str]) -> st.SearchStrategy[EntityRefObject]:
    """Entity references that resolve, given the types a pack declares."""
    return st.builds(EntityRefObject, type=st.just("entity_ref"), entity=st.sampled_from(entities))


def _predicates(entities: list[str]) -> st.SearchStrategy[PredicateSpec]:
    """One predicate whose subject and object both resolve within `entities`."""
    return st.builds(
        PredicateSpec,
        subject=st.sampled_from(entities),
        object=st.one_of(_SCALARS, _CODED, _entity_refs(entities)),
        cardinality=st.sampled_from(Cardinality),
        impact=st.sampled_from(ImpactLevel),
        min_source_tier=st.sampled_from(SourceTier),
        requires_corroboration=st.booleans(),
    )


@st.composite
def _ontologies(draw: st.DrawFn) -> Ontology:
    """A pack whose every entity reference resolves, by construction.

    Entity types first, then predicates over them - see the module docstring for
    why the other order generates almost nothing but validator failures.
    """
    entities = draw(st.lists(_ENTITY_TYPE, min_size=1, max_size=4, unique=True))
    return Ontology(
        name=draw(_NAME),
        version=draw(st.integers(min_value=1, max_value=99)),
        entities=entities,
        predicates=draw(st.dictionaries(_NAME, _predicates(entities), min_size=1, max_size=4)),
    )


# Registered into `SCHEMA_STRATEGIES` by `strategies.py`, so the drift guard in
# `tests/property/test_schemas.py` covers these like any other model.
ONTOLOGY_STRATEGIES: dict[type[GMModel], st.SearchStrategy[GMModel]] = {
    ScalarObject: _SCALARS,
    CodedObject: _CODED,
    EntityRefObject: _entity_refs(["Patient", "Provider"]),
    PredicateSpec: _predicates(["Patient", "Provider"]),
    Ontology: _ontologies(),
}
