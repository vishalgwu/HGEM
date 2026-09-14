"""Pairs that must not be flagged, and pairs that must escalate.  S4.3

The other mistake: a duplicate or an unrelated fact read as a contradiction,
which retires something somebody still relies on. Two allergies are two facts.

`_AMBIGUOUS` is the harder half. §2.3 puts 0.3-0.65 in a band where "the NLI is
unsure, so a human or frontier model decides", so those rows are labelled NONE -
no contradiction is established - and the probe checks the *hint* is `escalate`
rather than `coexist`. Deciding either way there is the failure, and a corpus
holding only comfortable cases would measure nothing.
"""

from __future__ import annotations

from typing import Final

from fixtures.conflict_pair import ConflictPair, pair
from guardmem_core.schemas.verdict import ConflictKind

__all__ = ["COEXIST"]

_N: Final = ConflictKind.NONE

_COEXIST: Final[tuple[ConflictPair, ...]] = (
    pair(
        "ok-two-allergies",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("latex", "and latex"),
        _N,
        0.03,
        0.05,
    ),
    pair(
        "ok-two-meds",
        "medication",
        ("metformin 500mg", "takes metformin"),
        ("atorvastatin 20mg", "atorvastatin at night"),
        _N,
        0.02,
        0.04,
    ),
    pair(
        "ok-two-contacts",
        "emergency_contact",
        ("person-ruth", "my daughter Ruth"),
        ("person-david", "my husband David"),
        _N,
        0.02,
        0.03,
    ),
    pair(
        "ok-two-diets",
        "dietary_restriction",
        ("low sodium", "low sodium"),
        ("no shellfish", "no shellfish"),
        _N,
        0.02,
        0.03,
    ),
    pair(
        "ok-unrelated",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("sulfa drugs", "and sulfa drugs"),
        _N,
        0.03,
        0.05,
    ),
    pair(
        "ok-duplicate-wording",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("penicillin", "penicillin allergy"),
        _N,
        0.02,
        0.93,
    ),
    pair(
        "ok-med-restated",
        "medication",
        ("metformin 500mg", "metformin 500mg twice a day"),
        ("metformin 500mg", "takes metformin 500mg"),
        _N,
        0.02,
        0.95,
    ),
    pair(
        "ok-narrower-allergy",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("penicillin", "allergic to penicillin, hives specifically"),
        _N,
        0.04,
        0.88,
    ),
    pair(
        "ok-diet-added",
        "dietary_restriction",
        ("vegetarian", "I'm vegetarian"),
        ("no caffeine after 6pm", "no caffeine after 6pm"),
        _N,
        0.02,
        0.02,
    ),
    pair(
        "ok-contact-added",
        "emergency_contact",
        ("person-ruth", "my daughter Ruth"),
        ("person-marcus", "my son Marcus as a third"),
        _N,
        0.02,
        0.03,
    ),
    pair(
        "ok-med-different-class",
        "medication",
        ("vitamin D 1000iu", "vitamin D"),
        ("insulin glargine", "insulin at bedtime"),
        _N,
        0.01,
        0.02,
    ),
    pair(
        "ok-allergy-severity",
        "allergy",
        ("shellfish", "shellfish, though that's milder"),
        ("shellfish", "shellfish allergy, mild"),
        _N,
        0.03,
        0.9,
    ),
    pair(
        "ok-diet-restated",
        "dietary_restriction",
        ("no shellfish", "no shellfish"),
        ("no shellfish", "avoids shellfish"),
        _N,
        0.02,
        0.92,
    ),
    pair(
        "ok-contact-restated",
        "emergency_contact",
        ("person-ruth", "Ruth Ellery first"),
        ("person-ruth", "my daughter, Ruth"),
        _N,
        0.02,
        0.94,
    ),
    pair(
        "ok-med-added",
        "medication",
        ("metformin 500mg", "metformin"),
        ("lisinopril 10mg", "lisinopril in the mornings"),
        _N,
        0.02,
        0.03,
    ),
    pair(
        "ok-allergy-and-diet",
        "allergy",
        ("shellfish", "allergic to shellfish"),
        ("penicillin", "and penicillin"),
        _N,
        0.02,
        0.04,
    ),
    pair(
        "ok-med-vitamin",
        "medication",
        ("insulin glargine", "insulin at bedtime"),
        ("vitamin D 1000iu", "vitamin D, which I buy myself"),
        _N,
        0.01,
        0.02,
    ),
    pair(
        "ok-contact-relation",
        "emergency_contact",
        ("person-ruth", "my daughter Ruth"),
        ("person-ruth", "Ruth, my daughter"),
        _N,
        0.02,
        0.95,
    ),
)

# The band §2.3 calls ambiguous: "0.3-0.65 - the NLI is unsure, so a human or
# frontier model decides". These are labelled NONE because no contradiction is
# established, and the probe checks the *hint* is `escalate` rather than
# `coexist` - deciding either way here is the failure.
_AMBIGUOUS: Final[tuple[ConflictPair, ...]] = (
    pair(
        "ambiguous-allergy-doubt",
        "allergy",
        ("penicillin", "allergic to penicillin"),
        ("penicillin", "I might have grown out of the penicillin thing"),
        _N,
        0.5,
        0.3,
    ),
    pair(
        "ambiguous-med-paused",
        "medication",
        ("metformin 500mg", "takes metformin"),
        ("metformin 500mg", "I'm off metformin this week"),
        _N,
        0.55,
        0.2,
    ),
    pair(
        "ambiguous-diet-relaxed",
        "dietary_restriction",
        ("low sodium", "low sodium"),
        ("low sodium", "I'm not as strict about salt now"),
        _N,
        0.45,
        0.35,
    ),
    pair(
        "ambiguous-contact-unsure",
        "emergency_contact",
        ("person-david", "my husband David"),
        ("person-david", "David maybe, I'd have to ask him"),
        _N,
        0.4,
        0.4,
    ),
    pair(
        "ambiguous-allergy-hearsay",
        "allergy",
        ("latex", "latex rash"),
        ("latex", "someone said it might not be latex"),
        _N,
        0.35,
        0.3,
    ),
    pair(
        "ambiguous-med-hearsay",
        "medication",
        ("lisinopril 10mg", "lisinopril in the mornings"),
        ("lisinopril 10mg", "I think the GP was going to change that one"),
        _N,
        0.45,
        0.3,
    ),
)

COEXIST: Final[tuple[ConflictPair, ...]] = (*_COEXIST, *_AMBIGUOUS)
