"""Pairs settled by arithmetic, before any judge is asked.  S4.3

§2.2(b) and (c). A `ONE` predicate with a second live value, or a
`ONE_PER_TIME` predicate whose intervals intersect, is a conflict whatever a
model would say about the wording - so every row here carries
`contradiction=None`, and that `None` is the assertion under test: the probe
fails if a judge was called at all.

The controls matter as much as the conflicts. Half of these rows restate the
incumbent rather than contradicting it, because a cardinality check that fires
on *any* second write is not a conflict detector, it is an append blocker.
"""

from __future__ import annotations

from typing import Final

from fixtures.conflict_pair import ConflictPair, pair
from guardmem_core.schemas.verdict import ConflictKind

__all__ = ["SETTLED"]

_K: Final = ConflictKind.CARDINALITY
_D: Final = ConflictKind.DUPLICATE
_N: Final = ConflictKind.NONE
_T: Final = ConflictKind.TEMPORAL_OVERLAP

# --- (b) cardinality: ONE predicates, settled without a judge ---------------
# `primary_dx`, `blood_type`, `consent_flag`, `primary_care_provider`,
# `care_plan_status` and `preferred_language` are all `cardinality: one` in
# `ontology/clinical.yaml`. A second live value with a different object is a
# CARDINALITY conflict regardless of what any model thinks (§2.2b), and a value
# that agrees is not a conflict at all.
_CARDINALITY: Final[tuple[ConflictPair, ...]] = (
    pair(
        "card-dx-changed",
        "primary_dx",
        ("E11.9", "type 2 diabetes"),
        ("I10", "essential hypertension"),
        _K,
    ),
    pair(
        "card-dx-same",
        "primary_dx",
        ("E11.9", "type 2 diabetes"),
        ("E11.9", "diagnosed with type 2 diabetes"),
        _D,
        0.02,
        0.95,
    ),
    pair(
        "card-blood-changed",
        "blood_type",
        ("O negative", "blood group O negative"),
        ("A positive", "blood group A positive"),
        _K,
    ),
    pair(
        "card-blood-same",
        "blood_type",
        ("O negative", "blood group O negative"),
        ("O negative", "O neg"),
        _D,
        0.01,
        0.96,
    ),
    pair(
        "card-consent-withdrawn",
        "consent_flag",
        (True, "yes, you can share my records"),
        (False, "actually, please stop sharing my records"),
        _K,
    ),
    pair(
        "card-consent-reaffirmed",
        "consent_flag",
        (True, "yes, you can share my records"),
        (True, "yes that is still fine"),
        _D,
        0.02,
        0.9,
    ),
    pair(
        "card-gp-changed",
        "primary_care_provider",
        ("provider-okafor", "Dr Okafor is my GP"),
        ("provider-nash", "Dr Nash is my GP now"),
        _K,
    ),
    pair(
        "card-gp-same",
        "primary_care_provider",
        ("provider-okafor", "Dr Okafor is my GP"),
        ("provider-okafor", "my GP is Ama Okafor"),
        _D,
        0.01,
        0.94,
    ),
    pair(
        "card-plan-changed",
        "care_plan_status",
        ("active", "care plan status active"),
        ("paused", "care plan status paused"),
        _K,
    ),
    pair(
        "card-language-changed",
        "preferred_language",
        ("English", "English is fine"),
        ("Welsh", "I'd prefer Welsh from now on"),
        _K,
    ),
    pair(
        "card-plan-same",
        "care_plan_status",
        ("active", "care plan status active"),
        ("active", "the plan is still active"),
        _D,
        0.01,
        0.96,
    ),
    pair(
        "card-language-same",
        "preferred_language",
        ("English", "English is fine"),
        ("English", "write to me in English"),
        _D,
        0.01,
        0.95,
    ),
)

# --- (c) temporal overlap: ONE_PER_TIME predicates --------------------------
# `home_address`, `preferred_pharmacy`, `insurance_plan`, `advance_directive`
# and `weight_kg` are `cardinality: one_per_time`. A differing value whose
# interval intersects the incumbent's is TEMPORAL_OVERLAP. A candidate that
# starts after the incumbent closed does not overlap and is not a conflict -
# but the corpus cannot express intervals, so the temporal rows below all
# describe an open incumbent and an undated candidate, which is the state every
# candidate is in today (see `_overlaps`).
_TEMPORAL: Final[tuple[ConflictPair, ...]] = (
    pair(
        "time-address-moved",
        "home_address",
        ("14 Ashfield Road, Leeds", "14 Ashfield Road"),
        ("3 Calder Way, Leeds", "we're at 3 Calder Way now"),
        _T,
    ),
    pair(
        "time-address-same",
        "home_address",
        ("14 Ashfield Road, Leeds", "14 Ashfield Road"),
        ("14 Ashfield Road, Leeds", "still 14 Ashfield Road"),
        _D,
        0.01,
        0.97,
    ),
    pair(
        "time-pharmacy-moved",
        "preferred_pharmacy",
        ("pharmacy-headrow", "Boots on The Headrow"),
        ("pharmacy-calder", "Lloyds on Calder Street from now on"),
        _T,
    ),
    pair(
        "time-pharmacy-same",
        "preferred_pharmacy",
        ("pharmacy-headrow", "Boots on The Headrow"),
        ("pharmacy-headrow", "the Boots on The Headrow"),
        _D,
        0.01,
        0.95,
    ),
    pair(
        "time-insurer-changed",
        "insurance_plan",
        ("payer-northwind", "Northwind Health"),
        ("payer-calder", "we moved to Calder Mutual"),
        _T,
    ),
    pair(
        "time-directive-replaced",
        "advance_directive",
        ("doc-adv-7781", "directive on file"),
        ("doc-adv-9002", "a new directive was filed"),
        _T,
    ),
    pair("time-weight-changed", "weight_kg", (78.5, "78.5 kilos"), (76.2, "76.2 kilos"), _T),
    pair(
        "time-weight-same", "weight_kg", (78.5, "78.5 kilos"), (78.5, "still 78.5"), _D, 0.01, 0.98
    ),
    pair(
        "time-insurer-same",
        "insurance_plan",
        ("payer-northwind", "Northwind Health"),
        ("payer-northwind", "still with Northwind"),
        _D,
        0.01,
        0.96,
    ),
    pair(
        "time-directive-same",
        "advance_directive",
        ("doc-adv-7781", "directive on file"),
        ("doc-adv-7781", "the directive from 2024"),
        _D,
        0.01,
        0.97,
    ),
    pair(
        "time-weight-restated",
        "weight_kg",
        (78.5, "78.5 kilos"),
        (78.5, "78 and a half kilos"),
        _D,
        0.01,
        0.93,
    ),
)

SETTLED: Final[tuple[ConflictPair, ...]] = (*_CARDINALITY, *_TEMPORAL)
