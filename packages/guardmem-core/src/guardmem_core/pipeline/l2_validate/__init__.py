"""Layer 2 - validation and conflict detection.  MEMORY_ENGINE.md 2

`schema_gate` (S4.1). Incumbent retrieval (S4.2), the three conflict checks
(S4.3) and dedupe/merge (S4.4) arrive at their own steps.
"""

from guardmem_core.pipeline.l2_validate.schema_gate import (
    GatedCandidate,
    GateOutcome,
    SchemaGateResult,
    gate,
)

__all__ = [
    "GateOutcome",
    "GatedCandidate",
    "SchemaGateResult",
    "gate",
]
