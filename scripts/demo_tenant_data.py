"""The demo tenant's transcript and the facts it supports.  BUILD_NOTEBOOK.md S3.6

Data, kept apart from `seed_demo_tenant.py` so that script is the *procedure*
and this is the *content*. Both are under `RULES.md` §2.4's line cap and neither
would be inside it combined.

**Every seeded assertion cites a real span in a real turn.** `SeedFact` names the
turn and quotes it; the script locates the quote with `span_linker.link_span`,
the same function Layer 1 uses, and refuses to seed a fact whose quote is not
there. So the demo database satisfies `RULES.md` §1.1 with genuine offsets
rather than plausible-looking ones - which matters, because the seed is what
every later demo, screenshot and manual test reads, and a fabricated span would
be discovered by the first person who clicked through to the source.

**The transcript is not `tests/fixtures/noise_corpus.py`.** That one is forty
turns too, and it is deliberately different: its content is chosen for what the
Layer-1 noise rules must and must not catch, and its labels are a test gate. A
demo seeded from a test fixture would couple the two, so a change made to
exercise a rule would silently change the demo, and vice versa.

**Tool turns are EHR payloads and they carry a different trust tier.** The
clinical pack requires `trusted_system` for `primary_dx`, `blood_type`,
`insurance_plan` and `advance_directive` - a patient saying their own blood type
is not a verified EHR record. Without those turns the seed could not source
those predicates at all, which is the ontology doing exactly what it is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from guardmem_core.schemas.base import ObjectValue
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn, TurnRole
from guardmem_core.types import Namespace, TurnId

__all__ = [
    "INTAKE_AT",
    "MOVED_AT",
    "NAMESPACE",
    "OBJECT_ENTITIES",
    "SEED_FACTS",
    "SUPERSESSIONS",
    "TENANT_SLUG",
    "TIER_ORDER",
    "TRANSCRIPT",
    "SeedFact",
]

# The demo tenant's slug. Every id the seed writes is derived from it, which is
# what makes a second run a no-op - see `seed_demo_tenant.identifier`.
TENANT_SLUG: Final = "demo-clinic"

NAMESPACE: Final = Namespace("patient:7781")

# World time. The intake call happened; the move happened five months later and
# is what supersedes the address and the pharmacy.
INTAKE_AT: Final = datetime(2026, 4, 8, 10, 15, tzinfo=UTC)
MOVED_AT: Final = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)

# `RULES.md` §4's ordering, strongest first: "TRUSTED_SYSTEM > VERIFIED_USER >
# UNVERIFIED_USER > TOOL_OUTPUT > RETRIEVED_WEB". `SourceTier` is a `StrEnum`
# and carries no order of its own, so the fact lives here until S4.1's schema
# gate needs it too - at which point it belongs beside the enum, not in two
# places.
TIER_ORDER: Final = (
    SourceTier.TRUSTED_SYSTEM,
    SourceTier.VERIFIED_USER,
    SourceTier.UNVERIFIED_USER,
    SourceTier.TOOL_OUTPUT,
    SourceTier.RETRIEVED_WEB,
)

# Entity types the pack declares, for the ids the assertions below point at.
# The step says "one patient entity" and this is five more, deliberately: an
# `entity_ref` that resolves to nothing is a demo that misrepresents the model
# it is demonstrating, and the graph store would hold the reference as a bare
# literal.
OBJECT_ENTITIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("provider-okafor", "Provider", "Dr Ama Okafor"),
    ("pharmacy-headrow", "Pharmacy", "Boots, The Headrow"),
    ("pharmacy-calder", "Pharmacy", "Lloyds, Calder Street"),
    ("payer-northwind", "Payer", "Northwind Health"),
    ("person-ruth", "Person", "Ruth Ellery"),
    ("person-david", "Person", "David Ellery"),
    ("doc-adv-7781", "Document", "Advance directive, filed 2024"),
)

_U: Final = TurnRole.USER
_A: Final = TurnRole.ASSISTANT
_T: Final = TurnRole.TOOL

# The forty turns. One continuous intake call with three EHR payloads spliced
# in, because that is how a real intake goes: the clinician has the record open
# while the patient talks, and the two sources are not equally trustworthy.
_LINES: Final[tuple[tuple[TurnRole, str], ...]] = (
    (_A, "Good morning - this is the intake line for Northwind Health. Can I take your name?"),
    (_U, "Morning. It's Joan Ellery, date of birth the fourth of June nineteen fifty-eight."),
    (_A, "Thank you Joan. I'll pull your record up now."),
    (_T, "EHR lookup: patient 7781. primary diagnosis E11.9. blood group O negative."),
    (_A, "Got it. Before we go further, do you have any allergies I should know about?"),
    (
        _U,
        "Yes - I'm allergic to penicillin. That one's important, I had a bad reaction as a child.",
    ),
    (_A, "Noted. Anything else?"),
    (_U, "And latex, gloves bring me out in a rash. Shellfish too, though that's milder."),
    (_U, "Oh, and sulfa drugs. I forget that one because it's been years."),
    (_A, "Thank you, that's four. I'll read them back at the end."),
    (_A, "What medication are you taking at the moment?"),
    (_U, "I take metformin 500mg twice a day for the diabetes."),
    (_U, "And lisinopril 10mg in the mornings, for the blood pressure."),
    (_U, "Atorvastatin 20mg at night. That's the cholesterol one."),
    (_U, "There's insulin glargine as well, at bedtime."),
    (_U, "Plus vitamin D 1000iu, but that's just something I buy myself."),
    (_A, "Let me check that against what we have."),
    (_T, "EHR lookup: active prescriptions 5. insurance: Northwind Health, plan payer-northwind."),
    (_A, "That matches. Who is your GP at the moment?"),
    (_U, "Doctor Okafor - Ama Okafor. She's been my GP for about six years now."),
    (_A, "And which pharmacy do you normally use?"),
    (_U, "Boots on The Headrow, usually. It's on my way home from work."),
    (_A, "Thank you. One sec, let me get to the next section."),
    (_A, "Is there anything about your diet we should record?"),
    (_U, "Low sodium, the GP was firm about that one."),
    (_U, "No shellfish, obviously, given the allergy."),
    (_U, "I'm vegetarian as well, have been since my twenties."),
    (_U, "And no caffeine after 6pm or I don't sleep."),
    (_A, "Understood. Who should we contact in an emergency?"),
    (_U, "My daughter Ruth Ellery first. Then my husband David Ellery if she's not reachable."),
    (_U, "My sister says she'd rather not be on the list, she's overseas most of the year."),
    (_A, "That's fine, we'll leave her off. What language would you prefer we write to you in?"),
    (_U, "English is fine, thank you."),
    (_A, "Could you confirm your current address?"),
    (_U, "14 Ashfield Road, Leeds. Though we're moving in September, I'll ring you then."),
    (_A, "Please do. And your weight, if you have it to hand?"),
    (_U, "78.5 kilos as of last week's check."),
    (_T, "EHR lookup: advance directive doc-adv-7781 on file. care plan status active."),
    (_A, "Last thing - may we share your records with the diabetes clinic?"),
    (_U, "Yes, you can share my records with the diabetes clinic. That's fine by me."),
)

TRANSCRIPT: Final[tuple[Turn, ...]] = tuple(
    Turn(turn_id=TurnId(f"t{index:02d}"), role=role, text=text, captured_at=INTAKE_AT)
    for index, (role, text) in enumerate(_LINES, start=1)
)

# A second, much shorter call five months later. It exists so the demo has a
# supersession in it: `ARCHITECTURE.md` §0's "nothing is deleted, contradiction
# resolves by supersession + tombstone" is the product's central claim, and a
# seed with no retired facts cannot show it.
_MOVE_LINES: Final[tuple[tuple[TurnRole, str], ...]] = (
    (_U, "Hello again - I said I'd ring when we moved. We're at 3 Calder Way, Leeds now."),
    (_A, "Thank you, I'll update that. Does your pharmacy change too?"),
    (_U, "Yes, it'll be Lloyds on Calder Street from now on. Boots is the wrong side of town."),
)

MOVE_CALL: Final[tuple[Turn, ...]] = tuple(
    Turn(turn_id=TurnId(f"m{index:02d}"), role=role, text=text, captured_at=MOVED_AT)
    for index, (role, text) in enumerate(_MOVE_LINES, start=1)
)


@dataclass(frozen=True, slots=True)
class SeedFact:
    """One assertion to seed, and the turn that supports it.

    Attributes:
        key: Stable slug. The assertion id is derived from it, which is what
            makes the whole seed idempotent without a lookup table.
        predicate: Must be declared by the clinical pack; the script refuses a
            fact whose predicate is not, exactly as §2.1 would.
        object: The stored value.
        turn_id: Which turn supports it.
        quote: The supporting text, verbatim. Located with `link_span`; a quote
            that is not in that turn stops the seed rather than producing a
            fabricated offset.
        tier: Trust of the source. Checked against the predicate's declared
            `min_source_tier` - the EHR payload turns are the only reason the
            `trusted_system` predicates can be seeded at all.
        valid_from: World time the fact became true.
    """

    key: str
    predicate: str
    object: ObjectValue
    turn_id: str
    quote: str
    tier: SourceTier
    valid_from: datetime = INTAKE_AT


_VU: Final = SourceTier.VERIFIED_USER
_TS: Final = SourceTier.TRUSTED_SYSTEM

SEED_FACTS: Final[tuple[SeedFact, ...]] = (
    # Critical, from the patient. All four allergies; the pack requires
    # corroboration on these, and `corroboration_count` stays 1 here because the
    # seed has one source for each - which is the honest state and exactly what
    # a reviewer should see.
    SeedFact(
        "allergy-penicillin", "allergy", "penicillin", "t06", "I'm allergic to penicillin", _VU
    ),
    SeedFact(
        "allergy-latex", "allergy", "latex", "t08", "latex, gloves bring me out in a rash", _VU
    ),
    SeedFact("allergy-shellfish", "allergy", "shellfish", "t08", "Shellfish too", _VU),
    SeedFact("allergy-sulfa", "allergy", "sulfa drugs", "t09", "sulfa drugs", _VU),
    # Critical, from the record. A patient reporting their own diagnosis or
    # blood group does not meet the pack's `trusted_system` floor.
    SeedFact("dx-primary", "primary_dx", "E11.9", "t04", "primary diagnosis E11.9", _TS),
    SeedFact("blood-type", "blood_type", "O negative", "t04", "blood group O negative", _TS),
    SeedFact(
        "advance-directive",
        "advance_directive",
        "doc-adv-7781",
        "t38",
        "advance directive doc-adv-7781 on file",
        _TS,
    ),
    SeedFact(
        "consent-diabetes",
        "consent_flag",
        True,
        "t40",
        "you can share my records with the diabetes clinic",
        _VU,
    ),
    # Medication.
    SeedFact(
        "med-metformin", "medication", "metformin 500mg", "t12", "metformin 500mg twice a day", _VU
    ),
    SeedFact(
        "med-lisinopril",
        "medication",
        "lisinopril 10mg",
        "t13",
        "lisinopril 10mg in the mornings",
        _VU,
    ),
    SeedFact(
        "med-atorvastatin",
        "medication",
        "atorvastatin 20mg",
        "t14",
        "Atorvastatin 20mg at night",
        _VU,
    ),
    SeedFact(
        "med-insulin",
        "medication",
        "insulin glargine",
        "t15",
        "insulin glargine as well, at bedtime",
        _VU,
    ),
    SeedFact("med-vitamind", "medication", "vitamin D 1000iu", "t16", "vitamin D 1000iu", _VU),
    # High impact, mixed sources.
    SeedFact("insurance", "insurance_plan", "payer-northwind", "t18", "plan payer-northwind", _TS),
    SeedFact(
        "contact-ruth",
        "emergency_contact",
        "person-ruth",
        "t30",
        "My daughter Ruth Ellery first",
        _VU,
    ),
    SeedFact(
        "contact-david", "emergency_contact", "person-david", "t30", "my husband David Ellery", _VU
    ),
    # ONE_PER_TIME. The first is superseded by the second - see `SUPERSESSIONS`.
    SeedFact(
        "address-ashfield",
        "home_address",
        "14 Ashfield Road, Leeds",
        "t35",
        "14 Ashfield Road, Leeds",
        _VU,
    ),
    SeedFact(
        "address-calder",
        "home_address",
        "3 Calder Way, Leeds",
        "m01",
        "3 Calder Way, Leeds",
        _VU,
        MOVED_AT,
    ),
    # Medium and low.
    SeedFact("gp", "primary_care_provider", "provider-okafor", "t20", "Ama Okafor", _VU),
    SeedFact("care-plan", "care_plan_status", "active", "t38", "care plan status active", _TS),
    SeedFact("diet-sodium", "dietary_restriction", "low sodium", "t25", "Low sodium", _VU),
    SeedFact("diet-shellfish", "dietary_restriction", "no shellfish", "t26", "No shellfish", _VU),
    SeedFact("diet-vegetarian", "dietary_restriction", "vegetarian", "t27", "I'm vegetarian", _VU),
    SeedFact(
        "diet-caffeine",
        "dietary_restriction",
        "no caffeine after 6pm",
        "t28",
        "no caffeine after 6pm",
        _VU,
    ),
    SeedFact(
        "pharmacy-headrow",
        "preferred_pharmacy",
        "pharmacy-headrow",
        "t22",
        "Boots on The Headrow",
        _VU,
    ),
    SeedFact(
        "pharmacy-calder",
        "preferred_pharmacy",
        "pharmacy-calder",
        "m03",
        "Lloyds on Calder Street",
        _VU,
        MOVED_AT,
    ),
    SeedFact("language", "preferred_language", "English", "t33", "English is fine", _VU),
    SeedFact("weight", "weight_kg", 78.5, "t37", "78.5 kilos", _VU),
)

# `(retired, successor)` by key. `MEMORY_ENGINE.md` §2.3 sets the incumbent's
# `valid_to` to the successor's `valid_from`, so the two intervals abut exactly
# and a point-in-time query has no gap and no overlap.
SUPERSESSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("address-ashfield", "address-calder"),
    ("pharmacy-headrow", "pharmacy-calder"),
)
