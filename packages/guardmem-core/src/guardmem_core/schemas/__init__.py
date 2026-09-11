"""The schema layer - every domain object crossing a public boundary.

`RULES.md` §2.1: "Public boundaries take and return Pydantic models - never
loose `dict[str, Any]`." These are those models.

This module re-exports the whole layer so that downstream packages have one
import site (`from guardmem_core.schemas import MemoryCandidate`) and so that
`__all__` below is a readable inventory of the layer's surface. Sibling modules
import from each other *directly*, never through this file, which keeps the
dependency order explicit and leaves no room for an import cycle:

    base -> policy -> verdict -> receipt -> candidate -> entity -> review

Which module owns what follows `PROJECT_TREE.md`. Three things it lists are
deliberately absent, each deferred to the step that pins its shape rather than
guessed at now: `Rule` and `PolicyPack` (S12.2, the policy engine), `Predicate`
(S3.5, where the ontology loader validates it), and `Thresholds` (S5.4, where
`decide()` takes it). `MemoryProposal` likewise arrives with the gateway.
"""

from __future__ import annotations

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.candidate import ExtractionResult, MemoryCandidate
from guardmem_core.schemas.entity import Cardinality, Edge, Entity, StoredAssertion
from guardmem_core.schemas.policy import Obligation, ObligationKind
from guardmem_core.schemas.receipt import (
    AuditEvent,
    Provenance,
    SourceTier,
    WriteReceipt,
)
from guardmem_core.schemas.review import (
    Diff,
    ReviewAction,
    ReviewDecision,
    ReviewStatus,
    ReviewTask,
)
from guardmem_core.schemas.verdict import (
    ConfidenceReport,
    ConflictKind,
    ConflictReport,
    Decision,
    DecisionRecord,
    ImpactLevel,
    RiskVerdict,
)

__all__ = [
    "AuditEvent",
    "Cardinality",
    "ConfidenceReport",
    "ConflictKind",
    "ConflictReport",
    "Decision",
    "DecisionRecord",
    "Diff",
    "Edge",
    "Entity",
    "ExtractionResult",
    "GMModel",
    "ImpactLevel",
    "MemoryCandidate",
    "ObjectValue",
    "Obligation",
    "ObligationKind",
    "Provenance",
    "ReviewAction",
    "ReviewDecision",
    "ReviewStatus",
    "ReviewTask",
    "RiskVerdict",
    "SourceTier",
    "StoredAssertion",
    "WriteReceipt",
]
