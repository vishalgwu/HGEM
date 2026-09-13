"""How a `StoredAssertion` is spelled as Postgres rows, and read back.  S3.2

Split out of `pgvector_store.py` along a real seam rather than to satisfy a line
count. This module knows the *shape* of the `assertion` and `provenance` tables
from `0001_initial` - column order, the JSONB encoding of `object`, the
`INT4RANGE` decomposition of a span - and nothing about connections,
transactions, tenancy or SQL statements. The store knows those and nothing about
column order. When S3.6 or a Qdrant backend changes one, it should not have to
read the other.

The one thing worth arguing about lives here too: what text an assertion is
embedded from. See `embed_text`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, uuid5

from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from asyncpg import Record

__all__ = [
    "ASSERTION_COLUMNS",
    "EMBEDDING_DIM",
    "INSERT_ASSERTION",
    "INSERT_PROVENANCE",
    "SELECT_PROVENANCE",
    "assertion_from_row",
    "assertion_params",
    "embed_text",
    "provenance_from_row",
    "provenance_params",
]

# `assertion.embedding` is `VECTOR(1024)` (`ARCHITECTURE.md` §5). Checked in the
# store rather than left to the driver because the error a dimension mismatch
# raises is about a column, while the actual fault is an embedder wired to the
# wrong model - `GM_EMBED_MODEL` pointing at a 1536- or 768-dimension pin.
EMBEDDING_DIM: Final = 1024

# The projection every read uses, in the order `assertion_params` supplies and
# `assertion_from_row` consumes. A module constant so the two cannot drift, and
# so no caller-supplied string ever reaches a SELECT list.
ASSERTION_COLUMNS: Final = (
    "id, tenant_id, namespace, subject_id, predicate, object_json, "
    "confidence, risk, valid_from, valid_to, recorded_at, retracted_at, "
    "superseded_by, corroboration_count, trace_id, visible"
)


def embed_text(assertion: StoredAssertion) -> str:
    """Render the text an assertion's vector is computed from.

    Args:
        assertion: The fact being written.

    Returns:
        A canonical rendering of the predicate and object.

    The subject is deliberately absent. `StoredAssertion.subject_id` is an
    `EntityId` - entity resolution has already happened - and a UUID contributes
    nothing an embedding model can use. The canonical name lives on `entity`,
    and joining it in would make the vector depend on a row this store does not
    own and cannot re-embed when it changes.

    `provenance[*].verbatim` is the other candidate, and it was considered: it is
    natural language, so it would very likely retrieve better than
    `"allergy: penicillin"` does. It is rejected because it is a *list*. A fact
    corroborated by three sources would have three texts and one vector slot, so
    the store would have to pick one or average them - and either way the same
    fact embeds differently depending on how many times it happened to be said,
    which is exactly the axis `MEMORY_ENGINE.md` §3.2 wants `S_cor` to carry and
    retrieval not to. Revisit with the eval harness at S22, which is the first
    point there is a number to compare.
    """
    return f"{assertion.predicate}: {_render(assertion.object)}"


def _render(value: str | float | bool | dict[str, object]) -> str:
    """Flatten an `ObjectValue` to something worth embedding.

    `json.dumps` on a bare string would embed the quotes, and on a dict it gives
    the model braces and colons to spend attention on. Structured objects are
    rendered as their values, which is what carries the meaning.
    """
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    return str(value)


def assertion_params(
    assertion: StoredAssertion, tenant_id: TenantId, vector: list[float]
) -> tuple[object, ...]:
    """Flatten one assertion into the INSERT's positional parameters.

    Args:
        assertion: The fact being written.
        tenant_id: The store's tenant, which overrides the assertion's own. They
            should agree; if they do not, the store's is authoritative because
            it came from an authenticated request and the model's came from
            whatever built it.
        vector: Its embedding.

    Returns:
        Values in `ASSERTION_COLUMNS` order, followed by the embedding.
        `visible` is NOT among them - the statement writes `false` literally, so
        there is no parameter a caller could set it through.
    """
    return (
        assertion.assertion_id,
        tenant_id,
        assertion.namespace,
        assertion.subject_id,
        assertion.predicate,
        json.dumps(assertion.object),
        assertion.confidence,
        assertion.risk,
        assertion.valid_from,
        assertion.valid_to,
        assertion.recorded_at,
        assertion.retracted_at,
        assertion.superseded_by,
        assertion.corroboration_count,
        assertion.trace_id,
        vector,
    )


def provenance_params(assertion: StoredAssertion) -> list[tuple[object, ...]]:
    """Flatten one assertion's citations, with ids derived from their content.

    Args:
        assertion: The fact whose provenance is being written.

    Returns:
        One parameter tuple per citation, span decomposed into the two bounds
        `int4range($n, $m)` takes.

    The id is a `uuid5` of `(assertion_id, source_hash, span)` rather than a
    fresh `uuid4`, and that is what lets the store replay a write on this table.
    `Provenance` carries no id of its own - it is a value, not an entity - so a
    random id would make every replay insert a duplicate citation, and
    `corroboration_count` would start disagreeing with the evidence it exists to
    summarise. Two different spans in the same document stay two rows, which is
    correct: they are two citations.
    """
    rows: list[tuple[object, ...]] = []
    for citation in assertion.provenance:
        start, end = citation.source_span
        name = f"{assertion.assertion_id}/{citation.source_hash}/{start}-{end}"
        rows.append(
            (
                str(uuid5(NAMESPACE_URL, name)),
                assertion.assertion_id,
                citation.source_hash,
                start,
                end,
                citation.source_tier.value,
                citation.verbatim,
                citation.alignment,
                citation.captured_at,
            )
        )
    return rows


def provenance_from_row(row: Record) -> Provenance:
    """Rebuild a `Provenance` from a provenance row.

    The row is expected to have decomposed the range already - `lower()` and
    `upper()` in the SELECT - because asyncpg hands back an `asyncpg.Range`
    otherwise and the domain model wants a plain half-open pair. Postgres
    normalises `int4range` to that same half-open form, so the two conventions
    are the same convention.
    """
    return Provenance(
        source_hash=row["source_hash"],
        source_span=(row["span_start"], row["span_end"]),
        source_tier=SourceTier(row["source_tier"]),
        verbatim=row["verbatim"],
        alignment=row["alignment"],
        captured_at=row["captured_at"],
    )


def assertion_from_row(row: Record, provenance: list[Provenance]) -> StoredAssertion:
    """Rebuild a `StoredAssertion` from one row and its citations.

    Args:
        row: A row projecting `ASSERTION_COLUMNS`.
        provenance: Every citation for it. At least one, always - the model
            enforces `min_length=1` and so does the deferred trigger, so an
            empty list here means the join lost rows rather than that the fact
            is unsourced.

    Returns:
        The domain object, with the `NewType` ids reapplied.

    The `str()` calls are not decoration. asyncpg returns `uuid.UUID` for a UUID
    column and the domain ids are `NewType(..., str)`, so without them the model
    would carry `UUID` objects that compare unequal to every id the pipeline
    holds, serialise differently in receipts, and pass `mypy` because `NewType`
    erases at runtime.
    """
    return StoredAssertion(
        assertion_id=AssertionId(str(row["id"])),
        tenant_id=TenantId(str(row["tenant_id"])),
        namespace=Namespace(row["namespace"]),
        subject_id=EntityId(str(row["subject_id"])),
        predicate=row["predicate"],
        object=json.loads(row["object_json"]),
        confidence=row["confidence"],
        risk=row["risk"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        recorded_at=row["recorded_at"],
        retracted_at=row["retracted_at"],
        superseded_by=(
            None if row["superseded_by"] is None else AssertionId(str(row["superseded_by"]))
        ),
        provenance=provenance,
        corroboration_count=row["corroboration_count"],
        trace_id=TraceId(row["trace_id"]),
        visible=row["visible"],
    )


# --- the statements those tuples fill -------------------------------------
#
# The SQL lives beside the column constant and the mapping functions rather than
# in the store, because these three are the same fact written three ways:
# `ASSERTION_COLUMNS` names the columns, `assertion_params` supplies them in that
# order, and `INSERT_ASSERTION` numbers the placeholders to match. Adding a
# column to `assertion` should be one file to edit, not two that fail at runtime
# when only one of them is remembered.
#
# The statements that encode *behaviour* rather than shape - the supersession
# UPDATE and the similarity SELECT, which carry the concurrency control and the
# visibility filter - stay in the store, where the reasoning for them is.

INSERT_ASSERTION: Final = f"""
    INSERT INTO assertion ({ASSERTION_COLUMNS}, embedding)
    VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6::jsonb, $7, $8,
            $9, $10, $11, $12, $13::uuid, $14, $15, false, $16)
    ON CONFLICT (id) DO NOTHING
"""  # noqa: S608 - ASSERTION_COLUMNS is a module constant; every value is bound

INSERT_PROVENANCE: Final = """
    INSERT INTO provenance (id, assertion_id, source_hash, source_span,
                            source_tier, verbatim, alignment, captured_at)
    VALUES ($1::uuid, $2::uuid, $3, int4range($4, $5), $6, $7, $8, $9)
    ON CONFLICT (id) DO NOTHING
"""

SELECT_PROVENANCE: Final = """
    SELECT assertion_id, source_hash, lower(source_span) AS span_start,
           upper(source_span) AS span_end, source_tier, verbatim,
           alignment, captured_at
    FROM provenance
    WHERE assertion_id = ANY($1::uuid[])
    ORDER BY captured_at
"""
