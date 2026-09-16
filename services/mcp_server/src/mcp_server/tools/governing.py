"""Turning a tool call into a governed decision, and back.  S6.2, MCP_INTEGRATION.md §2.2

Split from `pipeline.py` at `RULES.md` §2.4's cap, along the seam the two halves
already have: that module is the **published API** - §2.2's and §2.3's argument
schemas, read and refused exactly as written - and this one is *composition and
mapping*, which is where a concrete store, a concrete resolver and a result
shape live.

**This is the composition root for a request.** `guardmem_core.pipeline` may not
import a concrete store - an import-linter contract enforces it - so somebody has
to name `PgVectorStore`, `NamespaceEntityResolver`, `LLMJudge` and `LLMEntailer`,
and a service is where `PROJECT_TREE.md` puts that job. `lifespan` owns what is
process-scoped (the pool, the graph, the model client); this owns what is bound
to one call's tenant.

**Nothing here writes an assertion, and the result says so.** `run()` reaches a
decision and applies none: `RULES.md` non-negotiable #4 binds the audit event to
the state change, `VectorStore.upsert` owns the only transaction, and composing
the two is a Postgres-specific applier that needs its own ADR. So §2.2's
`assertion_id` - which its example shows on an `auto_write` - **cannot be
produced**, and `applied: false` is carried instead of leaving a caller to infer
from a missing field that their fact was stored. A decision of `auto_write` here
means "this would be written", which is a different claim from "this was
written" and has to read as one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from guardmem_core.pipeline.orchestrator import PipelineResult
    from guardmem_core.pipeline.per_candidate import CandidateFailure
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.schemas.verdict import DecisionRecord
    from mcp_server.tools.context import ToolContext

__all__ = ["deps_for", "result_of"]

# What `DecisionRecord.policy_version` records while no policy pack exists.
# S12.2 builds the engine; `override_signals` already takes the string and
# `RiskVerdict.obligations` is empty for the same reason. Named rather than
# written as a bare literal at the call site, because a version string that
# silently means "no policy was evaluated" is exactly the kind of value an
# auditor would otherwise read as the name of a pack.
_NO_POLICY_PACK: Final = "none"


def deps_for(context: ToolContext) -> Deps:
    """Compose the pipeline's dependencies for one tool call.

    Args:
        context: The call's tenant, namespace and tenant-bound store.

    Returns:
        A `Deps` the orchestrator can run.

    Raises:
        ToolRefusedError: no model provider is configured. The two write tools
            are the only ones that need one, so this is where the absence
            surfaces - `lifespan` deliberately starts without it so
            `memory.search` and `memory.get_entity` keep working. The message
            names the variable to set, because "no provider" without one sends
            an operator to read code.

    **Built per call rather than held on `ServerState`**, for the reason
    `ToolContext.store` is: the store is bound to the tenant of one request, and
    caching a `Deps` on the process is how a server ends up governing every
    tenant's facts against the first tenant's memory. Everything expensive
    inside it - the pool, the graph, the model client - is process-scoped and
    merely referenced here, so the per-call cost is a few object headers.

    `LLMJudge` and `LLMEntailer` are both built on the one model client. That is
    deliberate and it is a cost fact worth knowing: §2.2(a)'s adjudication and
    §3.1's clustering both run on whatever tier the client serves, so on a
    single-provider process (`build_llm` has no router until S9.2) a BALANCED
    judge call and a FAST extraction hit the same model.
    """
    state = context.state
    if state.llm is None:
        raise ToolRefusedError(
            f"this server has no model provider, so it cannot govern anything. "
            f"GM_LLM_PROVIDER is {state.settings.llm_provider!r} and its credential "
            "is not set - set it, or use GM_LLM_PROVIDER=ollama, which needs none. "
            "memory.search and memory.get_entity read governed memory and work."
        )
    return Deps(
        llm=state.llm,
        vector=context.store,
        graph=state.graph,
        embedder=state.embedder,
        nli=LLMJudge(state.llm),
        entail=LLMEntailer(state.llm).lookup,
        resolver=NamespaceEntityResolver(state.pool, timeout_s=state.settings.store_timeout_s),
        ontology=state.ontology,
        thresholds=state.settings.thresholds(),
        weights=V1_WEIGHTS,
        betas=V1_BETAS,
        policy_version=_NO_POLICY_PACK,
        max_concurrent_scores=state.settings.max_concurrent_scores,
    )


def result_of(result: PipelineResult, failures: list[CandidateFailure]) -> dict[str, Any]:
    """Map a pipeline result onto §2.2's published result object.

    Args:
        result: What the pipeline decided.
        failures: Candidates that raised. One per candidate, never a lost batch.

    Returns:
        §2.2's object, plus the two fields below.

    **`applied` is an amendment to §2.2 and it is not optional.** The published
    example carries `assertion_id` on an `auto_write`; nothing writes, so there
    is no id to carry, and a caller reading `"decision": "auto_write"` with no
    further signal would reasonably conclude the fact is now in memory. The
    field says plainly that it is not. It becomes `true` when the applier lands.

    **`failed` is the second.** §2.2 has no field for a candidate that raised,
    and `run()` returns them *beside* the decisions precisely so one bad
    provider response does not discard nineteen good results. Omitting them
    would mean a fact the caller submitted simply vanished from the answer -
    silent, and in the direction that matters. The error's `code` is carried
    rather than its message: `RULES.md` §1.5 keeps exception strings, which can
    hold a DSN or a span of untrusted source text, out of anything a caller
    sees.
    """
    return {
        "trace_id": str(result.trace_id),
        # §2.2's own vocabulary: "decided" for strict, "accepted" for async.
        # `_require_mode` refuses async until S8.4, so this is always the former.
        "status": "decided",
        "candidates": [_candidate(item.candidate, item.record) for item in result.governed],
        "dropped_noise": result.dropped_noise,
        "quarantined": len(result.quarantined),
        "applied": False,
        "failed": [
            {"candidate_id": str(failure.candidate_id), "code": _code_of(failure)}
            for failure in failures
        ],
    }


def _candidate(candidate: MemoryCandidate, record: DecisionRecord) -> dict[str, Any]:
    """One entry of §2.2's `candidates` array.

    Args:
        candidate: The proposed fact.
        record: What was decided about it.

    Returns:
        The published fields, and no `assertion_id` - see `result_of`.

    `predicate` and `object` come from the *candidate*, which is the only place
    they exist: `MEMORY_ENGINE.md` §0 gives `DecisionRecord` eight fields and
    none of them says what was decided about. `GovernedCandidate` pairs the two
    so this mapping cannot put one candidate's verdict beside another's fact.
    """
    return {
        "candidate_id": str(candidate.candidate_id),
        "predicate": candidate.predicate,
        "object": candidate.object,
        "decision": record.decision.value,
        "confidence": record.confidence.confidence,
        "risk": record.risk.risk,
        "reason_codes": list(record.reason_codes),
    }


def _code_of(failure: CandidateFailure) -> str:
    """The stable error code for a failed candidate, or a generic one.

    Args:
        failure: The candidate and its exception.

    Returns:
        The `GuardMemError.code` where the exception is one - `GM_PROVIDER`,
        `GM_VALIDATION` - and `GM_UNKNOWN` otherwise, which is what an
        unexpected exception type is.

    A code rather than a message, and a `getattr` rather than an `isinstance`
    chain: `RULES.md` §2.3 makes `code` the stable field every member of the
    hierarchy carries, so reading it is reading the contract.
    """
    code = getattr(failure.error, "code", None)
    return str(code) if isinstance(code, str) else "GM_UNKNOWN"
