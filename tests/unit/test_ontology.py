"""The ontology loader, and the pack it ships with.  BUILD_NOTEBOOK.md S3.5

S3.5's DONE WHEN is "loader validates the YAML into typed objects and rejects an
unknown `impact` value", and `TestRefusals` holds it - along with the other nine
ways a pack can be wrong, because `impact` is not special. The reason to test
any of them is the same: this file is the only thing standing between a typo and
a predicate that either cannot be written or can be written by anyone.

**The shipped pack is tested as an asset, not just as input.** `TestTheClinical
Pack` asserts on `clinical.yaml` itself - that it declares what the step asks
for, and that no high-impact predicate accepts a source `RULES.md` §4 says may
never write one. A loader that works on a pack nobody checked is half a step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from guardmem_core.schemas import (
    Cardinality,
    CodedObject,
    EntityRefObject,
    ImpactLevel,
    ScalarObject,
    SourceTier,
    load_ontology,
    parse_ontology,
)
from guardmem_core.schemas import ontology as ontology_module

if TYPE_CHECKING:
    from pathlib import Path

# Tiers that `RULES.md` §4 puts below a human: "retrieved web content can never
# auto-write a HIGH-impact predicate regardless of confidence".
WEAK_TIERS = frozenset({SourceTier.RETRIEVED_WEB, SourceTier.TOOL_OUTPUT})

# The predicates BUILD_NOTEBOOK S3.5 names explicitly. The pack has fifteen; a
# name here that goes missing is a spec the pack stopped meeting.
NAMED_BY_THE_STEP = (
    "allergy",
    "medication",
    "primary_dx",
    "primary_care_provider",
    "preferred_pharmacy",
    "insurance_plan",
    "emergency_contact",
    "care_plan_status",
    "consent_flag",
)

# Every required field, and no optional one: `requires_corroboration` is absent
# because a test below asserts it defaults to false. `pii_class` and
# `irreversibility` are here because ADR-0009 made them required - a pack
# missing either does not load, which is the whole point of requiring them.
MINIMAL = """
name: tiny
version: 1
entities: [Patient]
predicates:
  allergy:
    subject: Patient
    object: {type: text}
    cardinality: many
    impact: critical
    pii_class: special_category
    irreversibility: irreversible
    min_source_tier: verified_user
"""


def pack(*substitutions: tuple[str, str]) -> str:
    """`MINIMAL` with lines replaced, so a test shows only what it changed.

    The `assert` is the point. The first version of this helper took keyword
    arguments and mangled the names back into YAML, which substituted nothing -
    so nine refusal tests parsed a valid pack, raised nothing, and every one of
    them still "passed" because `pytest.raises` was the only thing they
    checked... until it was not. A substitution that does not apply is now a
    loud failure rather than a test that has quietly stopped testing.
    """
    text = MINIMAL
    for old, new in substitutions:
        assert old in text, f"{old!r} is not in the pack; this test would pass vacuously"
        text = text.replace(old, new)
    return text


class TestTheClinicalPack:
    def test_it_loads(self) -> None:
        assert load_ontology("clinical").name == "clinical"

    def test_it_declares_the_fifteen_predicates_the_step_asks_for(self) -> None:
        assert len(load_ontology("clinical").predicates) == 15

    @pytest.mark.parametrize("name", NAMED_BY_THE_STEP)
    def test_every_predicate_the_step_names_is_present(self, name: str) -> None:
        assert load_ontology("clinical").predicate(name) is not None

    def test_it_is_versioned(self) -> None:
        """`MEMORY_ENGINE.md` §2.1: a schema change is an audited event that
        triggers a revalidation sweep. Nothing sweeps yet; the version is what
        makes it possible to know what to sweep."""
        assert load_ontology("clinical").version >= 1

    def test_no_high_impact_predicate_trusts_a_weak_source(self) -> None:
        """`RULES.md` §4 caps what a tier may auto-write by impact. A pack that
        let `retrieved_web` assert a critical predicate would put the whole
        argument on the decision matrix, which is the wrong place for it - the
        ontology should not have proposed it in the first place.
        """
        risky = {
            name: spec.min_source_tier
            for name, spec in load_ontology("clinical").predicates.items()
            if spec.impact in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}
            and spec.min_source_tier in WEAK_TIERS
        }

        assert not risky

    def test_the_spec_examples_survive_verbatim(self) -> None:
        """§2.1's three worked examples, unchanged. The pack extends that YAML
        rather than reinterpreting it, and a drift here is a drift from the spec
        of record."""
        clinical = load_ontology("clinical")

        allergy = clinical.predicate("allergy")
        assert allergy is not None
        assert allergy.object == CodedObject(type="coded", system="RxNorm")
        assert allergy.cardinality is Cardinality.MANY
        assert allergy.impact is ImpactLevel.CRITICAL
        assert allergy.min_source_tier is SourceTier.VERIFIED_USER
        assert allergy.requires_corroboration is True

        pcp = clinical.predicate("primary_care_provider")
        assert pcp is not None
        assert pcp.object == EntityRefObject(type="entity_ref", entity="Provider")
        assert pcp.cardinality is Cardinality.ONE

        pharmacy = clinical.predicate("preferred_pharmacy")
        assert pharmacy is not None
        assert pharmacy.cardinality is Cardinality.ONE_PER_TIME
        assert pharmacy.impact is ImpactLevel.LOW


class TestLookup:
    def test_a_declared_predicate_comes_back_typed(self) -> None:
        weight = load_ontology("clinical").predicate("weight_kg")

        assert weight is not None
        assert weight.object == ScalarObject(type="number")

    def test_an_unknown_predicate_returns_none_rather_than_raising(self) -> None:
        """§2.1 sends an unknown predicate to the `quarantine` namespace -
        "retrievable, flagged, never promoted without review". That is a
        decision the schema gate makes, not an error this lookup forces."""
        assert load_ontology("clinical").predicate("favourite_colour") is None


class TestLoading:
    def test_loads_are_cached(self) -> None:
        """§2.1 versions ontologies so a sweep knows what changed; a pack that
        could change under a running process makes `version` a claim."""
        assert load_ontology("clinical") is load_ontology("clinical")

    def test_an_unknown_pack_says_where_packs_live(self) -> None:
        with pytest.raises(FileNotFoundError, match="ontology/"):
            load_ontology("veterinary")

    @pytest.mark.parametrize("name", ["../secrets", "sub/pack", "", "Clinical"])
    def test_a_name_that_is_not_one_path_segment_is_refused(self, name: str) -> None:
        """`load_ontology` is a public function of a library package, and
        `_PACK_ROOT / name` resolves `../..` perfectly happily."""
        with pytest.raises(ValueError, match="single lowercase path segment"):
            load_ontology(name)

    def test_a_pack_whose_name_disagrees_with_its_filename_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A copied file in the wrong place must be a load error.

        `PromptSpec` applies the same rule for the same reason: the path is the
        identity a call site uses, so `load_ontology("legal")` returning a pack
        that calls itself `clinical` would silently validate every candidate
        against the wrong vocabulary.

        Written through a redirected pack root rather than by adding a broken
        YAML file to the package, which would ship in the wheel - the wheel
        contents are checked, and a deliberately invalid pack inside it is a
        trap for whoever lists the directory next.
        """
        (tmp_path / "legal.yaml").write_text(MINIMAL.replace("name: tiny", "name: clinical"))
        monkeypatch.setattr(ontology_module, "_PACK_ROOT", tmp_path)
        load_ontology.cache_clear()

        with pytest.raises(ValueError, match="declares name 'clinical'"):
            load_ontology("legal")

        load_ontology.cache_clear()


class TestRefusals:
    def test_an_unknown_impact_is_rejected(self) -> None:
        """S3.5's DONE WHEN, in one line."""
        with pytest.raises(ValidationError, match="impact"):
            parse_ontology(pack(("impact: critical", "impact: catastrophic")), source="t")

    def test_an_unknown_cardinality_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cardinality"):
            parse_ontology(pack(("cardinality: many", "cardinality: several")), source="t")

    def test_an_unknown_source_tier_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_source_tier"):
            parse_ontology(
                pack(("min_source_tier: verified_user", "min_source_tier: a_friend")), source="t"
            )

    def test_a_missing_min_source_tier_is_rejected(self) -> None:
        """Required, unlike the §2.1 sketch. There is no value that is safe to
        assume: permissive silently widens a safety surface, strict makes an
        omission look like a broken predicate."""
        with pytest.raises(ValidationError, match="min_source_tier"):
            parse_ontology(pack(("    min_source_tier: verified_user\n", "")), source="t")

    def test_requires_corroboration_defaults_to_false(self) -> None:
        """Unlike the tier, its absence in the §2.1 example reads unambiguously
        as "no", and it is the ordinary case."""
        parsed = parse_ontology(MINIMAL, source="t").predicate("allergy")

        assert parsed is not None
        assert parsed.requires_corroboration is False

    def test_a_coded_object_without_a_system_is_rejected(self) -> None:
        """A coded value whose terminology nobody declared cannot be validated,
        deduplicated or shown to a reviewer."""
        with pytest.raises(ValidationError, match="system"):
            parse_ontology(pack(("object: {type: text}", "object: {type: coded}")), source="t")

    def test_an_unknown_object_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="type"):
            parse_ontology(pack(("object: {type: text}", "object: {type: blob}")), source="t")

    def test_an_unknown_key_on_a_predicate_is_rejected(self) -> None:
        """`extra="forbid"`: a misspelled key in a safety file must be a load
        error, never a silently ignored line."""
        with pytest.raises(ValidationError, match="impackt"):
            parse_ontology(MINIMAL + "    impackt: low\n", source="t")

    def test_an_undeclared_subject_type_is_rejected(self) -> None:
        """`subject: Provder` is a predicate no candidate can ever match, and it
        looks like an extraction failure three layers away."""
        with pytest.raises(ValidationError, match="undeclared entity types"):
            parse_ontology(pack(("subject: Patient", "subject: Provder")), source="t")

    def test_an_undeclared_entity_ref_target_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="undeclared entity types"):
            parse_ontology(
                pack(("object: {type: text}", "object: {type: entity_ref, entity: Pharmcy}")),
                source="t",
            )

    def test_every_dangling_reference_is_reported_at_once(self) -> None:
        """Fixing a fifteen-predicate pack one error per run takes fifteen runs."""
        with pytest.raises(ValidationError) as raised:
            parse_ontology(
                pack(
                    ("subject: Patient", "subject: Provder"),
                    ("object: {type: text}", "object: {type: entity_ref, entity: Pharmcy}"),
                ),
                source="t",
            )

        assert "Provder" in str(raised.value)
        assert "Pharmcy" in str(raised.value)

    def test_a_duplicate_predicate_key_is_rejected(self) -> None:
        """`yaml.safe_load` keeps the last value silently, so a pack with two
        `allergy:` blocks would load and enforce whichever came second."""
        with pytest.raises(ValueError, match="duplicate key 'allergy'"):
            parse_ontology(
                MINIMAL
                + """  allergy:
    subject: Patient
    object: {type: text}
    cardinality: one
    impact: low
    pii_class: none
    irreversibility: reversible
    min_source_tier: retrieved_web
""",
                source="t",
            )

    def test_a_pack_that_is_not_a_mapping_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            parse_ontology("- allergy\n- medication\n", source="t")

    def test_a_value_json_cannot_carry_is_rejected(self) -> None:
        """An unquoted `2026-03-14` is a `datetime.date` to YAML and nothing at
        all to JSON, so it would otherwise reach pydantic as an object no field
        declares."""
        with pytest.raises(ValueError, match="JSON cannot carry"):
            parse_ontology(MINIMAL + "released: 2026-03-14\n", source="t")

    def test_an_empty_pack_is_rejected(self) -> None:
        """A pack declaring nothing quarantines everything, which is a
        configuration mistake rather than a policy."""
        with pytest.raises(ValidationError):
            parse_ontology("name: tiny\nversion: 1\nentities: []\npredicates: {}\n", source="t")

    def test_the_error_names_the_pack_it_came_from(self) -> None:
        """Three packs loading at once makes "line 14 is wrong" useless."""
        with pytest.raises(ValueError, match="tenant-8812"):
            parse_ontology("just a string", source="tenant-8812")
