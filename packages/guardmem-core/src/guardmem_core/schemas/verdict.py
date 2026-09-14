"""Conflict, confidence, risk and the decision.  BUILD_NOTEBOOK.md S1.6

Copied from `MEMORY_ENGINE.md` §0, which is the spec of record: code that
disagrees with it is wrong until an ADR moves it (`RULES.md` §8). The scoring
that fills these in is Day 5 (S5.1-S5.4); this module is only their shape.

`PROJECT_TREE.md` lists `RiskVerdict`, `ConfidenceReport` and `Decision` here.
`ConflictKind` and `ConflictReport` are here too, because they are the third
verdict about a candidate and `DecisionRecord` composes all three - splitting
them out would put half of one record in another module for no gain.

`Thresholds` **is** here, and the docstring said it would not be. S5.4 was to
"settle its shape", and settling it turned up the reason it belongs in a schema
module rather than in `l3_score/decision.py`: `Settings` already enforces
`tau_lo < tau_mid < tau_hi` and `rho_lo < rho_hi`, so a second copy of that rule
beside `decide()` would be two homes for one invariant. `Settings.thresholds()`
builds one of these and its validator is the only enforcement; `decision.py`
imports the value and never reads settings. `DecisionRecord` still carries only
the `thresholds_version` string, as `MEMORY_ENGINE.md` §0 declares.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.policy import ObligationKind
from guardmem_core.types import AssertionId

__all__ = [
    "ConfidenceReport",
    "ConflictKind",
    "ConflictReport",
    "Decision",
    "DecisionRecord",
    "ImpactLevel",
    "RiskVerdict",
    "Thresholds",
]


class ImpactLevel(StrEnum):
    """Declared blast radius of a predicate, from the tenant ontology.

    Sets the floor under `RiskVerdict.risk` (`MEMORY_ENGINE.md` §3.3: low .15,
    medium .35, high .60, critical .80), which is what keeps a confident write
    to a critical field out of the auto-write path.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def risk_floor(self) -> float:
        """The floor this impact level puts under `RiskVerdict.risk`.

        Returns:
            `MEMORY_ENGINE.md` §3.3's value: low .15, medium .35, high .60,
            critical .80.

        §3.3 computes `R = max(R_raw, floor[impact])`, so these four numbers are
        what stop a confidently-scored write to a critical field reaching the
        auto-write path. They were prose in this docstring and a dict literal in
        a seed script until the S3.6 audit - the same four numbers written twice,
        one of which nothing checked. `l3_score/impact.py` is the consumer that
        makes this a property rather than a lookup table somebody else owns.
        """
        return _RISK_FLOORS[self]

    @property
    def risk_feature(self) -> float:
        """This level as §3.3's `impact_declared` feature.

        Returns:
            `MEMORY_ENGINE.md` §3.3's mapping: low 0, medium .33, high .66,
            critical 1.

        **Not the same numbers as `risk_floor`, and the difference is the
        point.** This one is an *input* to the linear score, weighted at
        `beta = 2.20` and traded off against seven other features; the floor is
        applied afterwards and cannot be traded off against anything. A
        critical-impact write is therefore expensive twice over - once because
        it pushes `z` up, and once because `R` can never land below .80
        whatever the other features say.
        """
        return _RISK_FEATURES[self]


# Defined after the class because a `StrEnum` body cannot hold a non-member
# mapping keyed by its own members. Private: `ImpactLevel.risk_floor` is the
# interface, so a caller cannot reach for a floor by string and miss the enum.
_RISK_FLOORS: dict[ImpactLevel, float] = {
    ImpactLevel.LOW: 0.15,
    ImpactLevel.MEDIUM: 0.35,
    ImpactLevel.HIGH: 0.60,
    ImpactLevel.CRITICAL: 0.80,
}

# §3.3's `impact_declared` feature scale. Evenly spaced where the floors are
# not, because this one enters a weighted sum and the floors are a safety net.
_RISK_FEATURES: dict[ImpactLevel, float] = {
    ImpactLevel.LOW: 0.0,
    ImpactLevel.MEDIUM: 0.33,
    ImpactLevel.HIGH: 0.66,
    ImpactLevel.CRITICAL: 1.0,
}


class Thresholds(GMModel):
    """The five cut points §3.4's matrix reads, and which set they are.

    Attributes:
        tau_lo: Below this, `C` is too low to act on - the REJECT band.
        tau_mid: Separates the escalate band from the auto-write-at-low-risk
            band. §3.4 is explicit about why it exists: "the matrix has **four**
            confidence bands, so it needs **three** confidence thresholds
            [...] without it the 0.60 boundary below is a magic number and
            `decide()` cannot be configured from settings."
        tau_hi: At or above this, `C` is high enough to auto-write at low risk.
        rho_lo: At or above this, `R` leaves the low-risk column.
        rho_hi: At or above this, `R` is high enough that a human always looks.
        version: Which set these are. `PRD.md` FR-3.3 makes a threshold change
            an audited event, and this is the string `DecisionRecord` carries.

    **Bands are half-open**: a lower bound is inclusive and an upper bound
    exclusive, so a value exactly on a threshold falls in the *higher* band.
    §3.4 says so for `C` and the same reading is applied to `R` - see
    `l3_score/decision.py`, which records where §3.4's own table disagrees with
    itself about that.
    """

    tau_lo: float = Field(ge=0.0, le=1.0)
    tau_mid: float = Field(ge=0.0, le=1.0)
    tau_hi: float = Field(ge=0.0, le=1.0)
    rho_lo: float = Field(ge=0.0, le=1.0)
    rho_hi: float = Field(ge=0.0, le=1.0)
    version: str

    @model_validator(mode="after")
    def _bands_must_be_ordered(self) -> Thresholds:
        """Reject a threshold set the matrix cannot use.

        The rule `Settings` used to state for itself, moved here so it has one
        home - `Settings.thresholds()` constructs one of these, so a bad `.env`
        still fails at startup rather than at the first decision.

        Out-of-order thresholds raise nowhere later: they silently produce a
        matrix with an empty band, so a whole class of candidate becomes
        unreachable and nothing looks wrong.

        Returns:
            The thresholds unchanged, once the bands are ordered.

        Raises:
            ValueError: if not `tau_lo < tau_mid < tau_hi`, or not
                `rho_lo < rho_hi`.
        """
        if not self.tau_lo < self.tau_mid < self.tau_hi:
            raise ValueError(
                "confidence thresholds must satisfy tau_lo < tau_mid < tau_hi, got "
                f"tau_lo={self.tau_lo}, tau_mid={self.tau_mid}, tau_hi={self.tau_hi}"
            )
        if not self.rho_lo < self.rho_hi:
            raise ValueError(
                "risk thresholds must satisfy rho_lo < rho_hi, got "
                f"rho_lo={self.rho_lo}, rho_hi={self.rho_hi}"
            )
        return self


class ConflictKind(StrEnum):
    """What Layer 2 found when it compared a candidate with its incumbents."""

    NONE = "none"
    DUPLICATE = "duplicate"  # merge, no new row
    REFINEMENT = "refinement"  # strictly more specific than the incumbent
    CONTRADICTION = "contradiction"  # NLI says mutually exclusive
    CARDINALITY = "cardinality"  # ONE-predicate already has a live value
    TEMPORAL_OVERLAP = "temporal_overlap"


class Decision(StrEnum):
    """The four outcomes. `PRD.md` FR-3.2: exactly one per candidate."""

    AUTO_WRITE = "auto_write"
    HITL_REVIEW = "hitl_review"
    REJECT = "reject"
    ESCALATE = "escalate"


class ConflictReport(GMModel):
    """Layer 2's verdict on a candidate against live memory.

    Attributes:
        kind: The classification from the resolution matrix,
            `MEMORY_ENGINE.md` §2.3.
        incumbent_assertion_id: The assertion this conflicts with, where there
            is one. Required rather than defaulted, as in the spec - a report
            that forgot to say which incumbent it compared against is not a
            usable audit record.
        entailment: `P(incumbent ⊨ candidate)`, a probability.
        contradiction: `P(mutually exclusive)`, the maximum over both directions.
        cosine: Similarity to the incumbent. Bounded at -1, not 0: cosine over
            unnormalised embeddings is genuinely negative sometimes, and
            clamping it here would hide that from `S_con` and from the tuner.
        resolution_hint: What §2.3 says to do about it. A `Literal` rather than a
            `StrEnum` because that is what the spec of record declares.
    """

    kind: ConflictKind
    incumbent_assertion_id: AssertionId | None
    entailment: float = Field(ge=0.0, le=1.0)
    contradiction: float = Field(ge=0.0, le=1.0)
    cosine: float = Field(ge=-1.0, le=1.0)
    resolution_hint: Literal["merge", "supersede", "coexist", "escalate"]


class ConfidenceReport(GMModel):
    """The five terms of the confidence composite and their result.

    `MEMORY_ENGINE.md` §3.2: ``C = w_H·(1 - H_norm) + w_g·S_src + w_s·S_sch +
    w_c·S_cor + w_k·S_con``. Every term is persisted, not only `C`, because the
    review UI renders the breakdown and `threshold_tuner.py` refits from it - a
    single scalar would make both impossible.

    Attributes:
        semantic_entropy: `H_norm`, normalised over meaning clusters (§3.1).
            Note the direction: this is *uncertainty*, so the composite uses
            ``1 - H_norm``. A high value here lowers confidence.
        grounding: `S_src`, entailment by the candidate's own verbatim span
            times a source-tier multiplier.
        schema_fit: `S_sch`, 1.0 exact ontology fit down to 0.4 unknown.
        corroboration: `S_cor`, from the number of independent sources.
        consistency: `S_con`, agreement with live memory.
        confidence: `C`, the weighted composite.
        weights_version: Which weight set produced `C`. S5.2: "you will change
            these weights and need to know which decisions used which."
    """

    semantic_entropy: float = Field(ge=0.0, le=1.0)
    grounding: float = Field(ge=0.0, le=1.0)
    schema_fit: float = Field(ge=0.0, le=1.0)
    corroboration: float = Field(ge=0.0, le=1.0)
    consistency: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    weights_version: str


class RiskVerdict(GMModel):
    """Blast radius. Deliberately a separate axis from confidence.

    `MEMORY_ENGINE.md` §3.3: "Risk is *not* the inverse of confidence. A
    perfectly-confident write to the account-owner field is still a high-risk
    operation."

    Attributes:
        impact_level: Declared impact from the ontology, which floors `risk`.
        risk: `R`, the squashed linear score after the impact floor is applied.
        features: The named contributions behind `R`, persisted verbatim so the
            review UI can show *why* something was flagged and so
            `threshold_tuner.py` can refit the coefficients from reviewer labels
            (§3.3).
        obligations: Guardrail obligations attached to this candidate. Typed as
            `list[str]` because that is what the spec of record declares, and
            validated against `ObligationKind` so the strings are a vocabulary
            rather than free text. If these should become `list[Obligation]`,
            that is an ADR against `MEMORY_ENGINE.md` §0, not a quiet edit here.
    """

    impact_level: ImpactLevel
    risk: float = Field(ge=0.0, le=1.0)
    features: dict[str, float]
    obligations: list[str]

    @field_validator("obligations")
    @classmethod
    def _obligations_must_be_known(cls, value: list[str]) -> list[str]:
        """Reject an obligation the decision matrix would not recognise.

        An unknown string here is silently inert: S5.4 composes the obligations
        it knows about and ignores the rest, so a typo'd `"require_reviewi"`
        would leave the auto-write path open with nothing in the record to show
        for it. That is precisely the failure `ARCHITECTURE.md` §0 calls "fail
        quiet", and the stance there is that degradation must never widen the
        auto-write path.

        Returns:
            The obligations unchanged, once every one is recognised.

        Raises:
            ValueError: if any entry is not an `ObligationKind` value.
        """
        known = {kind.value for kind in ObligationKind}
        unknown = [item for item in value if item not in known]
        if unknown:
            raise ValueError(
                f"unknown obligations {unknown}; expected values of ObligationKind "
                f"{sorted(known)}. Adding one is a change to a safety surface - "
                "declare it in ObligationKind first."
            )
        return value


class DecisionRecord(GMModel):
    """The full, replayable record of one decision.

    This is the object `RULES.md` §1.6 calls pure and deterministic: given the
    same reports, thresholds and policy version, `decide()` must produce the
    same record (invariant I4). It carries every input to that function, which
    is what makes `scripts/replay_trace.py` possible.

    Attributes:
        decision: The outcome.
        reason_codes: Machine-readable rationale, e.g. `["C_BELOW_TAU_HI",
            "R_ABOVE_RHO_LO", "POL_PHI_REVIEW"]`. `PRD.md` FR-3.4 requires this
            in addition to any free text.
        confidence: The `C` report.
        risk: The `R` verdict.
        conflict: The Layer 2 report the decision was taken against.
        thresholds_version: Which threshold set was in force (`PRD.md` FR-3.3
            makes a threshold change an audited event).
        policy_version: Which policy pack was in force.
        escalated_from: The decision this one replaced after a FRONTIER
            re-score. `MEMORY_ENGINE.md` §3.4: an escalated result "cannot
            escalate again".
    """

    decision: Decision
    reason_codes: list[str]
    confidence: ConfidenceReport
    risk: RiskVerdict
    conflict: ConflictReport
    thresholds_version: str
    policy_version: str
    escalated_from: Decision | None = None
