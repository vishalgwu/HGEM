"""Layer 3 - risk and entropy scoring.  MEMORY_ENGINE.md 3

`entropy` (S5.1). The confidence composite, the impact score and the decision
matrix are S5.2-S5.4.
"""

from guardmem_core.pipeline.l3_score.entropy import (
    EntailFn,
    MeaningClusters,
    cluster_meanings,
    semantic_entropy,
)

__all__ = [
    "EntailFn",
    "MeaningClusters",
    "cluster_meanings",
    "semantic_entropy",
]
