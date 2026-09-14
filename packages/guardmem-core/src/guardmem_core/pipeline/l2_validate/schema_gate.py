"""The ontology schema gate.  BUILD_NOTEBOOK.md S4.1

`MEMORY_ENGINE.md` §2.1, and the first thing in the pipeline to read the
ontology S3.5 built. Every candidate Layer 1 produced arrives here claiming a
predicate and a value; this decides whether the tenant's vocabulary admits it.

**Four outcomes, and the step names three because two of them share a door.**
§3.2's `S_sch` scale is what settles it - "1.0 exact ontology fit; 0.7 coerced;
0.4 unknown-but-plausible; 0 reject (never reaches scoring)". So:

- `PASS` (1.0) - the predicate exists and the value is already the declared type.
- `COERCED` (0.7) - the value became the declared type without inventing
  meaning. The confidence composite is where that costs something.
- `QUARANTINED` (0.4) - the predicate is not in the ontology. §2.1 sends it to
  the `quarantine` namespace, "retrievable, flagged, never promoted without
  review", so the candidate is **rewritten** rather than dropped.
- `REJECTED` (0) - the value cannot be the declared type. §2.1:
  `REJECT(reason=SCHEMA)`.

**Nothing here raises.** A stage that processed a batch and threw on the third
candidate would lose the other nine, and `DecisionRecord` is built to record a
`REJECT` with a reason rather than to catch one. `ValidationRejected` is for a
caller that handed this an incoherent *request*; a candidate that fails the
vocabulary is data, and data gets a verdict.

**Quarantine is a namespace rewrite, which is what makes the DONE WHEN
structural.** The step asks that the quarantine path never reach the store
router. It cannot: a quarantined candidate is returned in its own list, so
`admitted` carries only what may proceed - and even if a caller wrote one
anyway, its namespace is `quarantine:<tenant>` and every primary read is scoped
to a namespace that is not that one. Two independent reasons, which is what
`RULES.md` §4 means by defence in depth.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.ontology import CodedObject, EntityRefObject, ScalarObject
from guardmem_core.types import Namespace

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.schemas.ontology import ObjectSpec, Ontology

__all__ = ["GateOutcome", "GatedCandidate", "SchemaGateResult", "gate"]

# `MEMORY_ENGINE.md` §3.2's `S_sch` scale, which `ConfidenceReport.schema_fit`
# is filled from at Layer 3. Named here rather than written into each branch so
# the four numbers cannot drift apart, and so a reader can see the whole scale
# at once - the gaps between them are the point.
_FIT_EXACT: Final = 1.0
_FIT_COERCED: Final = 0.7
_FIT_UNKNOWN: Final = 0.4
_FIT_REJECTED: Final = 0.0

# The strings a boolean predicate may be written as. Deliberately tiny and
# deliberately not "truthy": `bool("no")` is `True`, which is the single most
# dangerous coercion this module could make - a patient declining consent
# recorded as having given it. Widening this set is a change to a safety
# surface and should arrive with a reason.
_TRUE_WORDS: Final = frozenset({"true", "yes", "y"})
_FALSE_WORDS: Final = frozenset({"false", "no", "n"})


class GateOutcome(StrEnum):
    """What the gate decided about one candidate.

    A closed vocabulary rather than a boolean, for the reason
    `ARCHITECTURE.md` §2.3 gives about obligations: the funnel in the dashboard
    groups by this, so an over-eager coercion shows up as a spike in one class
    rather than as a slow, invisible loss of fidelity.
    """

    # `noqa: S105` - flake8-bandit reads a constant named PASS as a hardcoded
    # password. It is the outcome §2.1 calls "pass", and the *value* is what
    # reaches the dashboard funnel and `DecisionRecord`, so renaming the member
    # to dodge the lint would put the code and the spec of record out of step.
    PASS = "pass"  # noqa: S105
    COERCED = "coerced"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


class GatedCandidate(GMModel):
    """One candidate and what the gate made of it.

    Attributes:
        candidate: The candidate as it should continue - which is **not**
            always the candidate that arrived. A coerced value is rewritten to
            the declared type, and a quarantined candidate's namespace is
            rewritten to `quarantine:<tenant>`. Downstream stages read this one.
        outcome: Which of the four doors it went through.
        schema_fit: §3.2's `S_sch`, which Layer 3 puts on `ConfidenceReport`.
            Carried per candidate rather than recomputed later, because by then
            the ontology lookup that produced it is gone.
        reason_code: Machine-readable rationale for
            `DecisionRecord.reason_codes`. `PRD.md` FR-3.4 wants the *why*
            legible without reading prose, and a rejected candidate that only
            says "rejected" is a support ticket.
    """

    candidate: MemoryCandidate
    outcome: GateOutcome
    schema_fit: float
    reason_code: str


class SchemaGateResult(GMModel):
    """What the gate admitted, held back, and refused.

    Three lists rather than one tagged list, and the shape is `NoiseResult`'s
    from S2.1 for the same reason: the caller's next move differs per group, so
    making it filter by a tag is handing it an opportunity to forget.

    Attributes:
        admitted: `PASS` and `COERCED`, in input order. The only group that may
            continue toward a primary write.
        quarantined: Unknown predicates, namespace already rewritten. Retrievable
            and flagged (§2.1), never promoted without review.
        rejected: Values the declared type cannot hold. These are recorded and
            go no further; §3.2 puts them at `S_sch = 0`, "never reaches
            scoring".
    """

    admitted: list[GatedCandidate]
    quarantined: list[GatedCandidate]
    rejected: list[GatedCandidate]


def gate(candidates: Sequence[MemoryCandidate], ontology: Ontology) -> SchemaGateResult:
    """Validate candidates against the tenant ontology.

    Args:
        candidates: What Layer 1 produced, in extraction order.
        ontology: The tenant's pack, from `load_ontology` or `parse_ontology`.

    Returns:
        The three groups, each in input order.

    Raises nothing. See the module docstring: a candidate that fails the
    vocabulary is data, and data gets a verdict rather than an exception.

    Two checks §2.1 describes are deliberately **not** here. The ontology
    declares each predicate's `subject` entity type, and this cannot check it:
    `MemoryCandidate.subject` is still a surface form, because entity
    resolution has not run - `StoredAssertion.subject_id` is where it becomes
    an `EntityId`, and S4.2 is where the resolution happens. And
    `min_source_tier` is not checked here either: `RULES.md` §4 makes the tier
    a *cap on what may auto-write*, which is a decision-matrix question, and
    answering it here would move a safety rule away from the table that
    composes it.
    """
    admitted: list[GatedCandidate] = []
    quarantined: list[GatedCandidate] = []
    rejected: list[GatedCandidate] = []
    for candidate in candidates:
        verdict = _judge(candidate, ontology)
        if verdict.outcome is GateOutcome.QUARANTINED:
            quarantined.append(verdict)
        elif verdict.outcome is GateOutcome.REJECTED:
            rejected.append(verdict)
        else:
            admitted.append(verdict)
    return SchemaGateResult(admitted=admitted, quarantined=quarantined, rejected=rejected)


def _judge(candidate: MemoryCandidate, ontology: Ontology) -> GatedCandidate:
    """Decide one candidate's outcome and rewrite it if the outcome says so.

    Args:
        candidate: The proposed fact.
        ontology: The tenant's pack.

    Returns:
        The verdict, carrying the candidate as it should continue.
    """
    spec = ontology.predicate(candidate.predicate)
    if spec is None:
        return GatedCandidate(
            candidate=_quarantined(candidate),
            outcome=GateOutcome.QUARANTINED,
            schema_fit=_FIT_UNKNOWN,
            reason_code="SCHEMA_UNKNOWN_PREDICATE",
        )
    fitted = _fit(candidate.object, spec.object)
    if fitted is None:
        return GatedCandidate(
            candidate=candidate,
            outcome=GateOutcome.REJECTED,
            schema_fit=_FIT_REJECTED,
            reason_code="SCHEMA_TYPE",
        )
    value, coerced = fitted
    if not coerced:
        return GatedCandidate(
            candidate=candidate,
            outcome=GateOutcome.PASS,
            schema_fit=_FIT_EXACT,
            reason_code="SCHEMA_OK",
        )
    return GatedCandidate(
        candidate=candidate.model_copy(update={"object": value}),
        outcome=GateOutcome.COERCED,
        schema_fit=_FIT_COERCED,
        reason_code="SCHEMA_COERCED",
    )


def _quarantined(candidate: MemoryCandidate) -> MemoryCandidate:
    """Move a candidate into its tenant's quarantine namespace.

    Args:
        candidate: The one whose predicate the ontology does not declare.

    Returns:
        The same candidate in `quarantine:<tenant>`.

    The spelling is `types.py`'s: "the `quarantine:<tenant>` namespace that
    failed guardrail content lands in". Rewriting rather than flagging is what
    makes the isolation structural - a flag has to be *checked* by every read
    path, and a namespace simply is not the one a primary read asks for.
    """
    return candidate.model_copy(
        update={"namespace": Namespace(f"quarantine:{candidate.tenant_id}")}
    )


def _fit(value: ObjectValue, spec: ObjectSpec) -> tuple[ObjectValue, bool] | None:
    """Return the value as the declared type, and whether coercion was needed.

    Args:
        value: What the candidate claims.
        spec: The predicate's declared object type.

    Returns:
        `(value, coerced)`, or `None` when the declared type cannot hold it.

    **Coercion never invents meaning, and the refusals are where that shows.**
    A `coded` or `entity_ref` object must already be a string: stringifying
    `71.5` into an RxNorm slot produces a code that does not exist, and a
    reviewer reading it back has no way to tell. A `number` refuses a `bool`
    and a `boolean` refuses a number, because `True` is not `1.0` in any
    clinical sense and the equality Python offers between them is an accident
    of `bool` subclassing `int`.
    """
    match spec:
        case CodedObject() | EntityRefObject():
            return (value, False) if isinstance(value, str) else None
        case ScalarObject(type="text"):
            return _as_text(value)
        case ScalarObject(type="number"):
            return _as_number(value)
        case _:
            return _as_boolean(value)


def _as_text(value: ObjectValue) -> tuple[ObjectValue, bool] | None:
    """Fit a value to a `text` predicate. A structured object is not text."""
    if isinstance(value, str):
        return value, False
    if isinstance(value, bool | float):
        return str(value), True
    return None


def _as_number(value: ObjectValue) -> tuple[ObjectValue, bool] | None:
    """Fit a value to a `number` predicate.

    `bool` is checked first and refused: it is a subclass of `int`, so a plain
    numeric test would silently accept `True` and store `1.0`.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return value, False
    if isinstance(value, str):
        try:
            return float(value), True
        except ValueError:
            return None
    return None


def _as_boolean(value: ObjectValue) -> tuple[ObjectValue, bool] | None:
    """Fit a value to a `boolean` predicate.

    A number is refused rather than read as truthy. `1.0` is not consent, and
    the one place this module could do real harm is recording a declined
    consent as a given one - see `_TRUE_WORDS`.
    """
    if isinstance(value, bool):
        return value, False
    if isinstance(value, str):
        word = value.strip().casefold()
        if word in _TRUE_WORDS:
            return True, True
        if word in _FALSE_WORDS:
            return False, True
    return None
