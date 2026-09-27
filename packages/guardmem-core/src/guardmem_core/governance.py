"""The Postgres-backed pipeline, composed once.  S8.4

`pipeline.Deps` has thirteen fields, and until this module four composition
roots built all thirteen by hand - `mcp_server`'s tools, the gateway's strict
path, the worker and the CHECKPOINT B generator. Eight of those fields were
spelled identically in every copy. Two of the copies carried a paragraph
defending the duplication: a shared composer "in `guardmem-core` would make the
library import every concrete store and provider". It already does -
`memory/graph/selection.py` names both graph backends and
`llm/providers/selection.py` all three providers, every one a declared
dependency of this package. The constraint that is real is narrower:
`guardmem_core.pipeline` may not import a concrete store, and an import-linter
contract enforces it. This module is not in `pipeline`.

**What stays with the caller is exactly what differs between callers.** A
service owns its process resources - the pool, the model client, the graph, the
embedder, the ontology - and the policy string it stamps on every record, so
all of those are arguments. What is built here is what every root built the same
way: the tenant-bound store, the judge, the entailer, the resolver, and the four
values read off settings. The copies' own docstrings named the cost of four
homes - "forgetting one is a service that starts and then fails at the first
request" - and this is the one home.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder, VectorStore
    from guardmem_core.schemas.ontology import Ontology
    from guardmem_core.settings import Settings
    from guardmem_core.types import TenantId

__all__ = ["build_deps"]


def build_deps(
    *,
    settings: Settings,
    pool: asyncpg.Pool,
    tenant_id: TenantId,
    llm: LLMClient,
    graph: GraphStore,
    embedder: Embedder,
    ontology: Ontology,
    policy_version: str,
    vector: VectorStore | None = None,
) -> Deps:
    """Compose the pipeline for one tenant over the process's resources.

    Args:
        settings: Read for the store timeout, the thresholds and the scoring
            concurrency bound.
        pool: The process pool; the store and the resolver are built on it.
        tenant_id: Whose data the pipeline may see. The store binds it and
            `pool.tenant_transaction` turns it into `app.tenant_id`, which the
            RLS policies read - so this argument is the isolation boundary, and a
            caller takes it from an authenticated principal or configuration,
            never from a request body.
        llm: The model client. The judge and the entailer are built on it too,
            so on a single-provider process a BALANCED adjudication and a FAST
            extraction hit the same model until S9.2's router exists.
        graph: The entity graph. The caller's choice, because the right one is
            not the same everywhere - the CHECKPOINT B generator wants an
            in-process graph a run cannot leak into the next.
        embedder: Query and write-side embedding. The store is built with this
            one, so retrieval compares vectors from a single model.
        ontology: The tenant's predicate pack.
        policy_version: Stamped on every `DecisionRecord`. Distinct per caller on
            purpose, so the audit log says which path made a decision.
        vector: A store to use instead of building one. `mcp_server` passes the
            one its `ToolContext` holds, which is what lets its unit suite drive
            the handlers against a fake.

    Returns:
        A `Deps` ready for `pipeline.run`.

    Built per call, never cached per process: the store and the resolver bind a
    tenant, and a cached `Deps` is how a process ends up serving every tenant as
    the first one that asked. Everything expensive is an argument, so the per-call
    cost is a few object headers.
    """
    return Deps(
        llm=llm,
        vector=vector
        if vector is not None
        else PgVectorStore(pool, embedder, tenant_id=tenant_id, timeout_s=settings.store_timeout_s),
        graph=graph,
        embedder=embedder,
        nli=LLMJudge(llm),
        entail=LLMEntailer(llm).lookup,
        resolver=NamespaceEntityResolver(pool, timeout_s=settings.store_timeout_s),
        ontology=ontology,
        thresholds=settings.thresholds(),
        weights=V1_WEIGHTS,
        betas=V1_BETAS,
        policy_version=policy_version,
        max_concurrent_scores=settings.max_concurrent_scores,
    )
