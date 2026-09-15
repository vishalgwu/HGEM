# ADR-0008: Entity resolution binds, it does not match

**Status:** Accepted
**Date:** 2026-09-15
**Amends:** `MCP_INTEGRATION.md` §2.2 (`hints.subject`), `ARCHITECTURE.md` §5 (`:Entity`)

## Context

`MemoryCandidate.subject` is a surface form — "Joan Ellery", as the speaker said
it. `StoredAssertion.subject_id` is an `EntityId`, and `assertion.subject_id` is
`UUID NOT NULL REFERENCES entity(id)`. Nothing turns one into the other.

`pipeline/deps.py` has called this "the largest gap in the build" since S5.6 and
is right that it is specified nowhere. Measured across the design suite, entity
resolution appears in **one line** — `ARCHITECTURE.md` §5's node shape,
`(:Entity {id, tenant_id, type, canonical_name})`. Not the notebook, not
`MEMORY_ENGINE.md`, not `PRD.md`.

Building the rest of the pipeline turned that gap into four specific facts,
which is what makes a decision possible now and was not possible before.

**1. The protocol signature cannot do the job.** `EntityResolver.resolve` takes
`(subject, *, tenant_id, namespace)`. Creating an entity needs `type`, which is
`NOT NULL`, and nothing in that signature supplies one. The information is in
scope at the only call site and is not passed:

```python
spec = deps.ontology.predicate(candidate.predicate)   # carries spec.subject
...
subject_id = await deps.resolver.resolve(
    proposal.subject_hint or candidate.subject,
    tenant_id=proposal.tenant_id,
    namespace=proposal.namespace,                      # spec is not passed
)
```

`PredicateSpec.subject` **is** the entity type — `allergy` declares
`subject: Patient`, checked at load against the pack's closed `entities` list.
So the type was always available and the seam was drawn one argument too narrow.

**2. Nothing can write an `entity` row.** `VectorStore` and `GraphStore` have no
entity CRUD, and the FK means the row must exist *before* the assertion. The
grants anticipate a writer — `GRANT SELECT, INSERT, UPDATE ON entity` — and
there is none.

**3. `namespace` is in the signature and not in the table.** `entity` is
`(id, tenant_id, type, canonical_name, created_at)`. So resolution is handed a
scope that storage cannot record, and two subjects in different namespaces of
one tenant have nowhere to differ.

**4. The namespace already names the subject.** This is the fact that changes
the problem. `MEMORY_ENGINE.md` documents the convention as
`"patient:8812" | "org:acme" | "session:xyz"`; `scope_of_namespace` reads the
prefix; the demo tenant is seeded under `patient:7781` with exactly one patient.
For the case this system is actually built around, *which* subject a proposal
concerns is already stated by the caller, unambiguously, before any text is
read.

That reframes the question. The literature problem — deciding whether "Joan
Ellery" and "Joan E." are one person — is a matching problem with a
precision/recall trade-off and no data here to set it with. It is also **not the
problem in front of us**, because nothing in the current design requires a name
to be matched at all.

## Decision

**v1 resolves a subject by explicit binding, and performs no name matching of
any kind.** A surface form is recorded, never compared.

`resolve` consults three sources, in this order, and stops at the first that
answers:

1. **`hints.subject`, when the caller supplies one.** It is an `EntityId`, and
   this ADR makes that explicit — see the amendment below. A caller that already
   knows the entity says so, and is believed.
2. **The namespace, when it is subject-bound.** A namespace of the form
   `<type>:<id>` whose `<type>` matches a declared ontology entity
   case-insensitively names one subject. The entity id is then derived:

   ```
   entity_id = uuid5(NAMESPACE_URL, f"guardmem/{tenant_id}/entity/{namespace}")
   ```

   Derived rather than looked up, which is the pattern every replay path in this
   repository already uses and which open item #2 states as a rule: the id is a
   pure function of `(tenant, namespace)`, so creation is
   `INSERT ... ON CONFLICT (id) DO NOTHING`, idempotent and race-free, with no
   read before the write and no second caller able to mint a rival id.
3. **Otherwise, refuse.** `ValidationRejected` (`GM_VALIDATION`), naming both
   remedies: supply `hints.subject`, or propose under a subject-bound namespace.

**The declared subject type is checked, not assumed.** The namespace prefix must
agree with `PredicateSpec.subject`, and a mismatch refuses. A proposal in
`org:acme` asserting `allergy` — whose subject is `Patient` — is a caller error,
and it is the kind that otherwise writes a real fact about the wrong kind of
thing.

**`canonical_name` is display, never a key.** It is set from the first surface
form seen for an entity and never read back for matching. That is what keeps
this honest: "Joan", "Mrs Ellery" and "the patient" proposed under
`patient:7781` are one entity because the *namespace* is one, not because the
strings were judged similar.

`resolve` gains the predicate's spec so it can supply `type`. The protocol
becomes:

```python
async def resolve(
    self,
    subject: str,
    *,
    tenant_id: TenantId,
    namespace: Namespace,
    expected_type: str,        # NEW - PredicateSpec.subject
) -> EntityId: ...
```

`subject` stays in the signature although nothing matches on it: it is what
`canonical_name` is set from on creation, and removing it would make a first
mention nameless in the graph and in every reviewer screen that reads it.

### Explicitly out of scope

- **Name matching.** Deferred to a later ADR, which needs an eval set and a
  measured precision/recall curve. Picking a similarity threshold today would be
  picking it from nothing.
- **`entity_ref` objects.** `MEMORY_ENGINE.md` §2.1 already settles them: a
  predicate declared `{type: entity_ref}` means the object string *is* an
  `EntityId`. Objects need no resolution; subjects do.
- **Merging two entities later discovered to be one.** Real, and a supersession
  problem rather than a resolution one.

## Alternatives rejected

- **Use the surface form as the id.** `deps.py` has argued against this since
  S5.6 and the argument holds: it splits one patient across three spellings,
  every incumbent lookup then returns nothing, and "no incumbent" reads as "this
  is a novel fact" — so the system writes a duplicate and reports confidence in
  it. A wrong answer that looks like a normal one.
- **Fuzzy-match `canonical_name` now, tune later.** The failure is asymmetric
  and the wrong half is silent. A false *split* writes a duplicate; a false
  *merge* attaches one person's allergy to another person's record, and no
  invariant in this system would catch it — I1 is satisfied (the span is real),
  I2 is satisfied (one live value per predicate, on the wrong entity). For a
  clinical pack that is the worst available failure, and it would be introduced
  by a threshold nobody measured.
- **Add a `namespace` column to `entity` with a unique index on
  `(tenant_id, namespace)`, and keep a random id.** Works, and was close. It
  costs a migration, a `RETURNING` round trip, and makes the id unknowable until
  the store has answered — where derivation makes it a pure function available
  before any I/O. Derivation also matches the scheme `scripts/seed_demo_tenant.py`
  already uses. Reconsider if a subject ever needs to exist under two
  namespaces, which this ADR does not support.
- **Resolve inside `retrieve_incumbents`.** Buries a matching decision with an
  audit story inside a retrieval call, which is the objection `deps.py` raised
  when it made this a protocol in the first place.
- **Ship no resolver and keep refusing.** The honest status quo, and it is what
  blocks CHECKPOINT B — the gate that decides whether anything downstream is
  worth building. The cost of the wrong resolver is a wrong write; the cost of
  no resolver is not learning whether the scoring works at all.

## Consequences

- **`EntityResolver.resolve` gains `expected_type`**, and `_decide_one` passes
  `spec.subject`. One call site.
- **An entity writer is needed and does not exist.** The insert is
  `INSERT INTO entity (id, tenant_id, type, canonical_name) VALUES (...)
  ON CONFLICT (id) DO NOTHING` — the grants already permit it. Where it lives is
  an implementation choice this ADR does not make; it is Postgres-specific, like
  the applier, and `guardmem_core.pipeline` may not import a concrete store.
- **The seed's entity ids change, and a re-seed is required once.**
  `scripts/seed_demo_tenant.py` derives the patient from the key
  `"patient-7781"`; the resolver derives from the namespace `"patient:7781"` and
  from the tenant *uuid* rather than its slug. The seed adopts core's derivation
  so there is one scheme rather than two — and because the derived id is the
  primary key, an existing database keeps its old rows under
  `ON CONFLICT DO NOTHING`. `make dev-reset && make seed`.
- **CHECKPOINT B is unblocked on this dependency.** The gate's corpus comes from
  the seed transcript, which is one subject under one namespace — the case this
  ADR resolves deterministically.
- **`MCP_INTEGRATION.md` §2.2 is amended in the same commit**, per `RULES.md`
  §8. `hints.subject` is `{"type": "string"}` with no description; it gains one
  saying it is an entity id. `Proposal.subject_hint` currently reaches the
  resolver as the *surface form* argument — `proposal.subject_hint or
  candidate.subject` — which under this decision would silently do nothing, and
  is a bug this ADR creates if it is not fixed with it.
- **`ARCHITECTURE.md` §5 is amended** to record that `:Entity.id` is derived
  from `(tenant_id, namespace)` for namespace-bound subjects, rather than being
  an opaque key.
- **A `session:` or `org:` namespace cannot carry a clinical assertion**, since
  neither names a declared entity type. That is the intended reading — a session
  is not something one holds an allergy about — but it means a deployment whose
  namespaces do not follow `<type>:<id>` must supply `hints.subject` on every
  proposal, or implement its own resolver. `scope_of_namespace` already carries
  the matching caveat for an unrecognised prefix.
- **This ADR is reversible at the cost of a re-seed**, which is the property
  that makes it appropriate to take now rather than after the gate. The matching
  problem it defers is the expensive one, and it is deferred with the data that
  would settle it still uncollected.
