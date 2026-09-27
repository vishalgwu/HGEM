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
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, Final

from guardmem_core.errors import GuardMemError
from guardmem_core.pipeline.orchestrator import Proposal, run
from guardmem_core.schemas.turn import Turn
from guardmem_core.types import Namespace, TenantId, TraceId
from worker.composition import build_deps

if TYPE_CHECKING:
    pass

__all__ = ["evaluate"]

_LOGGER: Final = logging.getLogger(__name__)


async def evaluate(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Govern one proposal that the gateway accepted earlier.

    Args:
        ctx: arq's job context. Carries `pool` and `settings`, put there by
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
            swallowed, which ends the job - the proposal is already recorded as
            accepted and the audit trail carries the refusal.

    **`model_validate_json`, not `model_validate`.** `GMModel` is strict, so a dict
    carrying `"user"` for a `TurnRole` and an ISO string for a `datetime` is refused -
    the trap `checkpoint_b_generate` records and the gateway's `TurnIn` exists to
    avoid. A job body is JSON to begin with, so round-tripping costs a `dumps`.

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
    async with AsyncExitStack() as stack:
        deps = await build_deps(stack, ctx["pool"], tenant, settings)
        proposal = Proposal(
            trace_id=trace,
            tenant_id=tenant,
            namespace=Namespace(str(payload["namespace"])),
            turns=[Turn.model_validate_json(json.dumps(turn)) for turn in payload["turns"]],
            source_tier=payload["source_tier"],
            k=int(payload["k"]),
            tier=payload["tier"],
        )
        try:
            result, failures = await run(proposal, deps)
        except GuardMemError as exc:
            return _refused(exc, trace, ctx)
    _LOGGER.info(
        "evaluated a queued proposal",
        extra={
            "trace_id": str(trace),
            "tenant": str(tenant),
            "scored": len(result.governed),
            "rejected": len(result.rejected),
            "quarantined": len(result.quarantined),
            "failed": len(failures),
        },
    )
    return {
        "trace_id": str(trace),
        "scored": len(result.governed),
        "rejected": len(result.rejected),
        "quarantined": len(result.quarantined),
        "failed": len(failures),
    }


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
