"""The pipeline's dependencies, for the inline path.  BUILD_NOTEBOOK.md S8.4

`mode=strict` runs the pipeline in the request, so the gateway needs a `Deps`.
`guardmem_core.governance.build_deps` composes it; what this module contributes
is what is the gateway's own - its policy stamp, and the process resources
`GatewayState` already holds.

**Per request, not per process, because the store and the resolver bind a
tenant.** The model client, the graph and the ontology are on `GatewayState`
because they are expensive and tenant-agnostic; the tenant-bound half is built on
every strict call. That is what keeps the binding impossible to get wrong - there
is no cached `Deps` for one tenant that another could be served.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core import governance

if TYPE_CHECKING:
    from gateway.lifespan import GatewayState
    from guardmem_core.pipeline.deps import Deps
    from guardmem_core.types import TenantId

__all__ = ["POLICY_VERSION", "build_deps"]

# Stamped on every `DecisionRecord` the inline path produces. Distinct from the
# worker's `worker-v1` on purpose: `PRD.md` FR-3.3 makes a threshold change an audited
# event and every record names the set that produced it, so a decision made inline and
# one made off-queue must be distinguishable in the audit log - they can hold
# different thresholds mid-deploy, and they draw different sample sizes always.
POLICY_VERSION: Final = "gateway-strict-v1"


def build_deps(state: GatewayState, tenant: TenantId) -> Deps:
    """Compose the pipeline for one tenant's inline request.

    Args:
        state: The process resources. `llm` must not be None - the caller checks and
            returns 503, because a 500 from a missing credential would report a
            configuration problem as a fault.
        tenant: Whose data this pipeline may see - the isolation boundary, taken from
            the authenticated principal and nowhere else.

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
    return governance.build_deps(
        settings=state.settings,
        pool=state.pool,
        tenant_id=tenant,
        llm=state.llm,
        graph=state.graph,
        embedder=state.embedder,
        ontology=state.ontology,
        policy_version=POLICY_VERSION,
    )
