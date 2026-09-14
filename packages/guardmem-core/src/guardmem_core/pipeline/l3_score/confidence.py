"""The confidence composite.  BUILD_NOTEBOOK.md S5.2

`MEMORY_ENGINE.md` §3.2:

    C = w_H(1 - H_norm) + w_g S_src + w_s S_sch + w_c S_cor + w_k S_con

Five terms, each measuring a different way a claim can be wrong, and every one
of them persisted rather than only `C` - §3.2's own reason is that the review UI
renders the breakdown and `threshold_tuner.py` refits from it, and a single
scalar makes both impossible.

**Pure, and the weights come in as a value.** No I/O, no clock, no settings read
inside the function, for the reason S5.4 states about `decide()`: this is the
arithmetic a `DecisionRecord` has to be replayable against. §3.2 calls the
weights "namespace-overridable", so they are a `ConfidenceWeights` argument
rather than a module constant, and the version travels on the weights so a
report cannot claim a version its numbers did not come from.

**Note the direction of the first term.** `H_norm` is *uncertainty*, so the
composite uses `1 - H_norm`. `ConfidenceReport.semantic_entropy` stores the
uncertainty, not the contribution - storing the flipped value would make the
persisted breakdown disagree with S5.1's output under the same name.

**Where `S_con` departs from §3.2, and it is not a small point.** §3.2 defines
it as `1 - contra` from Layer 2. But `conflict.py` reports `contradiction = 0.0`
whenever a *deterministic* check answered - a CARDINALITY or TEMPORAL_OVERLAP
conflict never reaches the judge, and S4.3 writes a zero there deliberately,
because "a fabricated 0.9 would read as a measurement". Read literally, §3.2
then scores a candidate that directly clashes with a live `ONE` predicate at
`S_con = 1.0` - full marks for consistency with memory, awarded to the one case
that is definitionally inconsistent with it. That zero is an *absence of
measurement*, not a measurement of absence, and the two cannot be told apart
downstream. So the deterministic kinds score 0.0 here. See `consistency`.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from pydantic import Field, model_validator

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.verdict import ConfidenceReport, ConflictKind

if TYPE_CHECKING:
    from guardmem_core.schemas.receipt import SourceTier
    from guardmem_core.schemas.verdict import ConflictReport

__all__ = [
    "V1_WEIGHTS",
    "ConfidenceWeights",
    "consistency",
    "corroboration",
    "grounding",
    "score_confidence",
]

# §3.2's lambda for `S_cor`. One source is 0.0 and the curve is concave, so the
# second source buys more than the fourth - which is the shape corroboration
# actually has: two independent witnesses is the big step, the tenth is not.
_CORROBORATION_LAMBDA: Final = 0.8

# §3.2: "`REFINEMENT` scores 0.9 rather than penalizing". A refinement agrees
# with the incumbent and says more, so the entailment asymmetry that identifies
# it is not evidence against the claim.
_REFINEMENT_CONSISTENCY: Final = 0.9

# The conflict kinds `conflict.py` answers without a judge, so their reports
# carry `contradiction = 0.0` because nothing measured it. See the module
# docstring and `consistency`.
_UNMEASURED: Final = frozenset({ConflictKind.CARDINALITY, ConflictKind.TEMPORAL_OVERLAP})


class ConfidenceWeights(GMModel):
    """§3.2's five weights, and which set they are.

    Attributes:
        uncertainty: `w_H`, on `1 - H_norm`.
        grounding: `w_g`, on `S_src`.
        schema_fit: `w_s`, on `S_sch`.
        corroboration: `w_c`, on `S_cor`.
        consistency: `w_k`, on `S_con`.
        version: What to stamp on every report these produce. S5.2: "you will
            change these weights and need to know which decisions used which."
            On the weights rather than passed beside them, so a report cannot
            carry a version its numbers did not come from.
    """

    uncertainty: float = Field(ge=0.0, le=1.0)
    grounding: float = Field(ge=0.0, le=1.0)
    schema_fit: float = Field(ge=0.0, le=1.0)
    corroboration: float = Field(ge=0.0, le=1.0)
    consistency: float = Field(ge=0.0, le=1.0)
    version: str

    @model_validator(mode="after")
    def _weights_must_sum_to_one(self) -> ConfidenceWeights:
        """Reject a weight set that does not sum to 1.

        §3.2 writes "sum = 1" into the spec, and it is load-bearing rather than
        tidy: every term is in [0, 1], so weights summing to 1 are exactly what
        makes `C` land in [0, 1] - which `ConfidenceReport.confidence` declares
        and which S5.4's thresholds are expressed against. A set summing to 1.1
        produces a `C` above 1.0 that fails validation on some inputs and not
        others; a set summing to 0.9 quietly caps confidence below every
        threshold it is compared with, and nothing raises at all.

        Returns:
            The weights unchanged, once they sum to 1.

        Raises:
            ValueError: if they do not, within floating-point tolerance.
        """
        total = (
            self.uncertainty
            + self.grounding
            + self.schema_fit
            + self.corroboration
            + self.consistency
        )
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError(
                f"confidence weights sum to {total}, not 1.0; MEMORY_ENGINE.md "
                "3.2 requires it, and it is what keeps C inside [0, 1]"
            )
        return self


V1_WEIGHTS: Final = ConfidenceWeights(
    uncertainty=0.35,
    grounding=0.25,
    schema_fit=0.10,
    corroboration=0.15,
    consistency=0.15,
    version="v1",
)
"""§3.2's default weights. S5.2 states them again as 0.35/0.25/0.10/0.15/0.15."""


def grounding(span_entailment: float, tier: SourceTier, alignment: float) -> float:
    """`S_src`: does the cited span actually support the claim?  §3.2

    Args:
        span_entailment: `P(verbatim span entails the claim)`. Supplied rather
            than computed, because computing it is a model call and this module
            is pure - see `score_confidence` on why that matters. S5.1's
            `EntailFn` is the shape the caller binds.
        tier: The source's trust tier, which supplies the multiplier.
        alignment: `Provenance.alignment` - how closely the model quoted, 1.0 on
            an exact match.

    Returns:
        The product, in [0, 1].

    §3.2 asks for the entailment "x source-tier multiplier", with a
    "fuzzy-match penalty applied if span alignment < 1.0". The *form* of that
    penalty is not specified anywhere, and multiplying by `alignment` is the
    reading taken here: it is a no-op at 1.0, which is what "applied if < 1.0"
    means, and it is monotonic, so a worse quote never scores better. Recorded
    as a choice rather than presented as the spec's.
    """
    return span_entailment * tier.grounding_multiplier * alignment


def corroboration(sources: int) -> float:
    """`S_cor`: how many *independent* sources say this.  §3.2

    Args:
        sources: `n_independent_sources`. Not `len(provenance)` - two spans of
            one document are two citations and one source, which is the
            distinction `StoredAssertion.corroboration_count` carries and
            `dedupe.merge` maintains.

    Returns:
        `1 - exp(-0.8(n - 1))`, in [0, 1). S5.2's check: 0.0 / 0.55 / 0.80 /
        0.91 for one to four sources.

    Raises:
        ValueError: `sources` is below 1. A scored candidate has at least one
            citation - `RULES.md` non-negotiable #1 - and zero would return a
            negative contribution, dragging `C` down by an amount no other term
            can produce.
    """
    if sources < 1:
        raise ValueError(
            f"corroboration needs at least one source, got {sources}; a "
            "candidate without provenance never reaches scoring"
        )
    return 1.0 - math.exp(-_CORROBORATION_LAMBDA * (sources - 1))


def consistency(conflict: ConflictReport) -> float:
    """`S_con`: how well this sits with what is already believed.  §3.2

    Args:
        conflict: Layer 2's report.

    Returns:
        `1 - contradiction` in the ordinary case; 0.9 for a REFINEMENT; 0.0 for
        a conflict that was settled without a judge.

    §3.2 gives the first two. The third is this step's, and the module docstring
    has the argument: CARDINALITY and TEMPORAL_OVERLAP are decided by arithmetic
    over the ontology and the clock, so their reports carry `contradiction =
    0.0` because nothing was measured - and `1 - 0.0` would hand the one kind of
    candidate that definitionally clashes with live memory a *perfect*
    consistency score. Scoring them 0.0 costs at most `w_k`, which is 0.15, and
    pushes a conflicting fact toward review rather than away from it.

    A candidate with no incumbents at all also reports `contradiction = 0.0`,
    and there it is correct to read 1.0: a novel fact is consistent with
    everything, because there is nothing for it to disagree with.
    """
    if conflict.kind in _UNMEASURED:
        return 0.0
    if conflict.kind is ConflictKind.REFINEMENT:
        return _REFINEMENT_CONSISTENCY
    return 1.0 - conflict.contradiction


def score_confidence(
    *,
    entropy: float,
    span_entailment: float,
    tier: SourceTier,
    alignment: float,
    schema_fit: float,
    sources: int,
    conflict: ConflictReport,
    weights: ConfidenceWeights = V1_WEIGHTS,
) -> ConfidenceReport:
    """Compose the five terms into `C`.  §3.2

    Args:
        entropy: `H_norm` from S5.1. **Uncertainty**, not confidence - the
            composite flips it.
        span_entailment: For `S_src`; see `grounding`.
        tier: The citation's source tier.
        alignment: The citation's `alignment`.
        schema_fit: `S_sch`, straight off `GatedCandidate.schema_fit`. S4.1
            already computes §3.2's scale - 1.0 exact, 0.7 coerced, 0.4
            unknown - so this is carried, never recomputed.
        sources: `n_independent_sources`; see `corroboration`.
        conflict: Layer 2's report, for `S_con`.
        weights: Which weight set to use. §3.2 makes these namespace-overridable
            and S5.2 asks for the version on every report, so both travel
            together.

    Returns:
        The full `ConfidenceReport`: all five terms, `C`, and the weights
        version that produced it.

    Raises:
        ValueError: `sources` is below 1, or a term falls outside [0, 1] - the
            latter from `ConfidenceReport`'s own bounds, which is where a
            `span_entailment` of 1.4 is caught.

    Deterministic and side-effect free. Given the same arguments it returns the
    same report, which is invariant I4's precondition and what makes
    `scripts/replay_trace.py` possible.
    """
    source_score = grounding(span_entailment, tier, alignment)
    corroboration_score = corroboration(sources)
    consistency_score = consistency(conflict)
    composite = (
        weights.uncertainty * (1.0 - entropy)
        + weights.grounding * source_score
        + weights.schema_fit * schema_fit
        + weights.corroboration * corroboration_score
        + weights.consistency * consistency_score
    )
    return ConfidenceReport(
        semantic_entropy=entropy,
        grounding=source_score,
        schema_fit=schema_fit,
        corroboration=corroboration_score,
        consistency=consistency_score,
        # Clamped for the reason S5.1's `H_norm` is: every term is in [0, 1] and
        # the weights sum to 1, so `C` cannot exceed 1.0 in arithmetic - but it
        # can by an ulp in float64, and `confidence` is declared `le=1.0`.
        confidence=min(1.0, max(0.0, composite)),
        weights_version=weights.version,
    )
