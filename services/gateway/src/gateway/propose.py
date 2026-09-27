"""The write path, both modes.  BUILD_NOTEBOOK.md S8.4

S8.4: "`mode=async` -> validate, hash payload to blob store, enqueue, return 202 +
`trace_id` in under 80 ms. `mode=strict` runs the pipeline inline with K=1 FAST."

**Why the two modes are different endpoints' worth of behaviour behind one path.**
`ADR-0003` is write-ahead accept with async evaluation, and `PRD.md` §6.1 budgets the
two paths an order of magnitude apart - 80 ms for the accept against 450 ms for
`mode=strict`. A caller that can wait gets a decision; a caller that cannot gets a
`trace_id` and a promise. Splitting them into two URLs would make the choice a
deployment concern rather than a per-request one, and the same client often wants
both: strict while a human watches, async in a batch.

**"Hash payload to blob store" is honoured as a hash, not as a blob store.** There is
no blob store in this repository and none is named before S16. What the step is
protecting is that a queued job does not carry an unbounded, unattributed body: the
`BodyHash` layer already computed `sha256` of the request, and that hash goes on the
job and into the log. The payload itself rides with the job in Redis, which is bounded
by the request size limit rather than by a bucket. A real blob store changes where the
bytes live and not what identifies them, which is why the hash is the part worth
having now.

**The enqueue is the last thing that happens.** Validation, the idempotency check and
the hash all run first, so a job only exists for a proposal that was accepted - the
alternative leaves the queue holding work the caller was told had failed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Final, Literal

from fastapi import Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from guardmem_core.llm.base import Tier
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.types import Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from gateway.auth import Principal
    from gateway.lifespan import GatewayState

__all__ = ["QUEUE", "Accepted", "Decided", "ProposeRequest", "enqueue", "trace_for"]

# Must equal `worker.main.QUEUE`. Duplicated because the import-linter contract "the
# services do not import each other" forbids the gateway importing the worker, and a
# shared constant in `guardmem-core` would put a queue name in the library that
# neither the eval harness nor the MCP server has any use for.
#
# A drift here does not fail: the gateway would enqueue into a queue nothing reads,
# every proposal would be accepted, and none would ever be evaluated. That is the
# worst failure this system has, so `tests/unit/test_gateway_propose.py` asserts the
# two spellings match.
QUEUE: Final = "guardmem:eval"

# The job name arq registers `worker.tasks.evaluate.evaluate` under. Same duplication,
# same reason, same test.
JOB: Final = "evaluate"

# How many turns one proposal may carry. `RULES.md` §2.2's position on unbounded
# input: a proposal is extracted by a model, so an unbounded turn list is an unbounded
# prompt and an unbounded bill. 200 is well above the 40-turn transcripts the eval
# corpus uses and far below anything that would not fit a context window.
_MAX_TURNS: Final = 200

# The scope a write requires.
WRITE_SCOPE: Final = "memory:write"


class TurnIn(BaseModel):
    """One turn as it arrives over HTTP.

    **`Turn` cannot be used directly as a request model, and finding out why is worth
    the extra class.** `GMModel` is strict: it refuses `"user"` where a `TurnRole` is
    declared and an ISO string where a `datetime` is, because a strict model is what
    stops a typo in an internal caller becoming a silently coerced value.
    `checkpoint_b_generate` hit the same wall and records it - "`model_validate_json`,
    NOT `model_validate`".

    HTTP only has strings. FastAPI parses a body into dicts and validates from those,
    so a strict model as a request schema rejects every well-formed request with a 422
    naming its own field types. This model is the lenient edge that accepts what a
    client can actually send, and `to_turn` round-trips through JSON - the one path
    the strict model does accept.

    Attributes:
        turn_id: Stable within one proposal.
        role: Who spoke. A `Literal` rather than the enum, so an unknown role is a 422
            naming the three valid values rather than a schema error about a class.
        text: What was said.
        captured_at: When. Timezone-aware; pydantic parses the offset form clients
            send, and `Turn` requires the awareness.
    """

    turn_id: str = Field(min_length=1, max_length=64)
    role: Literal["user", "assistant", "tool"]
    text: str = Field(min_length=1, max_length=20_000)
    captured_at: datetime

    def to_turn(self) -> Turn:
        """Convert to the pipeline's strict model.

        Returns:
            The `Turn`.

        Raises:
            pydantic.ValidationError: the value is well-formed for the wire and not
                for the domain - a naive `captured_at`, for instance. Surfaces as a
                422 rather than a 500 because FastAPI is still in the request.

        Through JSON rather than by field, because that is the conversion `GMModel`
        accepts and because a field-by-field build would be a second place the two
        models' shapes have to agree.
        """
        return Turn.model_validate_json(self.model_dump_json())


class ProposeRequest(BaseModel):
    """What a caller submits.

    Attributes:
        namespace: Isolation scope within the tenant. Required, because a default
            namespace is a write into somebody's primary memory that nobody asked for.
        turns: The conversation to extract from, as wire models - see `TurnIn` on why
            the strict `Turn` cannot be one. **No tenant field**, deliberately:
            the tenant comes from the credential and nowhere else, which is the
            property `dependencies.vector_store` and the RLS policy depend on.
        mode: `async` for the write-ahead accept, `strict` to wait for a decision.
            Defaults to `async` because that is the path with the SLA a caller is
            most likely to be relying on, and because the slow path should be the one
            you ask for.
        source_tier: How far the content may be trusted. Defaults to
            `unverified_user`, which is `MCP_INTEGRATION.md` §2.2's own default and
            the only safe one: a gateway cannot vouch for a caller's upstream, and
            `RULES.md` §4 caps what a tier may auto-write.
    """

    namespace: str = Field(min_length=1, max_length=200)
    turns: list[TurnIn] = Field(min_length=1, max_length=_MAX_TURNS)
    mode: Literal["async", "strict"] = "async"
    source_tier: SourceTier = SourceTier.UNVERIFIED_USER


class Accepted(BaseModel):
    """What `mode=async` returns, with 202.

    Attributes:
        trace_id: What the caller holds onto. Every audit event, every decision and
            the job's own result key carry it, so it is the one handle that reaches
            all three.
        status: Always `accepted`. Named rather than implied by the 202 because a
            client reading a body should not have to consult the status line.
        body_hash: `sha256` of the request, from the `BodyHash` layer. Echoed so a
            caller can prove which bytes were accepted - which is what makes a
            replayed write checkable rather than merely idempotent.
    """

    trace_id: str
    status: Literal["accepted"] = "accepted"
    body_hash: str | None = None


class Decided(BaseModel):
    """What `mode=strict` returns, with 200.

    Attributes:
        trace_id: As above.
        status: Always `decided`.
        scored: How many candidates were governed.
        written: How many of those became an assertion row. Rows land invisible
            and the outbox relay reveals them, so a read straight after a write
            may not see one yet.
        rejected: How many the schema gate refused.
        quarantined: How many went to the quarantine namespace.
        failed: How many candidates raised, while scored or while applied.
            Reported rather than dropped: a fact the caller submitted that simply
            vanished from the counts would be silent, in the direction that
            matters.

    Counts rather than the candidates themselves. A write response that carried the
    stored facts would be a read the caller did not ask for and did not pay a scope
    for - `memory:write` is not `memory:read`, and `RULES.md` §1.5 keeps internal
    state out of what a caller sees.
    """

    trace_id: str
    status: Literal["decided"] = "decided"
    scored: int = 0
    written: int = 0
    rejected: int = 0
    quarantined: int = 0
    failed: int = 0


def trace_for(namespace: str) -> TraceId:
    """Mint a trace id for one proposal.

    Args:
        namespace: Unused in the value and present so a caller cannot pass one by
            accident - see below.

    Returns:
        A fresh `TraceId`.

    **Server-minted, never taken from the request.** A caller-supplied trace id would
    let one tenant write audit events under another's trace, and the audit chain is
    the thing this system is for. `X-Request-Id` is the header a caller may set, and
    it is deliberately a different value with a different job: correlation, not
    identity.
    """
    del namespace
    return TraceId(f"tr_{uuid.uuid4().hex[:12]}")


def require_write(caller: Principal) -> None:
    """Refuse a caller that cannot write.

    Args:
        caller: The authenticated principal.

    Raises:
        HTTPException: 403, naming the scope required and never the scopes held.

    403 rather than 401: the credential was valid. A 401 would make a client retry an
    authentication that already succeeded.
    """
    if not caller.permits(WRITE_SCOPE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"this credential lacks the {WRITE_SCOPE} scope",
        )


async def enqueue(
    state: GatewayState,
    tenant: TenantId,
    trace: TraceId,
    body: ProposeRequest,
    body_hash: str | None,
) -> None:
    """Hand one proposal to the worker.

    Args:
        state: For the queue client.
        tenant: Whose proposal this is. Carried on the job rather than inferred,
            because the worker has no request and no credential to derive it from -
            this is the one place the tenant crosses a process boundary, which is why
            the job body is the thing to read when auditing isolation.
        trace: The id the caller was given.
        body: The validated request.
        body_hash: `sha256` of the request, for the log and the job.

    **`_job_id` is the trace id.** arq deduplicates on it, so a retried enqueue for
    one accepted proposal cannot produce two evaluations - which matters because the
    gateway returns 202 before the job runs, and a client that times out on the
    *response* may retry a request that was already queued. Idempotency at the HTTP
    layer covers the caller's replay; this covers ours.

    The payload is plain JSON. A job crosses Redis to another process, possibly one
    deployed later, so `model_dump(mode="json")` rather than the model - and
    `tasks.evaluate` re-validates on the way out for that reason.
    """
    await state.queue.enqueue_job(
        JOB,
        {
            "tenant_id": str(tenant),
            "trace_id": str(trace),
            "namespace": body.namespace,
            "turns": [turn.model_dump(mode="json") for turn in body.turns],
            "source_tier": body.source_tier.value,
            # `K` and the tier are the queue path's, not `mode=strict`'s: the worker
            # is not user-blocking, so it draws the full sample `MEMORY_ENGINE.md`
            # §1.2 wants rather than the one-shot the inline path is limited to.
            "k": state.settings.default_k,
            "tier": Tier.BALANCED.value,
            "body_hash": body_hash,
        },
        _job_id=str(trace),
        _queue_name=QUEUE,
    )


def namespace_of(body: ProposeRequest) -> Namespace:
    """The validated namespace as the pipeline's own type.

    Args:
        body: The request.

    Returns:
        A `Namespace`.

    A one-line conversion with a name, because `RULES.md` §2.1's whole argument for
    `NewType` is that the types are worth having where they are actually seen - and a
    bare `Namespace(body.namespace)` at three call sites is three chances to pass a
    tenant instead.
    """
    return Namespace(body.namespace)


IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=200,
        description="Replay this write safely. A repeat returns the stored response.",
    ),
]
"""The header S8.3's store is keyed by, declared here because this is its first caller.

Bounded at 200 characters for the reason every other input is: it becomes part of a
Redis key, and an unbounded header is an unbounded key.
"""


def request_body_hash(request: Request) -> str | None:
    """The hash the `BodyHash` layer computed.

    Args:
        request: The live request.

    Returns:
        `sha256` of the body, or None for a body-less method.

    Resolved through `BodyHash`'s collector rather than read as a value, and the
    difference is load-bearing: that layer runs before the body exists, so it records
    chunks as the handler reads them. Calling this *after* FastAPI has parsed the body
    is what makes the digest the digest of what was actually received - which is the
    property `source_hash` needs in the audit trail.

    Two hashes of one body is two things that can disagree, which is why no caller
    computes its own.
    """
    from gateway.middleware.throttle import body_hash_of

    return body_hash_of(dict(request.scope.get("state") or {}))
