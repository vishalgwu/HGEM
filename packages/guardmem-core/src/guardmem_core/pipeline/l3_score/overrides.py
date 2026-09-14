"""The seven hard overrides, and the rule that they never relax.  S5.4

`MEMORY_ENGINE.md` §3.4 lists them under "Hard overrides applied **after** the
matrix (obligations compose, they never relax)". Split from `decision.py`
because §3.4 splits them: the matrix is a lookup over two scores, and these are
seven unrelated safety rules that each know about something else entirely - a
canary, a source tier, a circuit breaker, a policy pack.

**"Never relax" is implemented as an ordering, not as a convention.** Each
override proposes a decision and `tighten` keeps whichever of the two is less
permissive. That makes the property mechanical: no override can turn a
HITL_REVIEW into an AUTO_WRITE however it is written, and the order they run in
cannot change the answer. Writing them as a chain of `if/elif` returning
directly would have made the guarantee depend on their order and on every future
edit preserving it.

The permissiveness order is how easily a candidate reaches memory:

    AUTO_WRITE  <  ESCALATE  <  HITL_REVIEW  <  REJECT

ESCALATE above AUTO_WRITE because it withholds the write pending a FRONTIER
re-score; HITL_REVIEW above ESCALATE because a human is a stricter gate than a
bigger model; REJECT at the top because nothing enters memory at all. That
ordering has a consequence worth stating: override 6's `require_review` cannot
pull a REJECT up into a review. An obligation tightens, and a rejection is
already tighter.

**Three of the seven need inputs `decide()`'s declared signature cannot
supply**, which is why `OverrideSignals` exists - see its docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core.pipeline.l3_score.impact_features import MutationType
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.policy import ObligationKind
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.verdict import Decision, ImpactLevel

if TYPE_CHECKING:
    from guardmem_core.schemas.verdict import ConfidenceReport, ConflictReport, RiskVerdict

__all__ = [
    "OverrideSignals",
    "apply_overrides",
    "tighten",
]

# See the module docstring. Index is permissiveness: later is stricter.
_PERMISSIVENESS: Final = (
    Decision.AUTO_WRITE,
    Decision.ESCALATE,
    Decision.HITL_REVIEW,
    Decision.REJECT,
)

# §3.4's override 2 fires at HIGH *or above*, and `ImpactLevel` is a `StrEnum` -
# comparing its members with `>=` compares their strings, which orders them
# "critical" < "high" < "low" < "medium" and is wrong without raising. The same
# trap `SourceTier.at_least` exists for.
_HIGH_IMPACT: Final = frozenset({ImpactLevel.HIGH, ImpactLevel.CRITICAL})

# Reason codes, in `MEMORY_ENGINE.md` §0's style (`"POL_PHI_REVIEW"`). `PRD.md`
# FR-3.4 requires machine-readable rationale in addition to free text, and these
# are what makes a decision explicable: the record names the rule that tightened
# it, not merely the outcome.
OVR_INJECTION: Final = "OVR_INJECTION_DETECTED"
OVR_WEB_HIGH_IMPACT: Final = "OVR_WEB_SOURCE_HIGH_IMPACT"
OVR_CORROBORATION: Final = "OVR_CORROBORATION_REQUIRED"
OVR_CONFLICT_ESCALATE: Final = "OVR_CONFLICT_HINT_ESCALATE"
OVR_CAPACITY: Final = "OVR_BUDGET_OR_CIRCUIT"
OVR_POLICY_REVIEW: Final = "OVR_POLICY_REQUIRE_REVIEW"
OVR_CRITICAL_RETRACT: Final = "OVR_CRITICAL_RETRACTION_STEP_UP"


class OverrideSignals(GMModel):
    """What the seven overrides need and the three reports cannot say.

    Attributes:
        injection_detected: `RULES.md` §3's confirmed injection - a canary echo,
            not a heuristic.
        source_tier: The citation's tier. Recoverable in principle from
            `RiskVerdict.features["source_tier_risk"]`, since the five
            multipliers are distinct - but that means float-comparing an enum
            back out of a wire format, and `features` is a `dict[str, float]`
            a caller could build without the key.
        mutation: What the write would do to memory. Same reasoning: it is in
            `features` as a number, and the number is not the vocabulary.
        requires_corroboration: The predicate's ontology flag. Nothing else in
            the signature carries the ontology.
        budget_exhausted: The tenant's cost cap is reached.
        circuit_open: A provider's circuit breaker is open.
        policy_version: Which policy pack produced `RiskVerdict.obligations`.
            `DecisionRecord` declares the field and no report carries it.

    **This parameter is a deviation from §3.4's declared signature, and the
    alternative was worse.** §3.4 writes
    `decide(conf, risk, conflict, thresholds, already_escalated)`, and overrides
    1, 2, 3 and 5 cannot be evaluated from those five: nothing there knows about
    a canary, a source tier, an ontology flag or a circuit breaker. Dropping
    four of the seven would have left a safety surface silently missing, and
    adding fields to `ConfidenceReport` or `RiskVerdict` is a change to a
    spec-of-record model (`RULES.md` §8). One value object, all of it data, so
    `decide()` stays pure and total.

    Deliberately has no defaults. A benign default on any of these is an
    override that quietly does not fire, which is the failure mode
    `ARCHITECTURE.md` §0 calls "fail quiet" - and the one that matters most here,
    because a caller who forgets `injection_detected` gets the *permissive*
    answer.
    """

    injection_detected: bool
    source_tier: SourceTier
    mutation: MutationType
    requires_corroboration: bool
    budget_exhausted: bool
    circuit_open: bool
    policy_version: str


def tighten(current: Decision, proposed: Decision) -> Decision:
    """Whichever of the two is less permissive.

    Args:
        current: What the decision is so far.
        proposed: What an override asks for.

    Returns:
        The stricter of the two, by the ordering in the module docstring.

    This is the whole of "obligations compose, they never relax", and having it
    as a function is what makes that a property of the code rather than of the
    care taken writing each override.
    """
    return max(current, proposed, key=_PERMISSIVENESS.index)


def apply_overrides(
    decision: Decision,
    *,
    conf: ConfidenceReport,
    risk: RiskVerdict,
    conflict: ConflictReport,
    signals: OverrideSignals,
    already_escalated: bool,
    corroborated: bool,
) -> tuple[Decision, list[str]]:
    """Run §3.4's seven overrides over a matrix result.

    Args:
        decision: What the matrix returned.
        conf: The confidence report. Unused by the seven and taken anyway, so
            this stays callable as the pipeline's second half and a future rule
            has the report to hand.
        risk: For `impact_level` and `obligations`.
        conflict: For `resolution_hint`.
        signals: The four rules' worth of context the reports cannot carry.
        already_escalated: Whether this candidate has been round the FRONTIER
            once. §3.4: an escalated result "cannot escalate again".
        corroborated: Whether at least two independent sources back the claim -
            computed by `decision.py` from `conf.corroboration`, so the rule for
            reading it lives in one place.

    Returns:
        The tightened decision and the reason codes that fired, in §3.4's order.

    The loop is the whole implementation: every rule proposes through `tighten`,
    so no rule can relax the matrix's answer and the order below is
    presentational rather than load-bearing.
    """
    del conf  # See the docstring: taken for the signature, not read.
    codes: list[str] = []
    for code, fires, proposed in _rules(
        risk=risk,
        conflict=conflict,
        signals=signals,
        already_escalated=already_escalated,
        corroborated=corroborated,
    ):
        if fires:
            decision = tighten(decision, proposed)
            codes.append(code)
    return decision, codes


def _rules(
    *,
    risk: RiskVerdict,
    conflict: ConflictReport,
    signals: OverrideSignals,
    already_escalated: bool,
    corroborated: bool,
) -> list[tuple[str, bool, Decision]]:
    """§3.4's seven, in its numbering, as `(code, fires, proposed)`.

    Returns:
        One entry per rule, whether or not it fires - so the list is the
        vocabulary and `apply_overrides` needs to know nothing about any of
        them.

    The four that need explaining:

    1. §3.4 writes "-> QUARANTINE (never AUTO_WRITE)" and `Decision` has no
       QUARANTINE member: `MEMORY_ENGINE.md` §0 declares four outcomes and
       `PRD.md` FR-3.2 says exactly one per candidate. So the quarantine is a
       *namespace* (§2.1) and the decision is REJECT - `RULES.md` §3 treats a
       canary echo as confirmed injection rather than a heuristic, and
       confirmed-hostile content does not need a human to adjudicate it. A gap
       worth naming: the spec names an outcome its own enum does not have.
    2. Fires at HIGH *or above*, which is why the membership test uses a set.
       `ImpactLevel` is a `StrEnum`, so `>=` would compare strings and order
       them "critical" < "high" < "low" < "medium" - wrong, and silent. The
       same trap `SourceTier.at_least` exists for.
    6. `RiskVerdict` has already validated its obligations against
       `ObligationKind`, so an unknown string raised there rather than being
       quietly ignored here.
    7. §3.4 writes "mutation == delete"; nothing deletes, and `RETRACT` is the
       vocabulary's name for withdrawing a belief. "+ step-up auth" rides on the
       reason code - `DecisionRecord` has nowhere else for it, and inventing a
       fifth `Decision` would be a spec change.
    """
    return [
        (OVR_INJECTION, signals.injection_detected, Decision.REJECT),
        (
            OVR_WEB_HIGH_IMPACT,
            signals.source_tier is SourceTier.RETRIEVED_WEB and risk.impact_level in _HIGH_IMPACT,
            Decision.HITL_REVIEW,
        ),
        (
            OVR_CORROBORATION,
            signals.requires_corroboration and not corroborated,
            Decision.HITL_REVIEW,
        ),
        (
            OVR_CONFLICT_ESCALATE,
            conflict.resolution_hint == "escalate",
            Decision.HITL_REVIEW if already_escalated else Decision.ESCALATE,
        ),
        (
            OVR_CAPACITY,
            signals.budget_exhausted or signals.circuit_open,
            Decision.HITL_REVIEW,
        ),
        (
            OVR_POLICY_REVIEW,
            ObligationKind.REQUIRE_REVIEW.value in risk.obligations,
            Decision.HITL_REVIEW,
        ),
        (
            OVR_CRITICAL_RETRACT,
            risk.impact_level is ImpactLevel.CRITICAL and signals.mutation is MutationType.RETRACT,
            Decision.HITL_REVIEW,
        ),
    ]
