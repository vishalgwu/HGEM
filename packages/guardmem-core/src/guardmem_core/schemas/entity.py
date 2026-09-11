"""Entities, stored assertions and graph edges.  BUILD_NOTEBOOK.md S1.6

`MEMORY_ENGINE.md` §0 does not define these - it specifies the *pipeline*, and
these are what the pipeline writes. Their shape comes from `ARCHITECTURE.md` §5,
which gives the `assertion` table in SQL and the entity graph in Cypher, and
from `MEMORY_ENGINE.md` §2.3-2.4 for supersession and merge semantics.

Two places where the model deliberately does not mirror the SQL one-to-one:

- **`provenance` is a list.** The table carries single `source_hash` /
  `source_span` columns, but §2.4 says a merge "appends the new `Provenance`",
  and §3.2's `S_cor` term is computed from the number of independent sources.
  A single-provenance assertion cannot represent a corroborated fact at all, so
  the domain model holds the list and S3.1 decides how it is stored.
- **No `embedding`.** The table has one; carrying 1024 floats on the domain
  object would put them into every audit payload, review task and MCP response
  for the benefit of one writer, and `RULES.md` §1.5's stance on what leaves the
  system through logs and spans argues the same way. How the vector store
  receives the vector is S1.7's decision, taken with the protocol in front of it.

`Predicate` is listed for this module by `PROJECT_TREE.md` and is not here. Its
fields - cardinality, impact, `min_source_tier`, `requires_corroboration` - are
specified by `MEMORY_ENGINE.md` §2.1 as *ontology* content, and S3.5 builds
`schemas/ontology.py` with the loader that validates them. Defining half of it
here would give the ontology two homes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.receipt import Provenance
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

__all__ = ["Cardinality", "Edge", "Entity", "StoredAssertion"]


class Cardinality(StrEnum):
    """How many live values a predicate may hold at once.

    From `MEMORY_ENGINE.md` §0. This is the single most consequential property
    in the ontology: `ONE` is what makes invariant I2 checkable, what turns a
    second value into a `CARDINALITY` conflict "regardless of NLI" (§2.2b), and
    what makes the review UI's supersession warning mandatory.
    """

    ONE = "one"  # employer, primary_dx, account_owner  -> new value supersedes
    MANY = "many"  # allergy, hobby, matter_tag         -> accumulates
    ONE_PER_TIME = "one_per_time"  # address, role      -> one valid per interval


class Entity(GMModel):
    """A node in the entity graph.

    `ARCHITECTURE.md` §5: `(:Entity {id, tenant_id, type, canonical_name})`.

    Attributes:
        entity_id: Stable id for the node.
        tenant_id: Owning tenant.
        type: Entity type from the ontology, e.g. `"Patient"`, `"Provider"`.
        canonical_name: The resolved name, as distinct from any surface form a
            candidate happened to use.
    """

    entity_id: EntityId
    tenant_id: TenantId
    type: str
    canonical_name: str


class StoredAssertion(GMModel):
    """A durable fact: bitemporal, sourced, and never deleted.

    `ARCHITECTURE.md` §0: "Nothing is deleted. Contradiction resolves by
    supersession + tombstone." The two time axes are what make "what did the
    agent believe on 2026-03-14?" a normal query: `valid_from`/`valid_to` is
    world time, `recorded_at`/`retracted_at` is system time.

    Attributes:
        assertion_id: This fact's id.
        tenant_id: Owning tenant; the value RLS is set from.
        namespace: Isolation scope.
        subject_id: The resolved entity, not a surface form. This is the
            difference from `MemoryCandidate.subject` - entity resolution has
            happened by the time an assertion exists.
        predicate: Ontology predicate.
        object: The stored value.
        confidence: `C` at the time of the write.
        risk: `R` at the time of the write.
        valid_from: World-time this became true. Required: a fact with no start
            cannot be ordered against a competing one, and §2.3's supersession
            sets `prior.valid_to = candidate.valid_from`.
        valid_to: World-time it stopped. `None` means currently believed, which
            is the filter every read path applies.
        recorded_at: System-time the belief was recorded.
        retracted_at: System-time it was retracted, if it was.
        superseded_by: The assertion that replaced this one.
        provenance: Every span supporting this fact, newest appended by a merge.
            At least one, always - `RULES.md` non-negotiable #1 makes a write
            without one a P0 bug, enforced here, by a `NOT NULL` constraint at
            S3.1, and by a property test.
        corroboration_count: Number of **independent** sources. Deliberately not
            `len(provenance)`: two spans from the same document are two
            provenance records and one source, and §3.2's `S_cor` is a function
            of independent sources, so conflating them would inflate confidence
            exactly where a poisoning attempt would want it inflated.
        trace_id: The proposal that produced it.
        visible: Set true only after both sides of the dual write land
            (`ARCHITECTURE.md` §2.4). Readers filter on it, which is what makes
            a partial write unretrievable rather than briefly wrong. Defaults
            false because S3.2 inserts it false and the outbox relay flips it.
    """

    assertion_id: AssertionId
    tenant_id: TenantId
    namespace: Namespace
    subject_id: EntityId
    predicate: str
    object: ObjectValue
    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime
    retracted_at: datetime | None = None
    superseded_by: AssertionId | None = None
    provenance: list[Provenance] = Field(min_length=1)
    corroboration_count: int = Field(default=1, ge=1)
    trace_id: TraceId
    visible: bool = False

    @model_validator(mode="after")
    def _validity_interval_must_not_be_inverted(self) -> StoredAssertion:
        """Reject a stored fact whose validity interval runs backwards.

        The same reasoning as on `MemoryCandidate`, and it matters more here:
        supersession writes `prior.valid_to = candidate.valid_from`, so an
        out-of-order pair would produce an incumbent that was never valid and a
        point-in-time query that returns nothing for a window the fact was
        actually believed in.

        Returns:
            The assertion unchanged, once the interval is coherent.

        Raises:
            ValueError: if `valid_to` precedes `valid_from`.
        """
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(
                f"valid_to {self.valid_to.isoformat()} precedes valid_from "
                f"{self.valid_from.isoformat()}; the validity interval is "
                "half-open [valid_from, valid_to) and cannot run backwards"
            )
        return self


class Edge(GMModel):
    """One `ASSERTS` relationship in the entity graph.

    `ARCHITECTURE.md` §5:
    `(:Entity)-[:ASSERTS {assertion_id, predicate, confidence, valid_from,
    valid_to, trace_id}]->(:Entity|:Literal)`. The fields below are those
    properties plus the two endpoints.

    The Cypher target is an entity *or* a literal, and this model does not carry
    two nullable fields to say which. `object` is an `ObjectValue`, exactly as on
    the candidate and the assertion, and the ontology says how to read it - a
    predicate declared `{type: entity_ref}` means the string is an `EntityId`.
    That is already how `MEMORY_ENGINE.md` §2.1 resolves the same ambiguity
    everywhere else, and one rule is better than two.

    Attributes:
        assertion_id: The assertion this edge materialises.
        subject_id: Source node.
        predicate: Edge label within `ASSERTS`.
        object: Target - a literal value, or an entity ref per the ontology.
        confidence: `C` carried onto the edge for ranking.
        valid_from: World-time the edge became true.
        valid_to: World-time it stopped; `None` means live.
        trace_id: The proposal that produced it.
    """

    assertion_id: AssertionId
    subject_id: EntityId
    predicate: str
    object: ObjectValue
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime
    valid_to: datetime | None = None
    trace_id: TraceId
