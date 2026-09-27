"""Everything the pipeline needs, for one queued job.  S8.4

`guardmem_core.governance.build_deps` composes the `Deps`; what this module owns
is what is the worker's own - the backends it binds and the policy string it
stamps. The model client is the worker's too, and `main.startup` opens it once.

**`NetworkXGraphStore` and `HashEmbedder` are what bind today**, and both matter for
reading any number this produces. The graph is built per job, so every job starts
with an empty one and `graph_fanout` is not a measurement of anything; hash vectors
model no semantics, so `novelty` and incumbent retrieval are near-noise. Neither
feeds `C` - they feed `R` - so a confidence number from this path is readable and a
risk number is not. A provider-backed embedder and `GM_GRAPH_BACKEND=neo4j` are what
close that; the classes are named rather than read from a setting because a worker
whose graph silently changed between runs would make two `R` values a week apart
incomparable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from guardmem_core import governance
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.schemas.ontology import load_ontology

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.pipeline.deps import Deps
    from guardmem_core.settings import Settings
    from guardmem_core.types import TenantId

__all__ = ["ONTOLOGY", "POLICY_VERSION", "build_deps"]

# The only pack that ships. Named here rather than read from a setting for the reason
# `mcp_server.lifespan.DEFAULT_ONTOLOGY` gives: a setting with one legal value is a
# setting nobody can get wrong, and the step that adds a second pack is the step that
# knows whether the choice is per-process or per-namespace.
ONTOLOGY: Final = "clinical"

# Stamped on every `DecisionRecord` this worker produces. `PRD.md` FR-3.3 makes a
# threshold change an audited event and every record names the set that produced it,
# so this string is what distinguishes a decision made by the queue from one made
# inline by the gateway - the two can hold different thresholds mid-deploy.
POLICY_VERSION: Final = "worker-v1"


def build_deps(pool: asyncpg.Pool, llm: LLMClient, tenant: TenantId, settings: Settings) -> Deps:
    """Compose a real pipeline for one tenant.

    Args:
        pool: The process-wide Postgres pool. Shared; the store built from it is not.
        llm: The process's model client, opened once by `main.startup`.
        tenant: Whose data this pipeline may see - the isolation boundary.
        settings: Read for timeouts and thresholds.

    Returns:
        A `Deps` ready for `pipeline.run`.

    **Per tenant, not per process.** The store binds one tenant and a worker serves
    every tenant's jobs, so this is called once per job rather than held on the
    worker's context. The pool and the model client are what is shared, and they
    are the expensive parts.
    """
    return governance.build_deps(
        settings=settings,
        pool=pool,
        tenant_id=tenant,
        llm=llm,
        graph=NetworkXGraphStore(),
        embedder=HashEmbedder(),
        ontology=load_ontology(ONTOLOGY),
        policy_version=POLICY_VERSION,
    )
