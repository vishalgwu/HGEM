"""Blast radius.  BUILD_NOTEBOOK.md S5.3

`MEMORY_ENGINE.md` §3.3:

    z     = sum(beta_f * x_f)
    R_raw = sigmoid(z)
    R     = max(R_raw, floor[impact_level])

**Risk is not the inverse of confidence, and the floor is what enforces that.**
§3.3 opens by saying so: "a perfectly-confident write to the account-owner field
is still a high-risk operation". `C` asks whether the claim is true; `R` asks
what breaks if it is not. They are separate axes because a system that collapsed
them would auto-write anything it was sure about, which is exactly the failure
this project exists to stop. The floor is the mechanism - no combination of the
eight features can pull a CRITICAL predicate below 0.80, so the feature weights
are free to be wrong without the safety property being wrong.

**The features are values, not lookups.** Nothing here reads a store, a clock or
a setting: `score_impact` takes a `RiskFeatures` and returns a `RiskVerdict`,
the same shape S5.2 has and for the same reason - S5.4's `decide()` has to be
replayable, and it composes this.

**Where the betas live, and what is missing.** §3.3 gives eight betas and a
bias, and says `threshold_tuner.py` will "refit beta from reviewer labels
(logistic regression on approve/reject, refit weekly, gated by a kappa check
before promotion)". Weekly refitting means a stored `R` is only reproducible
against the coefficients that produced it - so `RiskBetas` carries a `version`
the way `ConfidenceWeights` does. **`RiskVerdict` has nowhere to put it.**
`MEMORY_ENGINE.md` §0 gives it `impact_level`, `risk`, `features` and
`obligations`, and `DecisionRecord` carries `thresholds_version` and
`policy_version` but nothing for beta. After the first refit,
`scripts/replay_trace.py` would recompute a different `R` and print a diff it
cannot explain. Flagged rather than fixed here: adding a field to a spec-of-
record model is an ADR (`RULES.md` §8), and S5.5's audit chain and S5.6's replay
are the steps that will actually feel it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from pydantic import Field

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.verdict import RiskVerdict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.schemas.risk import ImpactLevel

__all__ = ["V1_BETAS", "RiskBetas", "RiskFeatures", "score_impact"]


class RiskFeatures(GMModel):
    """§3.3's eight features, each already normalised to [0, 1].

    Attributes:
        impact_declared: The ontology's impact level, as `ImpactLevel
            .risk_feature`.
        mutation_type: What this write would do to memory.
        scope: How widely shared the namespace is.
        graph_fanout: How much depends on the subject.
        pii_class: What kind of personal data the claim carries.
        irreversibility: Whether what an agent did on this belief can be undone.
        source_tier_risk: `1 - trust multiplier`.
        novelty: `1 - max cosine to existing memory`.

    A model rather than the `dict[str, float]` `RiskVerdict` persists, because
    the dict is a *wire* format and this is the thing being computed. Two
    properties come with it. A misnamed key cannot silently score zero - which
    matters twice over, since the same names are what
    `scripts/threshold_tuner.py` refits betas against, so a typo would drop a
    feature from the model *and* from the refit with nothing raising. And a
    feature outside [0, 1] is refused here rather than pushed through a sigmoid
    that will happily squash it into a plausible-looking number.

    `model_dump()` is what lands on `RiskVerdict.features`, so the persisted
    keys are these field names, and they stay the vocabulary the tuner reads.
    """

    impact_declared: float = Field(ge=0.0, le=1.0)
    mutation_type: float = Field(ge=0.0, le=1.0)
    scope: float = Field(ge=0.0, le=1.0)
    graph_fanout: float = Field(ge=0.0, le=1.0)
    pii_class: float = Field(ge=0.0, le=1.0)
    irreversibility: float = Field(ge=0.0, le=1.0)
    source_tier_risk: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)


class RiskBetas(GMModel):
    """§3.3's coefficients, the bias, and which fit they came from.

    Attributes:
        impact_declared: 2.20 in v1, and the largest - the ontology's own
            declaration outweighs everything the pipeline infers.
        mutation_type: 1.60.
        scope: 1.30.
        graph_fanout: 0.90.
        pii_class: 1.10.
        irreversibility: 1.40.
        source_tier_risk: 1.00.
        novelty: 0.50, the smallest - an unprecedented claim is a weak signal on
            its own, because most true facts are novel the first time.
        bias: -3.40. Negative, so a candidate with every feature at zero scores
            `sigmoid(-3.4)`, about 0.03, rather than the 0.5 a zero bias would
            give. Without it the neutral case would sit at the midpoint of the
            scale and every threshold would have to be expressed around it.
        version: Which fit these are. See the module docstring on the fact that
            `RiskVerdict` currently has nowhere to record it.

    Unbounded on purpose, unlike `ConfidenceWeights`: these are logistic
    regression coefficients, not a convex combination, so they do not sum to
    anything in particular and a refit may legitimately return a negative one.
    """

    impact_declared: float
    mutation_type: float
    scope: float
    graph_fanout: float
    pii_class: float
    irreversibility: float
    source_tier_risk: float
    novelty: float
    bias: float
    version: str


V1_BETAS: Final = RiskBetas(
    impact_declared=2.20,
    mutation_type=1.60,
    scope=1.30,
    graph_fanout=0.90,
    pii_class=1.10,
    irreversibility=1.40,
    source_tier_risk=1.00,
    novelty=0.50,
    bias=-3.40,
    version="v1",
)
"""§3.3's coefficients as written. The eight sum to exactly 10.0."""


def score_impact(
    features: RiskFeatures,
    impact_level: ImpactLevel,
    *,
    obligations: Sequence[str] = (),
    betas: RiskBetas = V1_BETAS,
) -> RiskVerdict:
    """Score the blast radius of writing this candidate.  §3.3

    Args:
        features: The eight, already normalised. `impact_features.py` is where
            domain objects become these.
        impact_level: The predicate's declared impact, which supplies the floor.
            Passed separately from `features.impact_declared` because the two
            are different uses of the same fact - one is weighed against seven
            other things, the other cannot be weighed against anything.
        obligations: Guardrail obligations to attach. Validated against
            `ObligationKind` by `RiskVerdict` itself, which refuses an unknown
            string rather than letting S5.4 ignore it silently.
        betas: Which coefficients to use. §3.3 has `threshold_tuner.py` refit
            these weekly, so they are an argument and carry their own version.

    Returns:
        The `RiskVerdict`: the level, `R`, the features persisted verbatim, and
        the obligations.

    Raises:
        ValueError: an obligation is not an `ObligationKind` value.

    Pure and deterministic - the same features and betas give the same verdict,
    which is what invariant I4 needs from everything `decide()` composes.

    The features are persisted exactly as they came in, because §3.3 asks for
    that twice over: "the review UI can show *why* something was flagged", and
    the tuner refits from them. A verdict that stored only `R` would make the
    first impossible and the second wrong.
    """
    weighted = (
        betas.impact_declared * features.impact_declared
        + betas.mutation_type * features.mutation_type
        + betas.scope * features.scope
        + betas.graph_fanout * features.graph_fanout
        + betas.pii_class * features.pii_class
        + betas.irreversibility * features.irreversibility
        + betas.source_tier_risk * features.source_tier_risk
        + betas.novelty * features.novelty
        + betas.bias
    )
    return RiskVerdict(
        impact_level=impact_level,
        risk=max(_sigmoid(weighted), impact_level.risk_floor),
        features=features.model_dump(),
        obligations=list(obligations),
    )


def _sigmoid(z: float) -> float:
    """`1 / (1 + exp(-z))`, without overflowing on a large negative `z`.

    Args:
        z: The linear score.

    Returns:
        The squashed value, in (0, 1).

    Written in two branches because the naive form raises `OverflowError` once
    `-z` exceeds about 710 - `math.exp(710)` is past the float64 maximum. A
    refit beta of the wrong sign on a feature at 1.0 is all it would take, and
    the failure would be an exception out of a pure scoring function rather than
    a number. For negative `z` the algebraically identical `e^z / (1 + e^z)`
    has an exponent that cannot overflow.
    """
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exponentiated = math.exp(z)
    return exponentiated / (1.0 + exponentiated)
