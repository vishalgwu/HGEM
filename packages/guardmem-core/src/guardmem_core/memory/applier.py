"""Turning a decision into durable state.  ADR-0010

`pipeline.run()` reaches a decision and applies none. This is the other half,
and the reason it is a module of its own rather than four lines in the
orchestrator is one sentence of `RULES.md`: the audit event commits **in the
same transaction as the state change**, or not at all.

That sentence is why this is Postgres-specific. `VectorStore.upsert` owns its
own transaction and hands out no connection - correctly, because a protocol that
handed one out would be a protocol about Postgres. `audit_store.append` requires
one, also correctly, because that is what makes the audit row atomic with what
it records. The two cannot be composed until something owns the transaction, and
ADR-0010 makes that this.

**One transaction per candidate, not per proposal.** A batch-wide unit would
discard nineteen decided writes because the twentieth lost a supersession race.
Candidates are independent by construction - their own subject, their own
conflict report, their own audit entry - and `_score_one` already makes this
argument about scoring, where the stakes are lower.

**Every decision is audited, including the three that write nothing.** A
`REJECT` that leaves no trace cannot be explained to the person whose fact was
dropped, cannot be reviewed, and cannot be learned from: `threshold_tuner.py`
(S20.2) refits §3.4's cut points from exactly those outcomes. `DECISION` is
appended for all four; `WRITE` and `SUPERSEDE` join it where state moved.

**Three things it does not do, each waiting on a named step.**

- **No review task.** `REVIEW` is in the audit vocabulary and S18.1 builds the
  queue, so a `HITL_REVIEW` is audited and produces no ticket yet.
- **No `visible`.** The outbox event commits here; the relay applies the graph
  side and reveals the row. `ARCHITECTURE.md` §2.4 unchanged - a reader sees
  nothing until both sides land, so a partial write is unretrievable rather than
  briefly wrong.
- **No merge.** §2.4's `merge` folds a duplicate into the incumbent and "does
  not create a row" - it is an `UPDATE` of `corroboration_count` plus an
  appended citation, which is a different statement from an insert and a
  different idempotency question. A replayed insert is a no-op by derived id; a
  replayed *increment* is not, and getting that wrong inflates the one number
  §3.2 uses to decide a fact is corroborated. That deserves its own pass rather
  than a hurried `UPDATE` here, so a `merge` hint is audited as a decision and
  applied as nothing, and `Applied.reason` says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, uuid5

from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.observability.audit_store import append, append_decision
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.verdict import Decision
from guardmem_core.types import AssertionId

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.memory.vector.pgvector_store import PgVectorStore
    from guardmem_core.memory.vector.pool import Conn
    from guardmem_core.pipeline.per_candidate import GovernedCandidate
    from guardmem_core.types import TenantId, TraceId

__all__ = ["Applied", "apply"]

# §3.4's four decisions, and the one that changes state. `ESCALATE` re-runs
# Layer 3 on a bigger model and `HITL_REVIEW` waits for a person; neither is a
# write, and treating either as one would store a fact nobody approved.
_WRITES: Final = frozenset({Decision.AUTO_WRITE})

# §2.4's fold-into-the-incumbent, deferred - see the module docstring.
_MERGE: Final = "merge"


@dataclass(frozen=True, slots=True)
class Applied:
    """What became of one decision.

    Attributes:
        candidate_id: Which candidate this answers for.
        assertion_id: The row that now exists, or `None` when nothing was
            written.
        reason: Why nothing was written, or `None` when something was. A short
            stable string rather than prose: `memory.propose` returns it to a
            caller, and `RULES.md` §1.5 keeps anything that might carry a DSN or
            a span of source text out of what a caller sees.

    `assertion_id` and `reason` are never both set and never both `None`, which
    is the invariant that makes this readable at a call site: one of them
    answers "what happened".
    """

    candidate_id: str
    assertion_id: AssertionId | None
    reason: str | None


async def apply(
    governed: GovernedCandidate,
    *,
    store: PgVectorStore,
    pool: asyncpg.Pool,
    tenant_id: TenantId,
    trace_id: TraceId,
    timeout_s: float,
) -> Applied:
    """Carry out one decision, atomically, and record it on the chain.

    Args:
        governed: The candidate, the incumbent it was judged against, and the
            verdict.
        store: The tenant's store. Named concretely rather than as the Protocol
            because `write_in` and `supersede_in` are on the concrete class -
            ADR-0010 says why that is deliberate.
        pool: The process pool; this opens one transaction on it.
        tenant_id: Whose state and whose audit chain.
        trace_id: The proposal, carried onto every event.
        timeout_s: Per-statement ceiling, from `settings.store_timeout_s`.

    Returns:
        What happened, for the caller to report.

    Raises:
        StoreUnavailable: Postgres is unreachable.
        ConcurrencyConflict: a supersession lost its race. The transaction rolls
            back, so the successor is not stored beside a still-live incumbent -
            which would be two live values for a `ONE` predicate, invariant I2
            broken by a retry rather than by a bug.

    **The embedding happens before the transaction opens**, via `store.prepare`.
    Embedding is a network call and a transaction holds a pooled connection; one
    inside the other means a slow provider occupies a connection another
    tenant's request is waiting for, and a provider that hangs holds it until the
    statement timeout.

    **The `DECISION` event is appended first, before any state moves.** Ordering
    inside one transaction is invisible to a reader - they commit together or
    not at all - but it is not invisible to a person reading this function, and
    the decision is what every other effect here is downstream of.
    """
    reason = _refusal(governed)
    assertion = None if reason else _fresh(governed)
    prepared = await store.prepare([assertion]) if assertion else None
    at = datetime.now(UTC)

    async with tenant_transaction(pool, tenant_id, timeout_s=timeout_s) as connection:
        await append_decision(
            connection,
            governed.record,
            tenant_id=tenant_id,
            trace_id=trace_id,
            created_at=at,
            timeout_s=timeout_s,
        )
        if assertion is None or prepared is None:
            return Applied(str(governed.candidate.candidate_id), None, reason)

        await store.write_in(connection, prepared)
        await _audit(
            connection,
            kind="WRITE",
            payload={
                "assertion_id": str(assertion.assertion_id),
                "predicate": assertion.predicate,
            },
            tenant_id=tenant_id,
            trace_id=trace_id,
            at=at,
            timeout_s=timeout_s,
        )
        retired = governed.record.conflict.incumbent_assertion_id
        if governed.record.conflict.resolution_hint == "supersede" and retired is not None:
            await store.supersede_in(connection, retired, assertion.assertion_id, at)
            await _audit(
                connection,
                kind="SUPERSEDE",
                payload={"retired": str(retired), "by": str(assertion.assertion_id)},
                tenant_id=tenant_id,
                trace_id=trace_id,
                at=at,
                timeout_s=timeout_s,
            )
    return Applied(str(governed.candidate.candidate_id), assertion.assertion_id, None)


def _refusal(governed: GovernedCandidate) -> str | None:
    """Why this decision writes nothing, or `None` when it writes.

    Args:
        governed: The candidate and its verdict.

    Returns:
        A short stable reason, or `None`.

    Four decisions and one of them writes. The three that do not are not
    failures and their reasons are not errors: a `REJECT` is the system working,
    and a caller needs to tell it apart from a write that was attempted and
    lost.
    """
    decision = governed.record.decision
    if decision not in _WRITES:
        return f"decision_{decision.value}"
    if governed.record.conflict.resolution_hint == _MERGE:
        return "merge_not_implemented"
    return None


def _assertion_id(governed: GovernedCandidate) -> AssertionId:
    """The id an approved candidate's row gets.

    Args:
        governed: The candidate and its verdict.

    Returns:
        A `uuid5` of the trace and the candidate id.

    Derived, not random, for the reason every id on a replay path here is: a
    retried apply must be a no-op rather than a second row. The trace makes it
    distinct across proposals and the candidate id across facts within one.
    """
    candidate = governed.candidate
    name = f"guardmem/assertion/{candidate.trace_id}/{candidate.candidate_id}"
    return AssertionId(str(uuid5(NAMESPACE_URL, name)))


def _fresh(governed: GovernedCandidate) -> StoredAssertion:
    """The assertion an approved candidate becomes.

    Args:
        governed: The candidate and its verdict.

    Returns:
        A `StoredAssertion` carrying the decision's own numbers and the
        candidate's citation, invisible until the relay reveals it.

    **The id is derived, not random**, for the reason every id on a replay path
    in this repository is: a retried apply must be a no-op rather than a second
    row. `uuid5` over the trace and the candidate is stable across retries of
    the same proposal and distinct across different ones.

    `valid_from` is the citation's `captured_at` rather than `now()`. World time
    is when the fact became true, and the clock at apply time is neither - using
    it would make a fact proposed today about a conversation last week appear to
    start today, which a point-in-time query would then answer wrongly.

    `confidence` and `risk` come off the record rather than being recomputed:
    the row has to carry the numbers the *decision* was made on, or
    `replay_trace.py` reproduces a decision that disagrees with the assertion it
    produced.
    """
    candidate = governed.candidate
    record = governed.record
    return StoredAssertion(
        assertion_id=_assertion_id(governed),
        tenant_id=candidate.tenant_id,
        namespace=candidate.namespace,
        subject_id=governed.subject_id,
        predicate=candidate.predicate,
        object=candidate.object,
        confidence=record.confidence.confidence,
        risk=record.risk.risk,
        valid_from=candidate.valid_from or candidate.provenance.captured_at,
        recorded_at=datetime.now(UTC),
        provenance=[candidate.provenance],
        trace_id=candidate.trace_id,
    )


async def _audit(
    connection: Conn,
    *,
    kind: str,
    payload: dict[str, object],
    tenant_id: TenantId,
    trace_id: TraceId,
    at: datetime,
    timeout_s: float,
) -> None:
    """Append one event to the chain, inside the caller's transaction.

    Args:
        connection: The applier's transaction.
        kind: One of `AuditEvent`'s six.
        payload: The event body, JSON-safe - `canonical_json` refuses a
            `datetime` rather than stringifying one, because a digest over a
            string that `JSONB` returns unchanged verifies forever while
            disagreeing with the object it came from.
        tenant_id: Whose chain.
        trace_id: The proposal.
        at: System time, shared across every event of one apply so they sort
            together.
        timeout_s: Per-statement ceiling.

    A wrapper over `append` that fixes the four arguments every call here
    repeats. Worth it at two call sites because the pair it *cannot* get wrong -
    connection and tenant - are the two that would be silent.
    """
    await append(
        connection,
        tenant_id=tenant_id,
        trace_id=trace_id,
        kind=kind,
        payload=payload,
        created_at=at,
        timeout_s=timeout_s,
    )
