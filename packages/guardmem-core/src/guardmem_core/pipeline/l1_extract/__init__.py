"""Layer 1 - extraction and noise reduction.  MEMORY_ENGINE.md 1

`noise_filter` (S2.1), `extractor` and `span_linker` (S2.2). The span linker
lands with the extractor rather than at S2.3, its own step, because a
`MemoryCandidate` has no valid state without a `Provenance` and a `Provenance`
has none without a span; S2.3 adds the fuzzy fallback and invariant I1's
property test.
"""

from guardmem_core.pipeline.l1_extract.extractor import (
    ExtractionBatch,
    ExtractionContext,
    extract,
)
from guardmem_core.pipeline.l1_extract.noise_filter import (
    NoiseClassification,
    NoiseVerdict,
    filter_noise,
)
from guardmem_core.pipeline.l1_extract.span_linker import link_span

__all__ = [
    "ExtractionBatch",
    "ExtractionContext",
    "NoiseClassification",
    "NoiseVerdict",
    "extract",
    "filter_noise",
    "link_span",
]
