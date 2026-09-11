"""Policy obligations.  BUILD_NOTEBOOK.md S1.6

`ARCHITECTURE.md` §2.3: "Every guardrail returns a structured `Obligation`
(e.g. `require_review`, `require_corroboration`, `redact_field`) rather than a
boolean - the decision matrix composes obligations rather than
short-circuiting." An obligation can only tighten an outcome; `MEMORY_ENGINE.md`
§3.4 applies them after the matrix and notes that they "compose, they never
relax".

**`Rule` and `PolicyPack` are deliberately not here yet.** `PROJECT_TREE.md`
lists them in this module and they will land in it - at S12.2, the step that
builds the policy engine. Nothing today pins their fields: `ARCHITECTURE.md`
§2.3 says packs are "Rego-compatible", which leaves open whether a `Rule` is a
Python predicate, a compiled expression, or a handle on a Rego module, and
S12.2 is where that gets decided against a working engine. Guessing the shape
now is exactly the "three refactors later" this step exists to avoid, and
S1.6's own WATCH OUT says to resist it. What *is* specified today is the
obligation vocabulary, and that is what this module defines.
"""

from __future__ import annotations

from enum import StrEnum

from guardmem_core.schemas.base import GMModel

__all__ = ["Obligation", "ObligationKind"]


class ObligationKind(StrEnum):
    """The obligations a guardrail may impose on a decision.

    The three `ARCHITECTURE.md` §2.3 names. Its "e.g." means the list is open in
    principle, but adding a member is a change to a safety surface - an
    obligation composes into the decision matrix - so it should arrive with the
    document that names it. `RiskVerdict` validates against exactly this set, so
    a typo'd obligation fails loudly instead of being ignored.
    """

    REQUIRE_REVIEW = "require_review"
    REQUIRE_CORROBORATION = "require_corroboration"
    REDACT_FIELD = "redact_field"


class Obligation(GMModel):
    """A structured requirement returned by a guardrail.

    Attributes:
        kind: What the decision path must do. Never a boolean - a guardrail says
            what it requires, and the matrix composes the requirements.
        reason_code: The code carried onto `DecisionRecord.reason_codes`, e.g.
            `"POL_PHI_REVIEW"` in `MEMORY_ENGINE.md` §0. This is what makes a
            decision explicable afterwards: the record names the rule family
            that tightened it, not merely the outcome.
    """

    kind: ObligationKind
    reason_code: str
