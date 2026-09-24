"""The write and read path.  BUILD_NOTEBOOK.md S8.2, S8.4

Propose a candidate, search, fetch an entity.

**One endpoint at S8.2, and it is a read.** S8.2's DONE WHEN is "a token for
tenant A cannot read tenant B's assertions through REST, MCP, or the SDK", and a
claim about reading needs something to read through. `GET /memory/search` is that
surface. The write path is S8.4's, where the async mode and the 202 contract are
decided together.

**What makes the isolation real is what is absent.** `queries.py` contains no
tenant predicate - not one - so nothing here filters by tenant and nothing here
could be made to filter by the wrong one. The scope comes entirely from the RLS
policy `0001_initial` puts on `assertion`, which reads `app.tenant_id`, which
`pool.tenant_transaction` sets from the store's bound tenant, which
`dependencies.vector_store` takes from the authenticated principal. That chain is
the security boundary, and every link is in a different module on purpose: one
function that both authenticated and queried would be a single edit away from
taking a tenant off the request.

`RULES.md` §2.4: a router validates, calls one core function, and shapes the
response. The handler below does those three things and no more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from gateway.dependencies import EmbedderDep, PrincipalDep, StoreDep
from guardmem_core.types import Namespace

if TYPE_CHECKING:
    from gateway.auth import Principal
    from guardmem_core.memory.vector.base import ScoredAssertion

__all__ = ["router"]

router: Final = APIRouter(prefix="/memory", tags=["memory"])

# What a caller may ask for in one page. Deliberately not `Settings.default_k`:
# that is the pipeline's sample size, and one constant meaning both "how many
# completions to draw" and "how many rows to return" is two meanings that drift
# apart the first time either is tuned.
_MAX_RESULTS: Final = 50

# The scope this router's reads require, from `PRD.md`'s "scoped API keys".
_READ_SCOPE: Final = "memory:read"


class Hit(BaseModel):
    """One assertion a search matched.

    Attributes:
        assertion_id: Stable identifier, for a follow-up read.
        subject_id: The resolved entity the fact is about - an id, not a surface
            form, because entity resolution has already happened by write time and
            `StoredAssertion` holds no name. Resolving it to a display name is a
            second read against `entity`, which this endpoint does not do.
        predicate: Which ontology predicate it asserts.
        object: The value, as the ontology types it.
        confidence: `C` as recorded when the assertion was written. Carried
            because a caller ranking or filtering needs it, and recomputing it
            here would produce a second number for one fact.
        cosine: Cosine **similarity** in [-1, 1], as the database measured it -
            `ScoredAssertion.cosine` explains why it cannot be recomputed here.
            **Lexical, not semantic**, while `HashEmbedder` is what the gateway
            binds; see `GatewayState.embedder`. A caller must not read it as
            relevance.
    """

    assertion_id: str
    subject_id: str
    predicate: str
    object: object
    confidence: float
    cosine: float


class SearchResult(BaseModel):
    """What `GET /memory/search` returns.

    Attributes:
        namespace: Echoed, so a response read out of context says what it was
            scoped to.
        hits: Matches, nearest first.
    """

    namespace: str
    hits: list[Hit] = Field(default_factory=list)


@router.get(
    "/search",
    summary="Search this tenant's memory",
    response_model=SearchResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid credential."},
        status.HTTP_403_FORBIDDEN: {"description": "The credential lacks memory:read."},
    },
)
async def search(
    store: StoreDep,
    embedder: EmbedderDep,
    caller: PrincipalDep,
    q: Annotated[str, Query(min_length=1, max_length=2000, description="The query text.")],
    namespace: Annotated[str, Query(min_length=1, max_length=200)],
    k: Annotated[int, Query(ge=1, le=_MAX_RESULTS)] = 10,
) -> SearchResult:
    """Return this tenant's assertions nearest to `q`.

    Args:
        store: Already bound to the caller's tenant. **The only source of tenancy
            in this handler** - there is deliberately no tenant parameter.
        embedder: Process-scoped query embedding, awaited for one text. Taken as
            its own dependency
            rather than off the store, whose `_embedder` is private for a reason:
            a store is a place to put things and read them back, and what turned
            text into a vector is the read path's business.
        caller: The authenticated principal, taken for the scope check.
        q: Query text, embedded by the store's embedder.
        namespace: Isolation scope within the tenant. Required rather than
            defaulted, because a default namespace is a read of somebody's primary
            memory that nobody asked for.
        k: How many to return.

    Returns:
        `SearchResult`, possibly empty. **Empty is the correct answer to a
        cross-tenant query**, not an error: RLS makes another tenant's rows
        invisible rather than forbidden, so the honest response is "no matches".
        A 403 there would confirm that something was present to be refused.

    Raises:
        HTTPException: 403 when the credential lacks `memory:read`.
        StoreUnavailable: Postgres is unreachable. Retryable, and the domain
            hierarchy already says so.

    `q` is bounded at 2000 characters and `k` at 50 by the annotations above -
    `RULES.md` §2.2's position applied to input rather than to time, since an
    unbounded query is an unbounded embedding and an unbounded scan.
    """
    _require(caller, _READ_SCOPE)
    found = await store.search(
        namespace=Namespace(namespace),
        embedding=(await embedder.embed([q]))[0],
        k=k,
        filters={},
    )
    return SearchResult(namespace=namespace, hits=[_hit(item) for item in found])


def _require(caller: Principal, scope: str) -> None:
    """Refuse a caller that lacks a scope.

    Args:
        caller: The authenticated principal.
        scope: What this route needs.

    Raises:
        HTTPException: 403, naming the scope required but never the scopes held.
            Echoing those back would let a caller map its own credential by
            probing routes, and it already knows what it was issued.

    403 rather than 401: the credential was valid, which is the distinction the
    two codes exist to draw. A 401 here would make a client retry an
    authentication that already succeeded.
    """
    if not caller.permits(scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"this credential lacks the {scope} scope",
        )


def _hit(item: ScoredAssertion) -> Hit:
    """Shape one stored assertion for the wire.

    Args:
        item: What the store returned.

    Returns:
        The response model.

    A deliberate projection rather than returning the stored model. `RULES.md`
    §1.5 keeps internal state out of what a caller sees, and a `StoredAssertion`
    carries provenance spans over source text the caller may not be entitled to -
    the offsets alone would leak the shape of a transcript.
    """
    return Hit(
        assertion_id=str(item.assertion.assertion_id),
        subject_id=str(item.assertion.subject_id),
        predicate=item.assertion.predicate,
        object=item.assertion.object,
        confidence=item.assertion.confidence,
        cosine=item.cosine,
    )
