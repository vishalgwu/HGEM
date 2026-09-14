"""Layer 2 - validation and conflict detection.  MEMORY_ENGINE.md 2

`schema_gate` (S4.1), `incumbents` (S4.2), and `conflict` plus `nli` (S4.3).
Dedupe/merge and §2.3's resolution table are S4.4.
"""

from guardmem_core.pipeline.l2_validate.conflict import detect
from guardmem_core.pipeline.l2_validate.incumbents import IncumbentSet, retrieve_incumbents
from guardmem_core.pipeline.l2_validate.nli import (
    AdjudicationBatch,
    Judgement,
    LLMJudge,
    NLIJudge,
)
from guardmem_core.pipeline.l2_validate.schema_gate import (
    GatedCandidate,
    GateOutcome,
    SchemaGateResult,
    gate,
)

__all__ = [
    "AdjudicationBatch",
    "GateOutcome",
    "GatedCandidate",
    "IncumbentSet",
    "Judgement",
    "LLMJudge",
    "NLIJudge",
    "SchemaGateResult",
    "detect",
    "gate",
    "retrieve_incumbents",
]
