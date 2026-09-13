"""The tenant ontology, as typed objects.  BUILD_NOTEBOOK.md S3.5

`MEMORY_ENGINE.md` §2.1 is the spec of record: the ontology "declares entity
types, predicates, value types, cardinality, impact level, and allowed source
tiers". Everything downstream of Layer 1 reads it. §2.1's schema gate coerces a
candidate's object against the declared value type and quarantines an unknown
predicate; §2.2(b) turns a second value for a `ONE` predicate into a
`CARDINALITY` conflict "regardless of NLI"; §3.3 floors `RiskVerdict.risk` at
the declared impact; §3.2's `S_cor` is what `requires_corroboration` demands.

Three of those consumers do not exist yet. This module is deliberately only the
*declaration* and the loader that validates it - the coercion table that turns
`{type: coded}` into a Python type belongs with `l2_validate/schema_gate.py`,
which is the code that will use it.

**The loader lives beside the models because `PROJECT_TREE.md` puts it there,**
and because the pair is the same thing said twice: the models are what a pack
may contain and the loader is what refuses a pack that contains anything else.
`prompts/loader.py` is the established shape for this and several of its
decisions are reused verbatim - the path-traversal guard on a name, the
`@cache`, and the trip through JSON that `_document` explains.

**Duplicate keys are an error, not a last-one-wins.** `yaml.safe_load` silently
keeps the final value, so a pack with two `allergy:` blocks would load, validate
and quietly enforce whichever came last. `prompts/loader.py` made the same
refusal for the same reason one step earlier; this is that decision applied to a
parser that does have an opinion.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import Field, model_validator

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.entity import Cardinality
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.verdict import ImpactLevel

__all__ = [
    "CodedObject",
    "EntityRefObject",
    "ObjectSpec",
    "Ontology",
    "PredicateSpec",
    "ScalarObject",
    "load_ontology",
    "parse_ontology",
]

# Packs live in `guardmem_core/ontology/<name>.yaml`, a sibling of `schemas/`.
_PACK_ROOT: Final = Path(__file__).resolve().parent.parent / "ontology"

# A pack name is one lowercase path segment. `_PACK_ROOT / name` would happily
# resolve `../../..`, and `load_ontology` is a public function of a library
# package - the same hole `prompts/loader.py` closed for the same reason.
_PACK_NAME: Final = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


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


class PredicateSpec(GMModel):
    """What one predicate is allowed to say, and how dangerous saying it is.

    Attributes:
        subject: The entity type this predicate attaches to. Checked against the
            pack's `entities` at load.
        object: The value domain. See `ObjectSpec`.
        cardinality: How many live values may coexist. `MEMORY_ENGINE.md`
            §2.2(b) reads `ONE` as a conflict trigger "regardless of NLI", which
            makes this the most consequential field in the file.
        impact: Declared blast radius. §3.3 floors `RiskVerdict.risk` at it
            (low .15, medium .35, high .60, critical .80), which is what keeps a
            confident write to a critical field out of the auto-write path.
        min_source_tier: The weakest source that may assert this at all.
            **Required**, unlike the sketch in §2.1 - see the module's step
            notes. There is no value that is safe to assume: defaulting
            permissive silently widens a safety surface, and defaulting strict
            makes an omission look like a broken predicate.
        requires_corroboration: Whether §3.2's `S_cor` must exceed one
            independent source. Defaults to `false`, which the §2.1 example's
            omissions unambiguously mean, and which is the ordinary case - most
            facts are believed on one source.
    """

    subject: str = Field(min_length=1)
    object: ObjectSpec
    cardinality: Cardinality
    impact: ImpactLevel
    min_source_tier: SourceTier
    requires_corroboration: bool = False


class Ontology(GMModel):
    """One versioned pack: the entity types and the predicates over them.

    Attributes:
        name: The pack, e.g. `"clinical"`. Must match the filename, which is
            what makes a copied file in the wrong place a load error rather
            than a silent mis-validation - the same rule `PromptSpec` applies.
        version: Bumped on every change. §2.1: "Ontologies are versioned; a
            schema change is an audited event and triggers a revalidation sweep
            of affected assertions." Nothing sweeps yet; the version is what
            makes it possible to know what to sweep.
        entities: Declared entity types. A closed list, so `subject` and
            `entity_ref` can be checked against it.
        predicates: The vocabulary, by name. §2.1 sends anything not in here to
            the `quarantine` namespace rather than rejecting it - which is why
            `predicate()` returns `None` instead of raising.
    """

    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    entities: list[str] = Field(min_length=1)
    predicates: dict[str, PredicateSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _every_referenced_entity_type_is_declared(self) -> Ontology:
        """Reject a pack whose predicates point at entity types it never declares.

        Returns:
            The ontology unchanged, once every reference resolves.

        Raises:
            ValueError: naming every unresolved reference at once, because
                fixing a pack one error per run is how a fifteen-predicate file
                takes fifteen edits.

        Without this, `subject: Provder` is a predicate no candidate can ever
        match and `entity: Pharmcy` is an edge that dangles - both of which look
        like an extraction failure at the other end of the pipeline, three
        layers away from the typo.
        """
        declared = set(self.entities)
        dangling = sorted(
            f"{name}.{field} -> {value!r}"
            for name, spec in self.predicates.items()
            for field, value in (
                ("subject", spec.subject),
                *(
                    (("object.entity", spec.object.entity),)
                    if isinstance(spec.object, EntityRefObject)
                    else ()
                ),
            )
            if value not in declared
        )
        if dangling:
            raise ValueError(
                f"undeclared entity types: {dangling}. Declared: {sorted(declared)}. "
                "Every subject and every entity_ref target must name a type in "
                "`entities`, or it is a predicate nothing can ever match."
            )
        return self

    def predicate(self, name: str) -> PredicateSpec | None:
        """Look up a predicate, or `None` if this pack does not declare it.

        Args:
            name: The predicate a candidate claims.

        Returns:
            Its spec, or `None`.

        `None` rather than a raise, deliberately. `MEMORY_ENGINE.md` §2.1 sends
        an unknown predicate to the `quarantine` namespace - "retrievable,
        flagged, never promoted without review" - which is a decision the schema
        gate makes, not an error this lookup should force.
        """
        return self.predicates.get(name)


class _StrictLoader(yaml.SafeLoader):
    """A `SafeLoader` that refuses a mapping with a repeated key."""


def _no_duplicate_keys(loader: _StrictLoader, node: yaml.MappingNode) -> dict[object, object]:
    """Construct a mapping, raising on a key that appears twice.

    Args:
        loader: The active loader.
        node: The mapping being constructed.

    Returns:
        The mapping.

    Raises:
        ValueError: on a duplicate key.

    `yaml.safe_load` keeps the last value silently, so a pack carrying two
    `allergy:` blocks would load, validate, and enforce whichever came second.
    In a file whose entire purpose is to pin what may be written and how
    dangerous it is, that is not a parsing nicety.
    """
    seen: set[object] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise ValueError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return dict(loader.construct_pairs(node, deep=True))


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def parse_ontology(raw: str, *, source: str) -> Ontology:
    """Validate one pack's YAML text into an `Ontology`.

    Args:
        raw: The pack, as text.
        source: What to name in an error message - a path, a URL, or a tenant
            id. Required rather than defaulted, because "line 14 is wrong" is
            not useful when three packs are being loaded.

    Returns:
        The validated ontology.

    Raises:
        ValueError: the text is not a YAML mapping, a key is duplicated, or a
            value is one JSON cannot carry.
        pydantic.ValidationError: a field is missing, unknown or ill-typed - an
            unknown `impact`, most usefully, which is this step's DONE WHEN.

    Separate from `load_ontology` because a pack does not have to be a file. A
    tenant-supplied ontology arrives over the wire, and a caller that had to
    write it to disk under `ontology/` to validate it would be forced to install
    an untrusted pack in order to find out whether it was valid.

    The round trip through JSON is not ceremony. `GMModel` is `strict=True`, so
    validating the parsed mapping directly would reject `"many"` for a
    `Cardinality` and `"critical"` for an `ImpactLevel` - every enum in the
    file, which is most of it. `schemas/base.py` records the escape hatch:
    strict mode relaxes for JSON input, because JSON has no type that could
    carry an enum otherwise. `prompts/loader.py` reached the same conclusion for
    the same reason.

    It also catches the one thing YAML can express and JSON cannot. An unquoted
    `2026-03-14` parses as a `datetime.date`, which would otherwise reach
    pydantic as an object no field declares; here it stops at the pack it came
    from.
    """
    try:
        parsed = yaml.load(raw, Loader=_StrictLoader)  # noqa: S506 - _StrictLoader is SafeLoader
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"{source}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{source}: an ontology pack must be a YAML mapping, got {type(parsed)}")
    try:
        document = json.dumps(parsed)
    except TypeError as exc:
        raise ValueError(
            f"{source}: the pack holds a value JSON cannot carry ({exc}). An "
            "unquoted date is the usual cause - quote it."
        ) from exc
    return Ontology.model_validate_json(document)


@cache
def load_ontology(name: str) -> Ontology:
    """Load and validate one ontology pack. Cached for the life of the process.

    Args:
        name: The pack, e.g. `"clinical"`. One lowercase path segment.

    Returns:
        The validated ontology.

    Raises:
        FileNotFoundError: no such pack is installed. On an editable install
            that means a typo; on a wheel it means the `.yaml` files were not
            packaged, which is worth checking with `uv build --wheel` the way
            S1.5 checked `py.typed`.
        ValueError: `name` is not a single path segment, the file is not a
            mapping, a key is duplicated, or `name` inside it disagrees with the
            file it was loaded from.
        pydantic.ValidationError: a field is missing, unknown or ill-typed - an
            unknown `impact`, most usefully, which is this step's DONE WHEN.

    Cached for the same reason prompts are: §2.1 versions ontologies so a
    revalidation sweep knows what changed, and a pack that could change under a
    running process would make `version` a claim rather than a fact.

    One consequence of that cache is worth stating, because `frozen=True` reads
    like it rules the problem out and does not. Freezing blocks attribute
    assignment, never mutation *inside* a container field (`schemas/base.py`
    says so), so `load_ontology("clinical").predicates.pop(...)` would succeed -
    and every later caller in the process would get the mutated pack. Treat the
    result as read-only; `model_copy` if you need a variant.
    """
    if not name or not set(name) <= _PACK_NAME:
        raise ValueError(
            f"ontology pack name {name!r} is not a single lowercase path segment; "
            "a name is a file under ontology/, never a path"
        )
    source = _PACK_ROOT / f"{name}.yaml"
    if not source.is_file():
        raise FileNotFoundError(
            f"no ontology pack at {source}. Packs live in "
            "packages/guardmem-core/src/guardmem_core/ontology/<name>.yaml "
            "(PROJECT_TREE.md)."
        )
    ontology = parse_ontology(source.read_text(encoding="utf-8"), source=str(source))
    if ontology.name != name:
        raise ValueError(
            f"{source}: the pack declares name {ontology.name!r} but sits at "
            f"{name}.yaml. The filename is the identity a call site uses, so the "
            "two may not disagree."
        )
    return ontology
