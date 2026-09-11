"""The shared model base for every schema.  BUILD_NOTEBOOK.md S1.6

`MEMORY_ENGINE.md` §0 opens with this base, named `_M`; `RULES.md` §2.1 states
the same configuration and names it `GMModel`. The name here is `GMModel`,
because a leading underscore marks a module-private name and this class is
imported by seven sibling modules and subclassed outside the package.

The configuration is four deliberate refusals:

``extra="forbid"``
    An unknown key is a contract violation, not a shrug. `RULES.md` §2.1 is
    explicit that this "matters most on LLM structured output: a hallucinated
    field should raise, not vanish silently" - which is the entire point of a
    governance gateway. A field the model invented must not be discarded
    quietly on the way to the store.

``frozen=True``
    Domain objects are immutable; transform with ``.model_copy(update=...)``.
    Note the limit: freezing blocks attribute assignment, not mutation *inside*
    a container field. ``result.candidates.append(...)`` still works on a frozen
    `ExtractionResult`, and no configuration pydantic offers prevents it.
    Freezing also makes a model hashable - but only while every field is itself
    hashable, so ``hash()`` on any model holding a `list` or `dict` raises
    `TypeError`. Neither is a reason to deviate from the spec of record, and
    both are pinned by tests so nobody discovers them at a bad moment.

``strict=True``
    No ``"1"`` -> ``1``. One documented exception survives: pydantic accepts an
    `int` where a `float` is declared, in strict mode, and converts it. So
    ``object=7`` on a `MemoryCandidate` stores ``7.0``. That is consistent with
    `MEMORY_ENGINE.md` §0 declaring the value type as `float` rather than
    `int | float`, and it round-trips stably, but it is surprising enough to be
    pinned by a test rather than left to be rediscovered.

``validate_default=True``
    From `RULES.md` §2.1's `GMModel`; `MEMORY_ENGINE.md` §0's `_M` omits it.
    RULES owns coding standards, so it is included. It costs nothing here - a
    default that cannot pass its own field's validation is a bug regardless -
    and it is what stops a later edit adding ``confidence: float = 1.5`` under
    a ``le=1`` bound.

**Strict mode is relaxed for JSON input, and that is what makes round-tripping
work at all.** Validating Python objects, a `datetime` field rejects a string, a
`tuple` field rejects a list, and an enum field rejects its own value as a bare
`str`. Validating JSON, all three are accepted, because JSON has no type that
could carry them otherwise. Every model in this layer therefore satisfies
``model_validate_json(model_dump_json(m)) == m`` while still refusing loose
input from Python callers. `tests/property/test_schemas.py` proves it over
generated instances of every model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["GMModel", "ObjectValue"]


class GMModel(BaseModel):
    """Base for every GuardMem schema. See the module docstring for the config."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


# The value domain of an assertion's object, from `MEMORY_ENGINE.md` §0
# (`MemoryCandidate.object: str | float | bool | dict`). It lives here, shared,
# rather than in either module that uses it: a candidate and the assertion it
# becomes must accept exactly the same values, and if the two definitions ever
# drift, a candidate that passed governance could fail to persist.
#
# Two deviations from the spec's spelling, both forced and neither semantic:
#
#   - `dict[str, object]`, not a bare `dict`. `mypy --strict` enables
#     `disallow_any_generics`, so a bare `dict` is an error; `dict[str, object]`
#     is the same type with its parameters stated.
#   - a `type` statement (PEP 695) rather than `x: TypeAlias = ...`, because
#     ruff's UP040 rewrites the latter on a py312 target. Verified that pydantic
#     resolves it.
#
# A JSON **array** is deliberately not a member. The spec lists four value
# shapes and a list is not among them: a multi-valued fact is modelled as
# several assertions under a `MANY`-cardinality predicate, which is what makes
# each one separately sourced, separately scored and separately retractable.
#
# The `dict` arm is only as durable as its contents are JSON-safe. Nothing here
# enforces that - a `datetime` nested inside one validates, serialises to a
# string, and comes back as a string, so the value silently stops being equal to
# itself across a round trip. That matters most for the audit chain, whose
# digest is taken over canonical JSON (S5.5). Pinned by test rather than
# enforced by a validator, which would cost a serialisation on every
# construction.
type ObjectValue = str | float | bool | dict[str, object]
