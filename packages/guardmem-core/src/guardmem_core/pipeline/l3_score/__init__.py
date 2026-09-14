"""Layer 3 - risk and entropy scoring.  MEMORY_ENGINE.md 3

`entropy` (S5.1) and `confidence` (S5.2). The impact score and the decision
matrix are S5.3-S5.4.
"""

from guardmem_core.pipeline.l3_score.confidence import (
    V1_WEIGHTS,
    ConfidenceWeights,
    consistency,
    corroboration,
    grounding,
    score_confidence,
)
from guardmem_core.pipeline.l3_score.entropy import (
    EntailFn,
    MeaningClusters,
    cluster_meanings,
    semantic_entropy,
)

__all__ = [
    "V1_WEIGHTS",
    "ConfidenceWeights",
    "EntailFn",
    "MeaningClusters",
    "cluster_meanings",
    "consistency",
    "corroboration",
    "grounding",
    "score_confidence",
    "semantic_entropy",
]
