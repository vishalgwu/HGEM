# ADR-0009: `pii_class` and `irreversibility` are ontology fields, and `CandidateClassifier` is deleted

**Status:** Accepted
**Date:** 2026-09-15
**Amends:** `MEMORY_ENGINE.md` §2.1 (the predicate declaration), §3.3 (two feature definitions)

## Context

§3.3 scores eight features and defines three of them nowhere. `scope`,
`pii_class` and `irreversibility` appear in its table, in no ontology field, in
no extractor output, and in no other document. S5.6 grouped them behind a
`CandidateClassifier` protocol and shipped no implementation, which is why
`run()` cannot be called.

They have been treated as one gap. They are three, and only one of them is
still open.

**`scope` was already answered.** `scope_of_namespace` reads it off the
namespace prefix — `org:` is `ORG`, `session:` is `SESSION`, everything else is
`USER` — and `MEMORY_ENGINE.md` documents that convention. It ships as a default
and is not in dispute here.

**The other two are predicate-level policy, and the ontology is already full of
predicate-level policy.** This is the observation the ADR turns on. Look at what
`PredicateSpec` already declares:

| Field | What kind of thing it is |
|---|---|
| `cardinality` | a fact about the domain |
| `impact` | **a policy judgement by the deploying organisation** |
| `min_source_tier` | **a policy judgement by the deploying organisation** |
| `requires_corroboration` | **a policy judgement by the deploying organisation** |

`deps.py` justified the protocol by saying `pii_class` and `irreversibility`
"are decisions a deploying organisation makes". That is true, and it is equally
true of `impact` — which is declared in a YAML file, validated at load, and
feeds the *same* §3.3 score through the same kind of `{0, .33, .66, 1}` mapping.
No principle separates them. One was put in the ontology and two were put behind
a protocol, and nothing recorded why.

**`irreversibility` in particular cannot be a per-candidate question.**
`Irreversibility`'s own docstring is precise about what it asks: not whether the
*fact* can be retracted — `ARCHITECTURE.md` §0 makes everything in this system
reversible by supersession — but whether what an agent *did* while holding the
belief can be undone. An email sent, a prescription filed, a payment made.

None of that has happened when `R` is computed. The pipeline scores a candidate
*before* anything acts on it, so there is no observation to classify. What can
be stated is a prior: *if* an agent acted on this predicate, how recoverable
would that be? An allergy drives a prescription; a preferred language drives a
letter template. That is a property of the predicate, decided once by the people
deploying the system, and it is exactly the shape of a declaration rather than
of an inference.

## Decision

**`PredicateSpec` gains `pii_class` and `irreversibility`, both required.**

```yaml
allergy:
  subject: Patient
  object: {type: coded, system: RxNorm}
  cardinality: many
  impact: critical
  pii_class: special_category      # NEW
  irreversibility: irreversible    # NEW
  min_source_tier: verified_user
  requires_corroboration: true
```

The vocabularies are the ones `impact_features.py` already declares and §3.3
already names — `none | quasi_identifier | direct | special_category` and
`reversible | partial | irreversible`. No new enum, no new mapping, and the
`risk_feature` properties that convert them to floats are unchanged.

**Required, not defaulted**, for the reason `min_source_tier` is required and
stated fifteen times in the pack rather than assumed once: there is no safe
default in either direction. Defaulting `pii_class` to `none` and
`irreversibility` to `reversible` scores 0.0 on both features and silently
under-prices every predicate nobody got round to classifying — the failure that
looks like a working system. Defaulting them to the top makes an omission
indistinguishable from a deliberate maximum, and floods review. A required field
makes the deploying organisation answer, which is what these two questions are
for.

**`scope` stays derived** from the namespace. It is the one of the three that is
a fact about the write rather than about the predicate, and it already has a
producer.

**`CandidateClassifier` and `CandidateRisk` are deleted.** Once all three
features come from the ontology and the namespace, the protocol has no
implementation, no possible implementation that is not "read the spec", and one
consumer. `RULES.md`'s working convention is to resist a seam with nothing on
the other side of it; keeping an async protocol so that a synchronous field read
can be awaited would be indirection paid for with a gap in `Deps` that blocks
`run()`.

`risk_features` takes the `PredicateSpec` it is already handed part of — it
currently receives `impact: ImpactLevel` unpacked from the spec — and the
namespace, and reads all four declared inputs from them.

### What this does not decide

- **Content-level PII detection.** S11.3 builds a Presidio-backed detector and a
  tokenization vault. That answers a different question — *does this string
  contain a phone number* — and does not supersede a declaration about what kind
  of data a predicate holds. If a deployment later wants a detected instance to
  raise a candidate above its predicate's declared class, the natural shape is
  the one `impact` already uses on `R`: **a declared floor that evidence can
  raise, never lower**. Named here so the ontology decision is not read as
  foreclosing it.
- **Refitting β.** `threshold_tuner.py` refits §3.3's coefficients from reviewer
  labels. These two features becoming declarations rather than inferences makes
  them *more* useful to it, not less: a stable per-predicate value is a clean
  regressor.

## Alternatives rejected

- **An LLM classifier per candidate.** The obvious reading of "classifier", and
  wrong three times over. It pays a model call per candidate to re-derive a
  constant; it makes `R` non-deterministic, which breaks the replay guarantee
  `RULES.md` §1.6 makes and which `decide()` is kept pure to protect; and it
  puts a data-protection judgement — is this special-category data under Article
  9? — in the hands of a sampler. A compliance answer should be a file someone
  signed off, not a completion.
- **Default both fields to their safe-looking value.** Rejected above. `none`
  and `reversible` are both 0.0, so an unclassified predicate scores as the
  least dangerous thing in the ontology.
- **Keep `CandidateClassifier` as an override seam, defaulting to the ontology.**
  This was the close call. Rejected because the seam has no second
  implementation in prospect — S11.3's detector fits the floor-raise shape
  above, not this one — and because an empty protocol in `Deps` is
  indistinguishable, from the outside, from the unimplemented one that has been
  blocking `run()` for four steps. If a per-candidate override is ever really
  needed, adding it back is a smaller change than the confusion is worth today.
- **Derive `pii_class` from the object type.** `{type: coded, system: RxNorm}`
  looks like it implies health data. It does not: `insurance_plan` is also coded
  and is not special-category, while `emergency_contact` is a plain
  `entity_ref` naming a real person. The object's *type* is a value domain; its
  *sensitivity* is orthogonal, and conflating them would get both wrong on the
  clinical pack this system ships with.
- **Put them on the policy pack (S12.2) instead of the ontology.** Defensible,
  and rejected on timing and cohesion. The policy engine does not exist; these
  two feed a score that exists now; and `impact` — their nearest relative —
  already lives in the ontology, so splitting them across two files would put
  one §3.3 feature in each.

## Consequences

- **`Deps` loses a member, and `run()`'s unimplemented dependencies go from two
  to one.** After this and ADR-0008, `EntityResolver` is the only protocol in
  `Deps` with no implementation, and it has a decision behind it.
- **`clinical.yaml` gains thirty lines and its `version` goes to 2.** All
  fifteen predicates must be classified; a required field means the pack does
  not load otherwise. The revalidation sweep §2.1 promises on a version change
  **does not exist** (open item #28), so nothing re-checks stored assertions —
  worth knowing, and not introduced by this ADR.
- **`risk_features` changes signature**, taking the spec and the namespace in
  place of `classified: CandidateRisk` and `impact: ImpactLevel`.
- **A test loses its instrument.** `tests/unit/test_orchestrator.py`'s
  concurrency probe counts overlapping scorings by wrapping the classifier —
  "`_decide_one` awaits it once per candidate", which is exactly why it was
  chosen. With the classifier gone the probe moves to another per-candidate
  await; `GraphStore.degree` and the NLI judge are both suitable, and the
  resolver will be once ADR-0008 is implemented. The test's *assertion* does not
  change.
- **Third-party ontology packs break.** Only `clinical.yaml` ships, so the blast
  radius today is one file. `parse_ontology` will name the missing field and the
  predicate, which is the behaviour a required field already has for
  `min_source_tier`.
- **The audit record gets better and this is the quiet win.** `RiskFeatures` is
  persisted verbatim on `RiskVerdict` so the review UI can show why something
  was flagged. Today two of the eight numbers would trace back to whatever a
  classifier happened to return; after this they trace to a named field in a
  versioned file, and "why was this scored as special-category?" is answerable
  by reading one line rather than by re-running a model.
