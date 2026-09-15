"""What a predicate declares about its own danger.  MEMORY_ENGINE.md §2.1, §3.3

Three enums, one property each, and they belong together because the ontology
declares all three and §3.3 weighs all three. `ImpactLevel` has been a
`PredicateSpec` field since S3.5; `PiiClass` and `Irreversibility` joined it at
ADR-0009, which found that they had been sitting behind a `CandidateClassifier`
protocol for four steps while `impact` - the same kind of judgement, by the same
people, feeding the same score through the same `{0, .33, .66, 1}` shape - was a
declared field. Nothing separated them except that one had been written down.

**Split out of `verdict.py` by `RULES.md` §2.4's line cap**, and the seam the cap
found is the right one: this module is what a *predicate* says about itself,
`verdict.py` is what the pipeline concluded about a *candidate*. The ontology
imports this and never imports a verdict.

The mapping tables are private under every enum, so a caller reaches a number
through the enum rather than by string. That is S3.6's audit finding: the same
four floors were prose in one docstring and a dict literal in a seed script,
with nothing comparing them.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ImpactLevel", "Irreversibility", "PiiClass"]


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


class PiiClass(StrEnum):
    """What kind of personal data a predicate carries, from the ontology.  §3.3

    The four are the vocabulary of data-protection law rather than this
    project's invention - "special category" is GDPR Article 9's term, and a
    clinical pack is made almost entirely of it.

    **Declared per predicate and never inferred** (ADR-0009). Whether something
    is Article 9 data is a compliance answer, and a compliance answer should be
    a file somebody signed off rather than a completion. Here beside
    `ImpactLevel` rather than in `l3_score/impact_features.py`, where it was
    born, because this is now something the ontology declares and the ontology
    may not import from the pipeline.
    """

    NONE = "none"
    QUASI_IDENTIFIER = "quasi_identifier"  # re-identifying in combination
    DIRECT = "direct"  # names, numbers, addresses
    SPECIAL_CATEGORY = "special_category"  # health, biometrics, beliefs

    @property
    def risk_feature(self) -> float:
        """This class as §3.3's `pii_class` feature.

        Returns:
            `MEMORY_ENGINE.md` §3.3's mapping: none 0, quasi-identifier .5,
            direct .8, special-category 1.

        Not evenly spaced, and that is §3.3's: the step from a quasi-identifier
        to a direct one is smaller than the step from nothing to re-identifiable
        in combination.
        """
        return _PII_FEATURES[self]


_PII_FEATURES: dict[PiiClass, float] = {
    PiiClass.NONE: 0.0,
    PiiClass.QUASI_IDENTIFIER: 0.5,
    PiiClass.DIRECT: 0.8,
    PiiClass.SPECIAL_CATEGORY: 1.0,
}


class Irreversibility(StrEnum):
    """Can acting on this predicate be practically undone?  §3.3

    Note "practically" and "downstream". Nothing inside this system is
    irreversible - `ARCHITECTURE.md` §0 makes supersession the only way a fact
    stops being believed, and the prior one stays readable. What is not
    reversible is what an *agent* did while holding the wrong belief: an email
    sent, a prescription filed, a payment made.

    **None of which has happened when `R` is computed**, which is why this can
    never be an observation about a candidate and is instead a prior about the
    predicate (ADR-0009). An allergy drives a prescription; a preferred language
    drives a letter template. The deploying organisation states which is which.
    """

    REVERSIBLE = "reversible"
    PARTIAL = "partial"
    IRREVERSIBLE = "irreversible"

    @property
    def risk_feature(self) -> float:
        """This level as §3.3's `irreversibility` feature.

        Returns:
            `MEMORY_ENGINE.md` §3.3's mapping: {0, .5, 1}.
        """
        return _IRREVERSIBILITY_FEATURES[self]


_IRREVERSIBILITY_FEATURES: dict[Irreversibility, float] = {
    Irreversibility.REVERSIBLE: 0.0,
    Irreversibility.PARTIAL: 0.5,
    Irreversibility.IRREVERSIBLE: 1.0,
}
