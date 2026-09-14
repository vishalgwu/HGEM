"""Layer 3 - risk and entropy scoring.  MEMORY_ENGINE.md 3

`entropy` (S5.1), `confidence` (S5.2), `impact` (S5.3) and `decision` plus
`overrides` (S5.4). The audit chain is S5.5.
"""

from guardmem_core.pipeline.l3_score.confidence import (
    V1_WEIGHTS,
    ConfidenceWeights,
    consistency,
    corroboration,
    grounding,
    score_confidence,
)
from guardmem_core.pipeline.l3_score.decision import decide
from guardmem_core.pipeline.l3_score.entropy import (
    EntailFn,
    MeaningClusters,
    cluster_meanings,
    semantic_entropy,
)
from guardmem_core.pipeline.l3_score.impact import (
    V1_BETAS,
    RiskBetas,
    RiskFeatures,
    score_impact,
)
from guardmem_core.pipeline.l3_score.impact_features import (
    Irreversibility,
    MutationType,
    PiiClass,
    Scope,
    graph_fanout,
    novelty,
    source_tier_risk,
)
from guardmem_core.pipeline.l3_score.overrides import OverrideSignals, tighten

__all__ = [
    "V1_BETAS",
    "V1_WEIGHTS",
    "ConfidenceWeights",
    "EntailFn",
    "Irreversibility",
    "MeaningClusters",
    "MutationType",
    "OverrideSignals",
    "PiiClass",
    "RiskBetas",
    "RiskFeatures",
    "Scope",
    "cluster_meanings",
    "consistency",
    "corroboration",
    "decide",
    "graph_fanout",
    "grounding",
    "novelty",
    "score_confidence",
    "score_impact",
    "semantic_entropy",
    "source_tier_risk",
    "tighten",
]
