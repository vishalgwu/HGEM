"""Pairs a judge has to catch.  S4.3

The mistake these are weighted against is a contradiction read as coexistence -
`contradiction_escape_rate` going quietly wrong, which is the failure mode
`MEMORY_ENGINE.md` §3.5 tracks because nothing downstream surfaces it. A missed
contradiction leaves two mutually exclusive facts both live and both citable.

Every row is a `cardinality: many` predicate, so neither (b) nor (c) fires and
the verdict turns entirely on the numbers. See `corpus_coexist` for the other
direction - the duplicates and the near-misses that must *not* be flagged.
"""

from __future__ import annotations

from typing import Final

from fixtures.conflict_pair import ConflictPair, pair
from guardmem_core.schemas.verdict import ConflictKind

__all__ = ["CONTRADICTIONS"]

_C: Final = ConflictKind.CONTRADICTION

# --- (a) NLI: MANY predicates, where only the judge can decide --------------
# `allergy`, `medication`, `emergency_contact` and `dietary_restriction` are
# `cardinality: many`, so neither (b) nor (c) fires and the numbers below are
# what the verdict turns on. Two allergies are two facts; an allergy and its
# denial are not.
_CONTRADICTIONS: Final[tuple[ConflictPair, ...]] = (
    pair(
        "nli-allergy-denied",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("penicillin", "not allergic to penicillin after all"),
        _C,
        0.95,
        0.02,
    ),
    pair(
        "nli-allergy-ruled-out",
        "allergy",
        ("shellfish", "allergic to shellfish"),
        ("shellfish", "the shellfish allergy was ruled out"),
        _C,
        0.92,
        0.03,
    ),
    pair(
        "nli-med-stopped",
        "medication",
        ("metformin 500mg", "takes metformin 500mg"),
        ("metformin 500mg", "stopped taking metformin"),
        _C,
        0.9,
        0.05,
    ),
    pair(
        "nli-med-never",
        "medication",
        ("lisinopril 10mg", "takes lisinopril"),
        ("lisinopril 10mg", "has never taken lisinopril"),
        _C,
        0.94,
        0.02,
    ),
    pair(
        "nli-contact-removed",
        "emergency_contact",
        ("person-ruth", "Ruth is my contact"),
        ("person-ruth", "please take Ruth off the list"),
        _C,
        0.88,
        0.04,
    ),
    pair(
        "nli-diet-lifted",
        "dietary_restriction",
        ("low sodium", "on a low sodium diet"),
        ("low sodium", "the sodium restriction was lifted"),
        _C,
        0.89,
        0.04,
    ),
    pair(
        "nli-allergy-tolerated",
        "allergy",
        ("latex", "latex brings me out in a rash"),
        ("latex", "latex gloves are fine now"),
        _C,
        0.86,
        0.05,
    ),
    pair(
        "nli-med-dose-conflict",
        "medication",
        ("insulin glargine", "insulin at bedtime"),
        ("insulin glargine", "no longer on insulin"),
        _C,
        0.91,
        0.03,
    ),
    pair(
        "nli-diet-reversed",
        "dietary_restriction",
        ("vegetarian", "I'm vegetarian"),
        ("vegetarian", "I eat meat again"),
        _C,
        0.87,
        0.04,
    ),
    pair(
        "nli-contact-declined",
        "emergency_contact",
        ("person-david", "David Ellery"),
        ("person-david", "David would rather not be contacted"),
        _C,
        0.83,
        0.06,
    ),
    pair(
        "nli-allergy-negated-late",
        "allergy",
        ("sulfa drugs", "allergic to sulfa drugs"),
        ("sulfa drugs", "the sulfa allergy turned out to be a mistake"),
        _C,
        0.9,
        0.03,
    ),
    pair(
        "nli-med-substituted",
        "medication",
        ("atorvastatin 20mg", "atorvastatin at night"),
        ("atorvastatin 20mg", "came off atorvastatin entirely"),
        _C,
        0.88,
        0.04,
    ),
    pair(
        "nli-diet-contradicted",
        "dietary_restriction",
        ("no caffeine after 6pm", "no caffeine after 6pm"),
        ("no caffeine after 6pm", "caffeine at night is fine for me"),
        _C,
        0.85,
        0.05,
    ),
)

CONTRADICTIONS: Final[tuple[ConflictPair, ...]] = _CONTRADICTIONS
