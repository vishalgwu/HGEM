"""Layer 2 - validation and conflict detection.  MEMORY_ENGINE.md 2

`schema_gate` (S4.1) and the retrieval half of `conflict` (S4.2). The three
conflict checks join the latter at S4.3; dedupe/merge is S4.4.
"""

from guardmem_core.pipeline.l2_validate.conflict import IncumbentSet, retrieve_incumbents
from guardmem_core.pipeline.l2_validate.schema_gate import (
    GatedCandidate,
    GateOutcome,
    SchemaGateResult,
    gate,
)

__all__ = [
    "GateOutcome",
    "GatedCandidate",
    "IncumbentSet",
    "SchemaGateResult",
    "gate",
    "retrieve_incumbents",
]
