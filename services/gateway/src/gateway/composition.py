"""The pipeline's dependencies, for the inline path.  BUILD_NOTEBOOK.md S8.4

`mode=strict` runs the pipeline in the request, so the gateway needs a `Deps` - all
thirteen fields of it. Choosing the concrete implementations is a composition root's
whole job, and `PROJECT_TREE.md`'s ownership table makes a service that root.

**This is the third such module in the repository and that is deliberate.**
`mcp_server.lifespan`, `worker.composition` and this one each compose their own, and
the import-linter contract "the services do not import each other" forbids sharing.
The cost is real - three places to add a dependency, and forgetting one is a service
that starts and fails at its first request. The alternative is worse: a shared
composer in `guardmem-core` would make the library import every concrete store and
provider, which breaks `ARCHITECTURE.md` §2.2's claim that the engine runs identically
in the gateway, the worker and the eval harness, because it could then only run where
all of them are installed.

**Per request, not per process, and only for the parts that bind a tenant.**
`PgVectorStore` and `NamespaceEntityResolver` take a tenant, so they are built here on
every strict call; the model client, the graph and the ontology are on `GatewayState`
because they are expensive and tenant-agnostic. Building the cheap half per request is
what keeps the tenant binding impossible to get wrong - there is no cached `Deps` for
one tenant that another could be served.
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
    from gateway.lifespan import GatewayState
    from guardmem_core.types import TenantId

__all__ = ["POLICY_VERSION", "build_deps"]

# Stamped on every `DecisionRecord` the inline path produces. Distinct from the
# worker's `worker-v1` on purpose: `PRD.md` FR-3.3 makes a threshold change an audited
# event and every record names the set that produced it, so a decision made inline and
# one made off-queue must be distinguishable in the audit log - they can hold
# different thresholds mid-deploy, and they draw different sample sizes always.
POLICY_VERSION = "gateway-strict-v1"


def build_deps(state: GatewayState, tenant: TenantId) -> Deps:
    """Compose the pipeline for one tenant's inline request.

    Args:
        state: The process resources. `llm` must not be None - the caller checks and
            returns 503, because a 500 from a missing credential would report a
            configuration problem as a fault.
        tenant: Whose data this pipeline may see. `PgVectorStore` binds it and
            `pool.tenant_transaction` turns it into `app.tenant_id`, which the RLS
            policies read - so this argument is the isolation boundary, and it comes
            from the authenticated principal and nowhere else.

    Returns:
        A `Deps` ready for `pipeline.run`.

    Raises:
        AssertionError: `state.llm` is None. A programming error rather than a request
            problem: the route checks first and answers 503, so reaching here without
            a provider means that check was removed.

    **`store_timeout_s` raw, not the request deadline.** Unlike
    `dependencies.vector_store`, which threads the budget, this store is built for a
    pipeline that makes many calls and would need the deadline consulted at each - and
    `Deps` takes a store, not a store factory. The request is still bounded: the
    deadline middleware set it, and `mode=strict`'s own SLA is `PRD.md` §6.1's 450ms.
    Threading it through `Deps` is the next increment and wants the pipeline to accept
    a deadline rather than the gateway to fake one.
    """
    assert state.llm is not None, "the route must answer 503 before composing without a provider"
    embedder = state.embedder
    return Deps(
        llm=state.llm,
        vector=PgVectorStore(
            state.pool, embedder, tenant_id=tenant, timeout_s=state.settings.store_timeout_s
        ),
        graph=state.graph,
        embedder=embedder,
        nli=LLMJudge(state.llm),
        entail=LLMEntailer(state.llm).lookup,
        resolver=NamespaceEntityResolver(state.pool, timeout_s=state.settings.store_timeout_s),
        ontology=state.ontology,
        thresholds=state.settings.thresholds(),
        weights=V1_WEIGHTS,
        betas=V1_BETAS,
        policy_version=POLICY_VERSION,
        max_concurrent_scores=state.settings.max_concurrent_scores,
    )
