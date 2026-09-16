"""Scaffolding for the S5.4 decision tests.

`decide()` takes three full reports, a threshold set and a signals object - five
models with thirty-odd fields between them, of which any one test varies two.
So the builders here default everything to the *most permissive* shape there is:
a perfectly confident, zero-risk, unconflicted candidate from a trusted source
with no obligations and nothing degraded.

That direction is deliberate. Every test then reads as "start from the case that
auto-writes, change one thing, and see it get stricter" - and a builder that
forgot to wire a field through would show up as an override that never fires,
which is the failure these tests exist to catch. Defaulting to a strict baseline
would hide exactly that.
"""

from __future__ import annotations

from typing import Final

from guardmem_core.pipeline.l3_score import MutationType, OverrideSignals
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.risk import ImpactLevel
from guardmem_core.schemas.verdict import (
    ConfidenceReport,
    ConflictKind,
    ConflictReport,
    RiskVerdict,
    Thresholds,
)

__all__ = [
    "DEFAULTS",
    "PERMISSIVE",
    "confidence",
    "conflict",
    "risk",
    "signals",
]

# `MEMORY_ENGINE.md` §3.4's stated defaults, which are also `Settings`'.
DEFAULTS: Final = Thresholds(
    tau_lo=0.45,
    tau_mid=0.60,
    tau_hi=0.78,
    rho_lo=0.35,
    rho_hi=0.70,
    version="test-v1",
)


def confidence(value: float = 0.95, *, corroboration: float = 0.0) -> ConfidenceReport:
    """A report carrying just what the matrix reads.

    Args:
        value: `C`.
        corroboration: `S_cor`. Defaults to 0.0, which is exactly one source -
            so the starred cell is *not* satisfied unless a test says so, and a
            test that means "corroborated" has to say it.
    """
    return ConfidenceReport(
        semantic_entropy=0.0,
        grounding=1.0,
        schema_fit=1.0,
        corroboration=corroboration,
        consistency=1.0,
        confidence=value,
        weights_version="test",
    )


def risk(
    value: float = 0.10,
    *,
    impact: ImpactLevel = ImpactLevel.LOW,
    obligations: list[str] | None = None,
) -> RiskVerdict:
    """A verdict carrying `R`, the impact level and any obligations."""
    return RiskVerdict(
        impact_level=impact,
        risk=value,
        features={},
        obligations=obligations or [],
    )


def conflict(
    hint: str = "coexist",
    kind: ConflictKind = ConflictKind.NONE,
    *,
    incumbent_assertion_id: str | None = None,
) -> ConflictReport:
    """A Layer 2 report.

    Args:
        hint: §2.3's action. The only field the decision matrix reads.
        kind: The classification.
        incumbent_assertion_id: Which live assertion this conflicts with.
            `None` for the common case - most facts are novel - and set by
            ADR-0010's applier tests, which need a real id to supersede.

    Returns:
        The report.
    """
    return ConflictReport.model_validate(
        {
            "kind": kind,
            "incumbent_assertion_id": incumbent_assertion_id,
            "entailment": 0.0,
            "contradiction": 0.0,
            "cosine": 0.0,
            "resolution_hint": hint,
        }
    )


def signals(**overrides: object) -> OverrideSignals:
    """The signals object, benign unless a test says otherwise."""
    base: dict[str, object] = {
        "injection_detected": False,
        "source_tier": SourceTier.TRUSTED_SYSTEM,
        "mutation": MutationType.COEXIST,
        "requires_corroboration": False,
        "budget_exhausted": False,
        "circuit_open": False,
        "policy_version": "test-policy",
    }
    return OverrideSignals.model_validate(base | overrides)


# The four arguments that make `decide()` return AUTO_WRITE, so a test can
# change exactly one of them.
PERMISSIVE: Final = (confidence(), risk(), conflict(), DEFAULTS)
