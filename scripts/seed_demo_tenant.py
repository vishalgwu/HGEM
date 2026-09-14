"""Seed the demo tenant.  BUILD_NOTEBOOK.md S3.6

`make seed`. One tenant, one patient plus the entities its facts point at, and
the twenty-eight assertions the shipped transcript supports - written through
the real write path, released by the real relay, and two of them superseded so
the demo actually contains a retired fact.

This is the END OF DAY 3 CHECK in executable form: "you can write an assertion
to Postgres and read it back with provenance." It touches every piece Day 3
built - `StoreRouter`, `PgVectorStore`, the outbox, `OutboxRelay`,
`NetworkXGraphStore` and the clinical ontology - and it is the first consumer of
the last of those.

**Idempotence is by construction, not by checking first.** Every id is a `uuid5`
of the demo slug and a stable key, so a second run collides with the first at
every insert and every `ON CONFLICT DO NOTHING` does nothing. That is the same
property S3.2 and S3.3 built for the relay's replay, reaching its second caller
unchanged - which is the useful signal here: the seed needed no idempotence
machinery of its own.

**Seeded vectors are reproducible noise.** There is no `Embedder` in the package
until S9.1, so this uses a deterministic hash embedder. Identical text embeds
identically and different text lands somewhere unrelated; *semantic* similarity
is not modelled at all. Nothing about retrieval quality may be measured against
seeded data - `RULES.md` §5 puts that in the nightly eval suite, where there is
a labelled corpus.

**Confidence is a placeholder and says so.** Layer 3 does not exist, so nothing
here is scored; `risk` is the impact floor `MEMORY_ENGINE.md` §3.3 declares for
the predicate's declared impact, which is the one part of the number that is
real. Do not calibrate anything against these values.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Final
from uuid import NAMESPACE_URL, uuid5

import asyncpg

from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.relay import OutboxRelay
from guardmem_core.memory.router import StoreRouter
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.pipeline.l1_extract.span_linker import link_span
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.ontology import PredicateSpec, load_ontology
from guardmem_core.schemas.receipt import Provenance
from guardmem_core.schemas.turn import Turn
from guardmem_core.settings import get_settings
from guardmem_core.types import AssertionId, EntityId, TenantId, TraceId

# A sibling module, resolved because `python scripts/seed_demo_tenant.py` puts
# this directory at the head of `sys.path`. An explicit `sys.path.insert` stood
# here until the S3.6 audit measured it: it was redundant on the only path that
# runs this file, and it forced every import below the statement.
from scripts.demo_tenant_data import (
    INTAKE_AT,
    MOVE_CALL,
    NAMESPACE,
    OBJECT_ENTITIES,
    SEED_FACTS,
    SUPERSESSIONS,
    TENANT_SLUG,
    TRANSCRIPT,
    SeedFact,
)

PACK: Final = "clinical"
PATIENT_KEY: Final = "patient-7781"
TRACE: Final = TraceId("tr_seed")

# Not scored. See the module docstring.
PLACEHOLDER_CONFIDENCE: Final = 0.80


def identifier(kind: str, key: str) -> str:
    """Derive a stable UUID for one seeded row.

    Args:
        kind: Row family, e.g. `"assertion"`. Keeps an entity and an assertion
            with the same key from colliding.
        key: The stable slug.

    Returns:
        A UUID string, identical on every run.

    This is the whole idempotence story. `uuid4` here would make a second `make
    seed` insert a parallel copy of everything, and the DONE WHEN is precisely
    that it does not.
    """
    return str(uuid5(NAMESPACE_URL, f"guardmem/{TENANT_SLUG}/{kind}/{key}"))


def build_assertion(fact: SeedFact, spec: PredicateSpec, turns: dict[str, Turn]) -> StoredAssertion:
    """Turn one `SeedFact` into an assertion with a real provenance span.

    Args:
        fact: What to seed.
        spec: The predicate's declaration, from the clinical pack.
        turns: Every turn in the shipped transcript, by id.

    Returns:
        The assertion, invisible, with one citation.

    Raises:
        ValueError: the quote is not in that turn, or the source is weaker than
            the predicate's declared floor. Both stop the seed. A quote that is
            not there would otherwise become a fabricated offset in the one
            database every later demo reads from, and a source below the floor
            is a fact the ontology would refuse - discovering that here, in
            data, is cheaper than discovering it in S4.1's schema gate.
    """
    turn = turns[fact.turn_id]
    match = link_span(fact.quote, turn.text)
    if match is None:
        raise ValueError(f"{fact.key}: {fact.quote!r} is not in turn {fact.turn_id}")
    if not fact.tier.at_least(spec.min_source_tier):
        raise ValueError(
            f"{fact.key}: source tier {fact.tier} is weaker than {fact.predicate}'s "
            f"declared minimum {spec.min_source_tier} (ontology/{PACK}.yaml)"
        )
    return StoredAssertion(
        assertion_id=AssertionId(identifier("assertion", fact.key)),
        tenant_id=TenantId(identifier("tenant", TENANT_SLUG)),
        namespace=NAMESPACE,
        subject_id=EntityId(identifier("entity", PATIENT_KEY)),
        predicate=fact.predicate,
        object=fact.object,
        confidence=PLACEHOLDER_CONFIDENCE,
        risk=spec.impact.risk_floor,
        valid_from=fact.valid_from,
        recorded_at=INTAKE_AT,
        provenance=[
            Provenance(
                source_hash=f"sha256:{hashlib.sha256(turn.text.encode()).hexdigest()}",
                source_span=match.span,
                source_tier=fact.tier,
                verbatim=match.text,
                alignment=match.alignment,
                captured_at=turn.captured_at or INTAKE_AT,
            )
        ],
        trace_id=TRACE,
    )


async def create_tenancy(owner: asyncpg.Connection) -> None:
    """Create the tenant and its entities, as the owner.

    Args:
        owner: A connection as the table owner.

    `guardmem_app` holds `SELECT` on `tenant` and nothing more, so a tenant is
    the deployment's to create and never the application's - which is why this
    script wants `GM_DATABASE_URL` and not the least-privilege DSN the stores
    use. `set_config` before the entity inserts because `entity` has `FORCE ROW
    LEVEL SECURITY` and its policy carries no explicit `WITH CHECK`, so Postgres
    reuses the `USING` expression and the owner cannot write a row for a tenant
    other than the one currently set.
    """
    tenant = identifier("tenant", TENANT_SLUG)
    await owner.execute(
        "INSERT INTO tenant (id, slug) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING",
        tenant,
        TENANT_SLUG,
    )
    await owner.execute("SELECT set_config('app.tenant_id', $1, false)", tenant)
    entities = (("patient-7781", "Patient", "Joan Ellery"), *OBJECT_ENTITIES)
    for key, kind, name in entities:
        await owner.execute(
            "INSERT INTO entity (id, tenant_id, type, canonical_name)"
            " VALUES ($1::uuid, $2::uuid, $3, $4) ON CONFLICT (id) DO NOTHING",
            identifier("entity", key),
            tenant,
            kind,
            name,
        )


async def supersede_the_moved_facts(store: PgVectorStore) -> int:
    """Retire the address and pharmacy the second call replaced.

    Args:
        store: The demo tenant's vector store.

    Returns:
        How many rows this run retired. Zero on a re-run.

    `ConcurrencyConflict` is caught and counted as "already done", which is the
    one place this script is not idempotent by construction: `supersede` is an
    `UPDATE ... WHERE valid_to IS NULL` and the second run matches nothing. The
    error says so in as many words, and for a seed that is the right reading -
    note that it would also be the reading if somebody else had retired the row,
    which is a distinction only a real caller needs to make.
    """
    retired = 0
    for old_key, new_key in SUPERSESSIONS:
        successor = next(fact for fact in SEED_FACTS if fact.key == new_key)
        try:
            await store.supersede(
                AssertionId(identifier("assertion", old_key)),
                AssertionId(identifier("assertion", new_key)),
                successor.valid_from,
            )
        except ConcurrencyConflict:
            continue
        retired += 1
    return retired


async def seed() -> None:
    """Write the demo tenant, release it, and report what is there.

    Raises:
        ValueError: a fact's quote is missing, its source is too weak, or its
            predicate is not in the clinical pack.
        StoreUnavailable: Postgres is unreachable.
    """
    settings = get_settings()
    # `Settings.database_url` carries SQLAlchemy's driver marker for Alembic;
    # asyncpg does not understand it.
    dsn = libpq_dsn(str(settings.database_url))
    ontology = load_ontology(PACK)
    # Annotated `str`, not inferred `TurnId`: `dict` is invariant in its key,
    # so an inferred `dict[TurnId, Turn]` is not a `dict[str, Turn]` and
    # `build_assertion` would not accept it. `SeedFact.turn_id` is a plain
    # `str` because these are literals in a data file, not ids the pipeline
    # minted. `mypy --strict` found this, which is the argument for the
    # Makefile change that put `scripts/` in its scope.
    turns: dict[str, Turn] = {turn.turn_id: turn for turn in (*TRANSCRIPT, *MOVE_CALL)}

    assertions = []
    for fact in SEED_FACTS:
        spec = ontology.predicate(fact.predicate)
        if spec is None:
            raise ValueError(f"{fact.key}: {fact.predicate!r} is not in ontology/{PACK}.yaml")
        assertions.append(build_assertion(fact, spec, turns))

    owner = await asyncpg.connect(dsn)
    try:
        await create_tenancy(owner)
    finally:
        await owner.close()

    pool = await create_pool(dsn, min_size=1, max_size=4)
    try:
        tenant = TenantId(identifier("tenant", TENANT_SLUG))
        store = PgVectorStore(
            pool, HashEmbedder(), tenant_id=tenant, timeout_s=settings.store_timeout_s
        )
        await StoreRouter(store, tenant_id=tenant).write(assertions)
        relay = OutboxRelay(pool, NetworkXGraphStore(), timeout_s=settings.store_timeout_s)
        released = 0
        while (run := await relay.run_once()).claimed:
            released += run.dispatched
        retired = await supersede_the_moved_facts(store)
    finally:
        await pool.close()

    print(f"tenant      {TENANT_SLUG} ({tenant})")
    print(f"turns       {len(TRANSCRIPT)} intake + {len(MOVE_CALL)} follow-up")
    print(f"assertions  {len(assertions)} submitted, {released} released this run")
    print(f"superseded  {retired} this run")
    print("re-run me: released and superseded go to zero, the row counts do not move")


if __name__ == "__main__":
    asyncio.run(seed())
