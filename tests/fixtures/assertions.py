"""One builder for `StoredAssertion`, and the citation it needs.

Five test modules had grown their own near-identical copy: the pgvector
fixtures, and the unit tests for the store router, the graph store, the fakes
and the row map. Each varied a different two or three fields and spelled the
other eleven the same way, so a schema change meant five edits and a
`Provenance` change meant five more - and nothing would have failed if one had
been missed, because each module only ever exercised its own copy.

The sixth, `test_schema_models.py::_assertion`, deliberately stays where it is.
It takes `**overrides` over a plain dict because its job is to build models that
are *invalid* - a span that points at nothing, an interval that runs backwards -
and a builder with typed keyword arguments cannot express those.

`StoredAssertion` has fourteen fields and `min_length=1` provenance. That is
exactly the shape that should be built in one place: the defaults below are
"a well-formed, sourced, invisible fact", and a test overrides only the axis it
is about, which is also the axis a reader should notice.

Kept in `tests/fixtures/` rather than shipped: `scripts/demo_tenant_data.py`
builds assertions too and deliberately does **not** use this. Its provenance
spans are located in a real transcript by `link_span`, which is the property
that makes the seeded database honest; a builder whose citation is `sha256:abc`
at `(0, 10)` would quietly undo it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from uuid import uuid4

from guardmem_core.schemas.base import ObjectValue
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

__all__ = ["NS", "TENANT", "WHEN", "citation", "stored_assertion"]

# Shared defaults. `NS` and `WHEN` were declared separately in each of the three
# modules this replaced, with the same two values.
NS: Final = Namespace("patient:8812")
WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")


def citation(
    *,
    verbatim: str = "allergic to penicillin",
    span: tuple[int, int] = (13, 35),
    tier: SourceTier = SourceTier.VERIFIED_USER,
    alignment: float = 0.97,
    captured_at: datetime = WHEN,
) -> Provenance:
    """One well-formed `Provenance`.

    Args:
        verbatim: The supporting text. Not checked against any source here -
            these are unit fixtures, and the *real* guarantee that
            `source[span] == verbatim` is `span_linker`'s, proved by the I1
            property suite and relied on by the seed.
        span: Half-open character offsets.
        tier: Trust of the source.
        alignment: How closely the model quoted.
        captured_at: When the source was captured.

    Returns:
        The citation.
    """
    return Provenance(
        source_hash="sha256:abc",
        source_span=span,
        source_tier=tier,
        verbatim=verbatim,
        alignment=alignment,
        captured_at=captured_at,
    )


def stored_assertion(
    *,
    assertion_id: str | None = None,
    tenant_id: TenantId = TENANT,
    namespace: Namespace = NS,
    subject: str = "e-1",
    predicate: str = "allergy",
    obj: ObjectValue = "penicillin",
    confidence: float = 0.9,
    risk: float = 0.5,
    valid_from: datetime = WHEN,
    valid_to: datetime | None = None,
    visible: bool = False,
    provenance: list[Provenance] | None = None,
    trace_id: str = "tr_test",
) -> StoredAssertion:
    """A well-formed, sourced, invisible assertion.

    Args:
        assertion_id: Defaults to a fresh `uuid4`, so two calls are two
            assertions. Pass one to test replay, where the point is that two
            calls are the *same* assertion.
        tenant_id: Owning tenant.
        namespace: Isolation scope.
        subject: The resolved entity id.
        predicate: Ontology predicate.
        obj: The stored value.
        confidence: `C` at write time.
        risk: `R` at write time.
        valid_from: World-time it became true.
        valid_to: World-time it stopped; `None` means live.
        visible: Defaults false, which is what `upsert` writes and what the
            relay alone may change. A test that passes `True` is testing a
            caller bug, not a state the system produces.
        provenance: Defaults to one `citation()` quoting this object. A *list*,
            matching the model's own field, because `MEMORY_ENGINE.md` §2.4
            resolves a duplicate by appending a `Provenance` - a corroborated
            fact has several, and `test_vector_rowmap.py` builds exactly that.
        trace_id: The proposal it came from.

    Returns:
        The assertion.
    """
    return StoredAssertion(
        assertion_id=AssertionId(assertion_id or str(uuid4())),
        tenant_id=tenant_id,
        namespace=namespace,
        subject_id=EntityId(subject),
        predicate=predicate,
        object=obj,
        confidence=confidence,
        risk=risk,
        valid_from=valid_from,
        valid_to=valid_to,
        recorded_at=WHEN,
        provenance=provenance or [citation(verbatim=f"{predicate} {obj}")],
        trace_id=TraceId(trace_id),
        visible=visible,
    )
