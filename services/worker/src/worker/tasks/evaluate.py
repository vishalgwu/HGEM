"""The job the async accept defers.  BUILD_NOTEBOOK.md S8.4

S8.4: "`mode=async` -> validate, hash payload to blob store, enqueue, return 202 +
`trace_id` in under 80 ms." This is what runs after the 202.

**ADR-0003 is the reason this exists at all**: write-ahead accept, async eval. The
gateway takes responsibility for a proposal in under 80 ms and the governing happens
here, off the request. `PRD.md` §6.1 budgets the full evaluation at `p95 1.6 s` and
notes it is "not user-blocking" - which is only true if it runs somewhere a user is
not waiting.

**A job that raises is retried by arq, and that is the wrong default for most of what
can go wrong here.** A `ProviderUnavailable` should be retried; a
`ValidationRejected` never will succeed, and retrying it burns the queue and the
model budget to reach the same answer. So the handler catches the domain hierarchy
and decides per `retryable`, which is the field `RULES.md` §2.3 put there for exactly
this.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from guardmem_core.errors import GuardMemError
from guardmem_core.governance import govern
from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.orchestrator import Proposal
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.types import Namespace, TenantId, TraceId
from worker.composition import build_deps

__all__ = ["evaluate"]

_LOGGER: Final = logging.getLogger(__name__)


async def evaluate(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Govern one proposal that the gateway accepted earlier.

    Args:
        ctx: arq's job context. Carries `pool`, `llm` and `settings`, put there by
            `main.startup`, plus arq's own `job_try` and `job_id`.
        payload: The proposal as the gateway enqueued it - a plain dict, because a
            job crosses a process boundary through Redis and must be JSON, not a
            pickled model. It is re-validated here rather than trusted: see below.

    Returns:
        A small summary, which arq stores as the job result. Counts rather than
        candidates: a result a caller polls should not carry the facts, because the
        facts are in the store behind RLS and the job result is not.

    Raises:
        GuardMemError: only when `retryable` is true, so arq retries exactly the
            failures that can succeed later. A non-retryable one is logged and
            swallowed, which ends the job. Either way it is a failure of the
            proposal as a whole, raised before anything was applied, so a retry
            starts clean.

    **Decisions are applied, one transaction each, through `governance.govern`.**
    Until 2026-09-27 this ran the pipeline and returned counts, so every decision a
    queued proposal reached was dropped - no assertion and no audit event - while
    this docstring said the facts were in the store.

    **`model_validate_json`, not `model_validate`.** `GMModel` is strict, so a dict
    carrying `"user"` for a `TurnRole` and an ISO string for a `datetime` is refused -
    the trap `checkpoint_b_generate` records and the gateway's `TurnIn` exists to
    avoid. A job body is JSON to begin with, so round-tripping costs a `dumps`.

    **`source_tier` and `tier` are converted to their enums explicitly**, for the
    same reason. `Proposal` is strict too, and it refused the strings the gateway
    enqueues - so until 2026-09-27 every queued job raised `ValidationError` here,
    before governing anything, and no test had ever run this function.

    **The payload is re-validated here, not trusted from the queue.** The gateway
    validated it before enqueueing, so this looks redundant. It is not: a job can sit
    in Redis across a deploy, so the code reading it may be newer than the code that
    wrote it, and `Turn.model_validate` is what turns a schema change into a clear
    failure rather than a mysterious one three frames deeper. It is also cheap next
    to the model calls that follow.
    """
    settings = ctx["settings"]
    tenant = TenantId(str(payload["tenant_id"]))
    trace = TraceId(str(payload["trace_id"]))
    proposal = Proposal(
        trace_id=trace,
        tenant_id=tenant,
        namespace=Namespace(str(payload["namespace"])),
        turns=[Turn.model_validate_json(json.dumps(turn)) for turn in payload["turns"]],
        source_tier=SourceTier(payload["source_tier"]),
        k=int(payload["k"]),
        tier=Tier(payload["tier"]),
    )
    deps = build_deps(ctx["pool"], ctx["llm"], tenant, settings)
    try:
        governed = await govern(
            proposal, deps, pool=ctx["pool"], timeout_s=settings.store_timeout_s
        )
    except GuardMemError as exc:
        return _refused(exc, trace, ctx)
    summary: dict[str, Any] = {
        "trace_id": str(trace),
        "scored": len(governed.result.governed),
        "written": governed.written,
        "rejected": len(governed.result.rejected),
        "quarantined": len(governed.result.quarantined),
        "failed": len(governed.failures),
    }
    _LOGGER.info("evaluated a queued proposal", extra={**summary, "tenant": str(tenant)})
    return summary


def _refused(exc: GuardMemError, trace: TraceId, ctx: dict[str, Any]) -> dict[str, Any]:
    """Decide whether arq should see this failure again.

    Args:
        exc: What the pipeline raised.
        trace: The proposal, for the log.
        ctx: arq's context, for `job_try`.

    Returns:
        A summary recording the refusal, when the job is finished with.

    Raises:
        GuardMemError: re-raised when `exc.retryable`, which is how arq is told to
            retry. Nothing else re-raises, so a permanent refusal costs one attempt.

    `job_try` is logged because a retry storm is invisible otherwise: the same
    `trace_id` failing eight times reads as eight proposals unless the attempt number
    is on the line.
    """
    if exc.retryable:
        _LOGGER.warning(
            "queued proposal failed and will be retried",
            extra={"trace_id": str(trace), "code": exc.code, "job_try": ctx.get("job_try")},
        )
        raise exc
    _LOGGER.warning(
        "queued proposal refused; not retrying",
        extra={"trace_id": str(trace), "code": exc.code},
    )
    return {"trace_id": str(trace), "refused": exc.code}
