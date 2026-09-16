"""The durable `GraphStore`.  BUILD_NOTEBOOK.md S7.1

S1.7 declared the protocol, S3.4 satisfied it in memory, and this is the backend
the protocol's docstring promised: "S7.1 swaps Neo4j in behind the same three
methods via `GM_GRAPH_BACKEND`". The step's DONE WHEN is that the integration
suite passes against **both** unchanged, so every decision below is made twice -
once for correctness against `ARCHITECTURE.md` §5, and once for agreement with
`NetworkXGraphStore`, which is the store the whole unit suite is written
against.

**Where the two backends must agree, and where they must not.**

| | NetworkX | Neo4j |
|---|---|---|
| `degree` counts | live edges, both directions, once each | same |
| `neighbors` walks | live out-edges, BFS, entity objects only | same |
| replay by `assertion_id` | `add_edge(key=...)` overwrites | `MERGE`+`SET` overwrites |
| durability | lost on restart | survives it |
| tenancy | **one tenant, enforced by refusing** | **many, enforced by scoping** |

The last row is the interesting one and it is not a difference in strictness -
it is the same guarantee reached from opposite directions. `GraphStore`'s read
methods take an `EntityId` and no tenant, so a store holding two tenants'
subgraphs has nothing to filter `degree()` on. NetworkX answers by refusing to
hold a second tenant at all. This backend exists *because* that refusal is not
an answer for a real deployment, so it filters instead: every read resolves the
tenant from the node it starts at and constrains every edge it counts or
follows to that tenant.

**Why that filter is load-bearing rather than belt-and-braces.** Object nodes
are shared by construction - `ARCHITECTURE.md` §5's `ASSERTS` edge can end at a
literal, and two tenants both recording an allergy to penicillin legitimately
converge on one node. Without the filter a two-hop walk from tenant A's patient
could pass through `"penicillin"` and come back with tenant B's edges, straight
into `MEMORY_ENGINE.md` §2.2's incumbent set and from there into conflict
detection. That is a cross-tenant read with no symptom, which is the failure
`RULES.md` §4 wants defence in depth against.

**A node with no tenant is read unscoped, and that is reachable from a test and
not from the pipeline.** A node created only ever as an *object* carries no
`tenant_id` - there is no single tenant it belongs to. Both callers of these
methods pass a resolved subject `EntityId`, which always has one; the unscoped
path exists because the protocol permits an arbitrary `EntityId` and the
contract suite exercises it. The honest fix is a tenant on the protocol, and the
place that gets one is **S8.2**, where a request first carries an authenticated
tenant to thread through.

**The driver is injected and never constructed here**, exactly as
`PgVectorStore` takes a pool and the LLM adapters take a transport. `RULES.md`
§2.2 puts client creation in a composition root - `graph/selection.py` is this
one's - and an `AsyncDriver` owns a connection pool, so one per process and
never one per request.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from neo4j import exceptions as neo4j_errors

from guardmem_core.errors import StoreUnavailable, ValidationRejected
from guardmem_core.memory.graph.cypher import (
    DEGREE,
    HOP,
    START_TENANT,
    UPSERT_ENTITY_OBJECT,
    UPSERT_LITERAL_OBJECT,
)
from guardmem_core.memory.graph.keys import object_key
from guardmem_core.schemas.entity import Edge
from guardmem_core.types import AssertionId, EntityId, TraceId

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from neo4j import AsyncDriver

    from guardmem_core.schemas.entity import StoredAssertion

__all__ = ["Neo4jGraphStore"]

# The driver faults that mean "try again", as opposed to "this query is wrong".
# `ServiceUnavailable` and `SessionExpired` are `DriverError`s - the server is
# unreachable or the session moved - and `TransientError` is the server itself
# asking for a retry (a deadlock, a leader switch). Everything else is a
# `ClientError` or a `DatabaseError`: a Cypher bug or a constraint violation,
# which must not be dressed as an outage, because `ARCHITECTURE.md` §4's rule
# for a graph outage is "the outbox retries" and retrying a syntax error forever
# is how a poison event is made.
_RETRYABLE: Final = (
    neo4j_errors.ServiceUnavailable,
    neo4j_errors.SessionExpired,
    neo4j_errors.TransientError,
)


class Neo4jGraphStore:
    """The entity graph, in Neo4j.

    Structurally a `GraphStore`: it inherits from nothing and registers nowhere,
    and `mypy --strict` is what checks the conformance - the same arrangement
    S1.7 chose for every store seam. Nothing in `pipeline/` may import this
    class, and the `import-linter` contract in `pyproject.toml` enforces it.
    """

    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        """Bind a driver to this store.

        Args:
            driver: An `AsyncDriver`. **Injected, not constructed here** - see
                the module docstring. Its lifetime belongs to the composition
                root, which closes it; a store that opened its own would leak a
                connection pool per request.
            database: Which database to address. `None` uses the driver's
                default, which is what the Community edition offers - it is
                single-database, and `docker-compose.dev.yml` names `neo4j`
                explicitly so the bolt URI in `.env.example` is unambiguous.
                Enterprise deployments get a database per tenant at the step
                that needs one.
        """
        self._driver = driver
        self._database = database

    async def upsert_assertion(self, a: StoredAssertion) -> None:
        """Materialise `a` as one `ASSERTS` edge, idempotently by id.

        Args:
            a: The assertion. `subject_id` is an `:Entity`; `object` is an
                `:Entity` when it is a string and a `:Literal` otherwise.

        Raises:
            StoreUnavailable: Neo4j is unreachable. Retryable, and
                `ARCHITECTURE.md` §4 is explicit that it is not fatal - the
                write proceeds vector-only and the outbox retries. What must
                never happen is dropping the assertion.
            ValidationRejected: the subject entity is already held for a
                different tenant. Not a store fault and not retryable: it means
                two tenants derived the same `EntityId`, which ADR-0008's
                `uuid5` derivation makes impossible unless something upstream is
                wrong. Refusing is what stops one tenant's blast radius being
                computed from another's edges.

        Replaying is a no-op by construction: `MERGE` on the relationship's
        `assertion_id` finds the edge already there and `SET` rewrites its
        properties with the same values. That is the semantics the S3.3 relay
        needs, and the same one `NetworkXGraphStore` gets from an edge key.
        """
        await self._reject_foreign_tenant(a)
        key = object_key(a.object)
        query = UPSERT_ENTITY_OBJECT if isinstance(a.object, str) else UPSERT_LITERAL_OBJECT
        await self._run(
            query,
            {
                "subject": str(a.subject_id),
                "canonical_name": str(a.subject_id),
                "object_key": key,
                "assertion_id": str(a.assertion_id),
                "predicate": a.predicate,
                # Serialised, because a Neo4j property cannot hold a map or a
                # nested list and `ObjectValue` permits both. The same choice
                # `assertion.object_json` makes in Postgres, for the same reason.
                "object_json": json.dumps(a.object, sort_keys=True),
                "confidence": a.confidence,
                "valid_from": a.valid_from,
                "valid_to": a.valid_to,
                "trace_id": str(a.trace_id),
                "tenant": str(a.tenant_id),
            },
        )

    async def neighbors(self, entity: EntityId, hops: int = 1) -> list[Edge]:
        """Return live edges reachable from `entity` within `hops`.

        Args:
            entity: The node to expand from.
            hops: How far. One by default, which is what both callers want.

        Returns:
            The edges, live ones only, each assertion once. Order within a hop
            is **not** specified - `NetworkXGraphStore` says the same, and the
            reason it says it is this backend: Cypher makes no promise about row
            order without an `ORDER BY`, so a test that pinned one here would
            pin something neither store guarantees. The suite compares sets.

        Raises:
            StoreUnavailable: Neo4j is unreachable. Retryable.

        **A Python loop over one query per hop, not a variable-length path.**
        Cypher's `-[:ASSERTS*1..n]->` needs a literal bound, so expressing
        `hops` that way would mean rendering a caller's integer into query text
        - which `RULES.md` §4 forbids outright, and which is the one place in
        this module where the tempting shortcut is also an injection. The loop
        is also why the two backends agree: it is the same breadth-first walk
        `NetworkXGraphStore.neighbors` runs, with the frontier passed as a list
        parameter and one round trip per hop rather than per node.

        The walk follows an object only when it is an `:Entity`, which is
        exactly when the stored object was a string - the assumption
        `Edge.object` documents. Cycles terminate because each assertion is
        collected once.
        """
        tenant = await self._tenant_of(entity)
        seen: set[str] = set()
        found: list[Edge] = []
        frontier = [str(entity)]
        for _ in range(max(hops, 0)):
            rows = await self._run(HOP, {"frontier": frontier, "tenant": tenant})
            following: dict[str, None] = {}
            for row in rows:
                assertion_id = str(row["assertion_id"])
                if assertion_id in seen:
                    continue
                seen.add(assertion_id)
                found.append(_edge_from(row))
                if row["object_is_entity"]:
                    following[str(row["object_key"])] = None
            if not following:
                break
            frontier = list(following)
        return found

    async def degree(self, entity: EntityId) -> int:
        """Return how many live edges touch `entity`, in either direction.

        Args:
            entity: The node to measure.

        Returns:
            The count of live edges, or zero for an entity this graph has never
            seen. Zero rather than an error, per the protocol: an unknown
            subject is a novel one, and `MEMORY_ENGINE.md` §3.3 prices novelty
            separately.

        Raises:
            StoreUnavailable: Neo4j is unreachable. Retryable.

        One query, not two. `DEGREE` reads the tenant off the node it starts at
        (`n.tenant_id IS NULL OR r.tenant_id = n.tenant_id`), so the scoping
        costs no round trip and needs no parameter.

        **`rows[0]` is unguarded on purpose, and S7.2 is where the guard went.**
        `count(r)` is an aggregate with no grouping key, so Cypher returns one
        row even when the `MATCH` finds nothing - verified against 5.26, which
        answers `[{'degree': 0}]` for a node that does not exist. An
        `if not rows: return 0` in front of this was therefore unreachable, and
        unreachable defensive code is worse than none: it reads as a handled
        case and is a branch no test can ever cover. If `DEGREE` ever stops
        aggregating, this raises `IndexError` and
        `test_an_unknown_entity_has_degree_zero` fails - which is the loud
        failure, rather than a zero from a query that quietly stopped answering.

        `neighbors` cannot resolve its tenant the same way, because its later
        hops must stay scoped to the tenant of the node the walk *started* at
        rather than of whatever node it has reached.
        """
        rows = await self._run(DEGREE, {"entity": str(entity)})
        return int(rows[0]["degree"])

    # --- internals ----------------------------------------------------------

    async def _tenant_of(self, entity: EntityId) -> str | None:
        """The tenant a read starting at `entity` is scoped to.

        Returns:
            The node's `tenant_id`, or `None` when the node is unknown or has
            only ever been an object. `None` means unscoped - see the module
            docstring for why that is reachable from the contract suite and not
            from the pipeline.
        """
        rows = await self._run(START_TENANT, {"entity": str(entity)})
        if not rows:
            return None
        tenant = rows[0]["tenant"]
        return None if tenant is None else str(tenant)

    async def _reject_foreign_tenant(self, a: StoredAssertion) -> None:
        """Refuse a write whose subject already belongs to another tenant.

        Raises:
            ValidationRejected: the subject node is held for a different tenant.

        Checked on the **subject** only, never on the object, for the reason
        `NetworkXGraphStore` gives: a subject is a resolved `EntityId` and
        belongs to exactly one tenant, while an object is frequently a literal
        that two tenants legitimately share.

        This backend is multi-tenant, so unlike NetworkX the check is not about
        refusing a second tenant - it is about refusing a *collision*. ADR-0008
        derives an `EntityId` as a `uuid5` over the tenant, so two tenants
        cannot reach the same id by any legitimate route; one that did would
        silently merge two customers' graphs, and every `degree()` afterwards
        would be wrong in a direction nobody would notice.
        """
        rows = await self._run(START_TENANT, {"entity": str(a.subject_id)})
        if not rows:
            return
        held = rows[0]["tenant"]
        if held is not None and str(held) != str(a.tenant_id):
            raise ValidationRejected(
                f"entity {a.subject_id} is already held for tenant {held}; this "
                f"write claims it for {a.tenant_id}. ADR-0008 derives an EntityId "
                "as a uuid5 over the tenant, so two tenants cannot legitimately "
                "reach one id - this is a collision, and merging the two graphs "
                "would make every blast-radius score wrong without a symptom.",
                trace_id=a.trace_id,
                candidate_id=a.assertion_id,
            )

    async def _run(self, query: str, parameters: Mapping[str, Any]) -> Sequence[Any]:
        """Run one statement, translating driver faults.

        Args:
            query: A statement from `cypher.py`.
            parameters: Its parameters, always bound and never interpolated.

        Returns:
            The records, eagerly - every query here returns at most a bounded
            page and holding a cursor open would hold a connection with it.

        Raises:
            StoreUnavailable: the server is unreachable, the session expired, or
                the server asked for a retry.

        `execute_query` rather than a hand-rolled session: it runs the statement
        in a managed transaction with the driver's own retry policy, which is
        the part worth not reimplementing. A `ClientError` propagates unwrapped
        for the reason `_RETRYABLE` gives.
        """
        try:
            result = await self._driver.execute_query(
                query, parameters_=dict(parameters), database_=self._database
            )
        except _RETRYABLE as exc:
            raise StoreUnavailable(f"neo4j is unreachable: {exc}") from exc
        records: Sequence[Any] = result.records
        return records


def _edge_from(row: Mapping[str, Any]) -> Edge:
    """Rebuild one `Edge` from a `HOP` row.

    Returns:
        The edge, with its object decoded from `object_json`.

    The object comes back through `json.loads` rather than off the node's `key`,
    and the difference matters for a literal: the key is a canonicalised
    `literal:{...}` string, while the edge has to carry the *value* the pipeline
    stored. Reading the key would turn the integer 42 into the string
    `"literal:42"` somewhere deep in a conflict check.
    """
    return Edge(
        assertion_id=AssertionId(str(row["assertion_id"])),
        subject_id=EntityId(str(row["subject_id"])),
        predicate=str(row["predicate"]),
        object=json.loads(row["object_json"]),
        confidence=float(row["confidence"]),
        valid_from=_native(row["valid_from"]),
        valid_to=_native(row["valid_to"]),
        trace_id=TraceId(str(row["trace_id"])),
    )


def _native(value: Any) -> Any:
    """Convert a Neo4j temporal back to a `datetime`.

    The driver returns `neo4j.time.DateTime` for a stored timestamp, which is
    not a `datetime` and which pydantic would reject. `to_native()` is the
    documented conversion; anything without it - a `None`, or an already-native
    value from a fake - passes through.
    """
    to_native = getattr(value, "to_native", None)
    return to_native() if callable(to_native) else value
