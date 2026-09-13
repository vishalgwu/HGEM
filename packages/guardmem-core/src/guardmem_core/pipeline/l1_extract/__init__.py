"""Layer 1 - extraction and noise reduction.  MEMORY_ENGINE.md 1

`noise_filter` (S2.1) is here. `extractor` (S2.2) and `span_linker` (S2.3)
arrive with their steps.
"""

from guardmem_core.pipeline.l1_extract.noise_filter import (
    NoiseClassification,
    NoiseVerdict,
    filter_noise,
)

__all__ = ["NoiseClassification", "NoiseVerdict", "filter_noise"]
