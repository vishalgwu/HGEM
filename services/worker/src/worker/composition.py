"""Everything the pipeline needs, built once per worker process.  S8.4

`Deps` has thirteen fields and `pipeline/deps.py` explains why each is a Protocol.
Choosing the concrete implementations is a composition root's whole job, and
`PROJECT_TREE.md`'s ownership table makes a service that root - which is also why
this is duplicated in spirit across `mcp_server.lifespan`, `gateway.composition` and
`scripts/checkpoint_b_generate`, and why the import-linter contract "the services do
not import each other" forbids sharing it.

**That duplication is deliberate and has a cost worth naming.** Four composition
roots means four places to add a dependency, and forgetting one is a service that
starts and then fails at the first request. The alternative is worse: a shared
composer in `guardmem-core` would make the library import every concrete store and
provider, which breaks `ARCHITECTURE.md` §2.2's claim that the engine runs
identically in the gateway, the worker and the eval harness - because it could then
only run where all of them are installed.

**`NetworkXGraphStore` and `HashEmbedder` are what bind today**, and both matter for
reading any number this produces. The graph is in-process, so a worker sees only
what it wrote itself and `graph_fanout` is not a measurement of anything; hash
vectors model no semantics, so `novelty` and incumbent retrieval are near-noise.
Neither feeds `C` - they feed `R` - so a confidence number from this path is
readable and a risk number is not. S9.1's real embedder and S7.1's Neo4j backend are
what close that, and the reason this names the classes rather than reading a setting
is that a worker whose graph silently changed between runs would make two `R` values
a week apart incomparable.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import httpx

from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.llm.providers import build_llm
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from guardmem_core.schemas.ontology import load_ontology

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.settings import Settings
    from guardmem_core.types import TenantId

__all__ = ["ONTOLOGY", "POLICY_VERSION", "build_deps"]

# The only pack that ships. Named here rather than read from a setting for the reason
# `mcp_server.lifespan.DEFAULT_ONTOLOGY` gives: a setting with one legal value is a
# setting nobody can get wrong, and the step that adds a second pack is the step that
# knows whether the choice is per-process or per-namespace.
ONTOLOGY = "clinical"

# Stamped on every `DecisionRecord` this worker produces. `PRD.md` FR-3.3 makes a
# threshold change an audited event and every record names the set that produced it,
# so this string is what distinguishes a decision made by the queue from one made
# inline by the gateway - the two can hold different thresholds mid-deploy.
POLICY_VERSION = "worker-v1"


async def build_deps(
    stack: AsyncExitStack, pool: asyncpg.Pool, tenant: TenantId, settings: Settings
) -> Deps:
    """Compose a real pipeline for one tenant.

    Args:
        stack: The caller's exit stack. Every resource opened here is registered on
            it the moment it exists, so a later failure cannot leak an earlier
            success - the argument `mcp_server.lifespan` makes at length about a
            hand-written `try/finally`.
        pool: The process-wide Postgres pool. Shared; the store built from it is not.
        tenant: Whose data this pipeline may see. `PgVectorStore` binds it and
            `pool.tenant_transaction` turns it into `app.tenant_id`, which is what
            the RLS policies read - so this argument is the isolation boundary.
        settings: Read for the provider, timeouts and thresholds.

    Returns:
        A `Deps` ready for `pipeline.run`.

    Raises:
        ValueError: no model provider is configured. **Raised rather than degraded.**
            The gateway may serve reads without a model - `mcp_server` explains why
            its own `llm` is optional - but a worker exists only to run the pipeline,
            and a pipeline with no model cannot extract anything. A worker that
            started anyway would drain the queue by failing every job, which looks
            like throughput.

    **Per tenant, not per process.** The store binds one tenant and a worker serves
    every tenant's jobs, so this is called once per job rather than held on the
    worker's context. Only the pool, the HTTP client and the model client are shared,
    and those are the expensive ones.
    """
    http = await stack.enter_async_context(httpx.AsyncClient(base_url=settings.ollama_url))
    llm = await stack.enter_async_context(build_llm(settings, http))
    embedder = HashEmbedder()
    return Deps(
        llm=llm,
        vector=PgVectorStore(pool, embedder, tenant_id=tenant, timeout_s=settings.store_timeout_s),
        graph=NetworkXGraphStore(),
        embedder=embedder,
        nli=LLMJudge(llm),
        entail=LLMEntailer(llm).lookup,
        resolver=NamespaceEntityResolver(pool, timeout_s=settings.store_timeout_s),
        ontology=load_ontology(ONTOLOGY),
        thresholds=settings.thresholds(),
        weights=V1_WEIGHTS,
        betas=V1_BETAS,
        policy_version=POLICY_VERSION,
        max_concurrent_scores=settings.max_concurrent_scores,
    )
