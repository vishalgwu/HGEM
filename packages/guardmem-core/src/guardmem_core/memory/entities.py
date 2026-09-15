"""Which entity a candidate is about.  ADR-0008

`MemoryCandidate.subject` is a surface form - "Joan Ellery", as the speaker said
it. `StoredAssertion.subject_id` is an `EntityId`, and `assertion.subject_id` is
`UUID NOT NULL REFERENCES entity(id)`. This is what turns one into the other,
and it is the last of the three `Deps` members that had no producer.

**It binds, it does not match.** ADR-0008 in one line. No string here is ever
compared to another string for similarity: `resolve` reads an explicit binding -
the caller's `hints.subject`, or a namespace that names one subject - and
refuses when it has neither. The surface form is recorded as `canonical_name`
and never read back for matching.

The reason is an asymmetry rather than a preference. A false *split* writes a
duplicate, which is visible and recoverable. A false *merge* attaches one
person's allergy to another person's record, and **no invariant in this system
would catch it**: I1 is satisfied because the span is real, I2 is satisfied
because there is one live value per predicate - on the wrong entity. For a
clinical pack that is the worst failure available, and a similarity threshold
nobody has measured is not a good way to acquire it. The matching resolver
arrives behind the same Protocol when there is an eval to set a threshold from.

**The id is derived, not looked up.**

    uuid5(NAMESPACE_URL, f"guardmem/{tenant_id}/entity/{namespace}")

A pure function of `(tenant, namespace)`, which is the rule every replay path in
this repository already follows: the id is known before any I/O, creation is
`ON CONFLICT DO NOTHING` and therefore idempotent, and two concurrent callers
cannot mint rival ids for one subject. `scripts/seed_demo_tenant.py` derives its
ids the same way and now derives *this* one from here, so there is one scheme
rather than two.

**Writing the row is not optional and that is the foreign key's doing.** The
assertion cannot commit until its entity exists, so `resolve` inserts. It is the
one thing in this module that touches Postgres, which is why the module sits in
`memory/` and not in `pipeline/` - `guardmem_core.pipeline` may not import a
concrete store, and an import-linter contract enforces it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, uuid5

import asyncpg

from guardmem_core.errors import StoreUnavailable, ValidationRejected
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.types import EntityId

if TYPE_CHECKING:
    from guardmem_core.types import Namespace, TenantId

__all__ = ["NamespaceEntityResolver", "derive_entity_id", "subject_type_of"]

# `INSERT ... ON CONFLICT DO NOTHING` rather than an upsert. A second proposal
# under the same namespace must not rewrite `canonical_name`: the first surface
# form seen is as good as the fourth, and rewriting it would make the graph's
# display name depend on which proposal happened to arrive last.
_INSERT_ENTITY: Final = """
    INSERT INTO entity (id, tenant_id, type, canonical_name)
    VALUES ($1::uuid, $2::uuid, $3, $4)
    ON CONFLICT (id) DO NOTHING
"""


def derive_entity_id(tenant_id: TenantId, namespace: Namespace) -> EntityId:
    """The entity id for a subject-bound namespace.  ADR-0008

    Args:
        tenant_id: The owning tenant. Part of the derivation, so the same
            namespace string under two tenants is two entities - which is the
            isolation property the whole product exists to keep.
        namespace: The subject-bound namespace, e.g. `"patient:7781"`.

    Returns:
        The id, as a UUID string. Deterministic: same inputs, same id, forever.

    Not private, because `scripts/seed_demo_tenant.py` calls it. That is the
    point of it being here - the seed used to derive the patient from its own
    key `"patient-7781"` under its own slug, and two derivations of one id is
    the shape `RULES.md`'s conventions warn about.
    """
    return EntityId(str(uuid5(NAMESPACE_URL, f"guardmem/{tenant_id}/entity/{namespace}")))


def subject_type_of(namespace: Namespace) -> str | None:
    """The entity type a namespace names, if it names one.

    Args:
        namespace: The candidate's namespace.

    Returns:
        The prefix of a `<type>:<id>` namespace, or `None` when there is no
        prefix at all. **Not checked against the ontology here** - that is
        `resolve`'s job, because only the caller knows which type the predicate
        expects and a mismatch has to name both sides.

    `"patient:7781"` gives `"patient"`. `"scratch"` gives `None`. A namespace
    with several colons keeps only the first segment, since `<type>:<id>` is the
    documented shape and an id containing a colon is still one id.
    """
    prefix, separator, rest = namespace.partition(":")
    if not separator or not prefix or not rest:
        return None
    return prefix


class NamespaceEntityResolver:
    """An `EntityResolver` that reads bindings and never compares names.

    Structurally an `EntityResolver`, not a subclass of one, for the reason
    S1.7 made these contracts Protocols.

    Bound to a pool rather than a connection: `resolve` is called once per
    candidate and the pipeline scores candidates concurrently, so a shared
    connection would serialise them behind each other's transactions.
    """

    def __init__(self, pool: asyncpg.Pool, *, timeout_s: float) -> None:
        """Bind the pool this resolver writes entities through.

        Args:
            pool: The process-wide pool. `RULES.md` §2.2 puts pool creation in
                lifespan, and this is a consumer of one rather than an owner.
            timeout_s: Ceiling on each round trip, from
                `settings.db_timeout_s`. Passed explicitly rather than left to
                the pool default, per §2.2.
        """
        self._pool = pool
        self._timeout_s = timeout_s

    async def resolve(
        self,
        subject: str,
        *,
        tenant_id: TenantId,
        namespace: Namespace,
        expected_type: str,
    ) -> EntityId:
        """Bind `subject` to an entity, creating the row if it is new.

        Args:
            subject: The surface form, or the caller's `hints.subject` where one
                was given. **Recorded as `canonical_name`, never matched on.**
            tenant_id: Whose entity graph this is.
            namespace: The isolation scope, and the binding this reads.
            expected_type: The entity type the predicate declares its subject to
                be - `PredicateSpec.subject`.

        Returns:
            The entity id. The row is guaranteed to exist when this returns,
            which is what the assertion's foreign key needs.

        Raises:
            ValidationRejected: the namespace does not name a subject, or names
                one of a different type than the predicate expects. Both are
                caller errors with a stated remedy, and both are refusals rather
                than guesses - see the module docstring on why a wrong guess
                here is unrecoverable. Raised **without** a `trace_id`: the
                `EntityResolver` protocol carries none, and `_score_one`
                attributes the failure to its candidate, which is where a reader
                looks first. Widening the protocol for one error path was not
                worth it.
            StoreUnavailable: the entity row could not be written.

        **`hints.subject` is handled by the caller, not here.** `Proposal
        .subject_hint` reaches this as `subject`, and a hint that is already an
        `EntityId` is a binding this method would have to detect by shape - the
        ambiguity ADR-0008 resolves by documenting the field rather than by
        sniffing it. The pipeline passes the hint through as the surface form;
        a gateway that wants the id route reads it before calling.
        """
        declared = subject_type_of(namespace)
        if declared is None:
            raise ValidationRejected(
                f"namespace {namespace!r} does not name a subject, so there is nothing "
                f"to attach {subject!r} to. Entity resolution binds rather than matches "
                "(ADR-0008): use a '<type>:<id>' namespace, or pass hints.subject."
            )
        if declared.casefold() != expected_type.casefold():
            raise ValidationRejected(
                f"namespace {namespace!r} names a {declared!r}, and this predicate "
                f"declares its subject to be a {expected_type!r}. Writing it anyway "
                "would attach a real fact to the wrong kind of thing."
            )
        entity_id = derive_entity_id(tenant_id, namespace)
        await self._ensure(entity_id, tenant_id, expected_type, subject)
        return entity_id

    async def _ensure(
        self, entity_id: EntityId, tenant_id: TenantId, entity_type: str, canonical_name: str
    ) -> None:
        """Insert the entity row if it is not already there.

        Args:
            entity_id: The derived id.
            tenant_id: The owning tenant.
            entity_type: `entity.type`, which is `NOT NULL`.
            canonical_name: The first surface form seen, which is also
                `NOT NULL` - so a blank subject is stored as the namespace
                rather than failing the insert on a constraint that would tell
                the caller nothing.

        Raises:
            StoreUnavailable: the write failed.

        Inside `tenant_transaction` because `entity` carries row-level security
        and `SET LOCAL app.tenant_id` is what satisfies it. Sharing that helper
        with the store and the relay is deliberate: a second, subtly different
        copy of the tenant-scoping is the bug it exists to prevent.
        """
        try:
            async with tenant_transaction(self._pool, tenant_id, timeout_s=self._timeout_s) as conn:
                await conn.execute(
                    _INSERT_ENTITY,
                    str(entity_id),
                    str(tenant_id),
                    entity_type,
                    canonical_name or str(entity_id),
                    timeout=self._timeout_s,
                )
        except asyncpg.PostgresError as exc:
            raise StoreUnavailable(f"could not write entity {entity_id}: {exc}") from exc
