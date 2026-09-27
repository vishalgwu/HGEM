"""The Postgres-backed pipeline: composed once, run, and applied.  S8.4

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

**`govern` is run-then-apply, and two of the three services were missing the
second half.** `pipeline.run()` decides and writes nothing, by design - ADR-0010
puts the write in `memory.applier`, one transaction per candidate with its audit
events. The MCP server called both. The gateway's `mode=strict` and the worker
called `run()` alone and returned counts, so every decision either reached was
computed and dropped: no assertion, and no `DECISION` event on the audit chain,
although the worker's own docstring said "the facts are in the store behind RLS".
One function that does both is what stops a caller stopping halfway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.memory.applier import apply_all
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from guardmem_core.pipeline.orchestrator import run

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.memory.applier import Applied
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder, VectorStore
    from guardmem_core.pipeline.orchestrator import PipelineResult, Proposal
    from guardmem_core.pipeline.per_candidate import CandidateFailure
    from guardmem_core.schemas.ontology import Ontology
    from guardmem_core.settings import Settings
    from guardmem_core.types import TenantId

__all__ = ["Governed", "build_deps", "govern"]


@dataclass(frozen=True, slots=True)
class Governed:
    """What governing one proposal decided, and what became of each decision.

    Attributes:
        result: The pipeline's decisions, candidate by candidate.
        applied: What the applier did with each, by candidate id - a row
            written, or the stable reason none was.
        failures: Every candidate that raised, whether while it was scored or
            while its decision was applied. One list, because to a caller both
            mean the same thing: this fact was neither decided nor dropped.
    """

    result: PipelineResult
    applied: dict[str, Applied]
    failures: list[CandidateFailure]

    @property
    def written(self) -> int:
        """How many candidates became an assertion row."""
        return sum(1 for outcome in self.applied.values() if outcome.assertion_id is not None)


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


async def govern(
    proposal: Proposal, deps: Deps, *, pool: asyncpg.Pool, timeout_s: float
) -> Governed:
    """Run the pipeline on one proposal and apply every decision it reached.

    Args:
        proposal: What to govern.
        deps: From `build_deps`. Its `embedder` is also the applier's, so a
            written row carries a vector from the model retrieval compares with.
        pool: The process pool the applier opens its transactions on.
        timeout_s: Per-statement ceiling, from `settings.store_timeout_s`.

    Returns:
        The decisions, what the applier did with each, and every per-candidate
        failure from either half.

    Raises:
        InjectionDetected: extraction itself was compromised - `pipeline.run`'s
            proposal-wide failure, raised before anything is applied.
        ProviderUnavailable: the noise filter or the extractor could not run.
            Retryable, and nothing was written, so a retry is clean.
        ValueError: no surviving turn carries a capture time.

    **The applier's store is bound to `proposal.tenant_id`**, the same field the
    extractor stamped on every candidate, so the tenant of a written row and the
    tenant of its audit chain come from one value rather than two that happen to
    agree. Built here rather than taken from `deps.vector`, which is typed as the
    `VectorStore` Protocol; the applier needs the concrete class, which is where
    ADR-0010 put `write_in` and `supersede_in` on purpose.

    A written row is **invisible** until the outbox relay lands its graph side -
    ADR-0010 leaves `visible` to the relay - so a search straight after this
    finds nothing, by design.
    """
    result, failures = await run(proposal, deps)
    store = PgVectorStore(pool, deps.embedder, tenant_id=proposal.tenant_id, timeout_s=timeout_s)
    applied, apply_failures = await apply_all(
        result.governed,
        store=store,
        pool=pool,
        tenant_id=proposal.tenant_id,
        trace_id=result.trace_id,
        timeout_s=timeout_s,
    )
    return Governed(result=result, applied=applied, failures=[*failures, *apply_failures])
