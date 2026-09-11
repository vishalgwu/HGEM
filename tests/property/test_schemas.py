"""Generative properties of the schema layer.  BUILD_NOTEBOOK.md S1.6

S1.6's DONE WHEN asks for one property: "every model round-trips through
`model_dump_json` -> `model_validate_json`". Two more are asserted alongside,
because they are the other half of what `GMModel`'s configuration claims and
neither is checkable from one hand-written example - `extra="forbid"` must
reject an unknown key on *every* model, and `frozen=True` must refuse assignment
on *every* model.

The strategies live in `tests/fixtures/strategies.py`; this file is the
properties and the guard that keeps the two in step.
`test_every_model_has_a_strategy` fails if `SCHEMA_STRATEGIES` and
`conftest.all_schema_models()` disagree, so a schema added at a later step
without one cannot go quietly untested - the same way `tests/unit/test_errors.py`
walks the exception hierarchy rather than listing it.

`RULES.md` §5 requires 500 examples, which is what `_EXAMPLES` sets.
`deadline=None` is deliberate: these properties are about serialisation
identity, not latency, and a per-example wall-clock budget on a shared CI runner
measures the runner's scheduling rather than the code.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import HealthCheck, given, settings

from conftest import all_schema_models
from fixtures.strategies import ANY_SCHEMA, SCHEMA_STRATEGIES
from guardmem_core.schemas import GMModel

_EXAMPLES = 500


def _over_every_schema(check: Callable[[GMModel], None]) -> None:
    """Run `check` over generated instances of the whole layer, and prove it.

    The obvious shape - `@pytest.mark.parametrize` over the models with a
    `@given` on each - was measured and rejected. hypothesis costs ~0.45 s to
    set a test up whatever the example count, so three properties across sixteen
    models pays that 48 times: 198 s for the file at the 500 examples
    `RULES.md` §5 asks for, against 13 s drawing from a union of all of them.

    That trades away the guarantee that every model was exercised, so it is
    bought back explicitly - the run records which types it saw and this
    function fails if any schema was missed. Without that, "500 examples over a
    union" is a claim about probability, and a gate should not be one.

    Args:
        check: The property to assert, called once per generated instance.
    """
    exercised: set[type[GMModel]] = set()

    @settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(instance=ANY_SCHEMA)
    def run(instance: GMModel) -> None:
        exercised.add(type(instance))
        check(instance)

    run()

    missed = all_schema_models() - exercised
    assert not missed, (
        f"{_EXAMPLES} examples never produced: {sorted(m.__name__ for m in missed)}. "
        "The property held, but not for those - so it proves nothing about them."
    )


def test_every_model_has_a_strategy() -> None:
    """The registry must cover the layer, or the properties below lie.

    A schema added at a later step with no strategy would sail through a suite
    that looks exhaustive. This is the test that notices - and at S1.7 it did,
    catching `LLMResponse` the moment `conftest` started importing the whole
    package rather than whatever a test happened to pull in.
    """
    models = all_schema_models()
    missing = models - set(SCHEMA_STRATEGIES)
    stale = set(SCHEMA_STRATEGIES) - models

    assert not missing, f"schemas with no strategy: {sorted(m.__name__ for m in missing)}"
    assert not stale, (
        f"strategies for schemas that no longer exist: {sorted(m.__name__ for m in stale)}"
    )


def test_every_model_round_trips_through_json() -> None:
    """S1.6's DONE WHEN: `model_validate_json(model_dump_json(m)) == m`.

    This is what makes the audit log replayable and the MCP surface honest. It
    is also the property `strict=True` most threatens - a strictly-typed
    `datetime` field rejects a string in Python and accepts one from JSON, and
    that asymmetry is the only reason the round trip is possible at all.
    """

    def check(instance: GMModel) -> None:
        model = type(instance)
        assert model.model_validate_json(instance.model_dump_json()) == instance

    _over_every_schema(check)


def test_every_model_rejects_an_unknown_field() -> None:
    """`extra="forbid"`, on every model, through the JSON path.

    `RULES.md` §2.1: "a hallucinated field should raise, not vanish silently."
    The JSON path is the one that matters - that is where structured model
    output enters the system.
    """

    def check(instance: GMModel) -> None:
        payload = instance.model_dump(mode="json")
        payload["field_the_model_hallucinated"] = 1

        with pytest.raises(ValueError, match="field_the_model_hallucinated"):
            type(instance).model_validate(payload)

    _over_every_schema(check)


def test_every_model_is_frozen() -> None:
    """`frozen=True`, on every model.

    `RULES.md` §2.1: domain objects are immutable and transform via
    `.model_copy()`. Asserted across the layer rather than once on the base,
    because a module could override `model_config` and nothing else would
    notice.
    """

    def check(instance: GMModel) -> None:
        field = next(iter(type(instance).model_fields))

        with pytest.raises(ValueError, match="frozen"):
            setattr(instance, field, None)

    _over_every_schema(check)
