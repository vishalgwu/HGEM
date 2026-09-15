"""The human review queue's contracts.  BUILD_NOTEBOOK.md S1.6

The HITL subsystem is built on Day 18, and the UI on Day 19; what is specified
today is the shape of a task, of a reviewer's decision, and of the diff they are
shown. Sources: `ARCHITECTURE.md` §2.7, `PRD.md` FR-9, `MCP_INTEGRATION.md` §2.7
for the wire contract of `review.decide`, and `DESIGN_SYSTEM.md` §3.2-§3.3 for
what the queue row and the task view actually need.

**`ReviewTask` references its candidate by id rather than embedding it.**
`ARCHITECTURE.md` §2.7 describes the queue as a Postgres table claimed with
`SELECT ... FOR UPDATE SKIP LOCKED`, and S3.1 gives it its own `review_task`
table; embedding a whole `MemoryCandidate` and `DecisionRecord` would be
deciding that table's storage shape four steps early. `(trace_id, candidate_id)`
already identifies the decision uniquely - `DecisionRecord` has no id of its own
- so the reference is complete, and the composed view a reviewer sees is the
response model S18/S6 assemble.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.entity import Cardinality, StoredAssertion
from guardmem_core.schemas.risk import ImpactLevel
from guardmem_core.types import (
    CandidateId,
    Namespace,
    ReviewerId,
    ReviewTaskId,
    TenantId,
    TraceId,
)

__all__ = ["Diff", "ReviewAction", "ReviewDecision", "ReviewStatus", "ReviewTask"]


class ReviewAction(StrEnum):
    """What a reviewer did with a task.

    `MCP_INTEGRATION.md` §2.7: `review.decide` takes
    `action: approve|edit|reject`. `DESIGN_SYSTEM.md` §3.2 shows a fourth
    keyboard action, `S` for skip - skip is not here, because skipping releases
    the lease and leaves the task pending. It is a queue operation, not a
    decision, and recording it as one would pollute the reviewer-agreement
    labels that `threshold_tuner.py` learns from.
    """

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ReviewStatus(StrEnum):
    """Where a task is in the queue.

    `PENDING` and `CLAIMED` come from `ARCHITECTURE.md` §2.7's lease model - two
    reviewers must never open the same task. `OVERDUE` is named by S18.2 and by
    `DESIGN_SYSTEM.md` §3.3, which puts breached tasks in a separate section
    with escalation context rather than reordering them into the main list.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    DECIDED = "decided"
    OVERDUE = "overdue"


class ReviewTask(GMModel):
    """One item in the human review queue.

    Attributes:
        task_id: The queue item. Returned to the agent as `review_task_id`
            (`MCP_INTEGRATION.md` §2.2) and taken back as `task_id` by
            `review.decide`.
        tenant_id: Owning tenant.
        trace_id: The proposal under review.
        candidate_id: The candidate under review. With `trace_id`, this names
            the `DecisionRecord` that routed it here.
        namespace: Isolation scope, which is also how tasks are routed to
            reviewers by skill (`PRD.md` FR-9.1).
        impact_level: Declared impact. Drives queue priority and, at `CRITICAL`,
            step-up re-auth on approval (`RULES.md` §4, S18.3).
        priority: `impact x staleness x SLA-burn` (`PRD.md` FR-9.1). A computed
            float rather than an enum because the queue orders by it.
        status: Queue state.
        created_at: When the task was enqueued; the age the UI shows.
        sla_due_at: When the SLA breaches. `DESIGN_SYSTEM.md` §3.2 renders it as
            remaining time, never a raw timestamp, and S18.2 sweeps it - the
            breach escalates or applies the policy default and **never**
            auto-approves.
        assigned_to: The reviewer holding it, if any.
        leased_until: When the Redis-backed lease expires and the task returns
            to the queue.
    """

    task_id: ReviewTaskId
    tenant_id: TenantId
    trace_id: TraceId
    candidate_id: CandidateId
    namespace: Namespace
    impact_level: ImpactLevel
    priority: float = Field(ge=0.0)
    status: ReviewStatus
    created_at: datetime
    sla_due_at: datetime
    assigned_to: ReviewerId | None = None
    leased_until: datetime | None = None


class ReviewDecision(GMModel):
    """A human's verdict on a task - and a labelled training example.

    `PRD.md` FR-9.3: reviewer decisions are labelled data. They feed
    `threshold_tuner.py` and are reported as Cohen's κ against the model's
    decision, which is why an edit is recorded as a first-class outcome rather
    than as an approval of something slightly different.

    Attributes:
        task_id: The task decided.
        action: What the reviewer chose.
        reason_code: Required, always. `DESIGN_SYSTEM.md` §3.2 makes the reject
            reason a required field on the form, and S18.3 requires a reason
            code on every action - an approval with no stated reason is a label
            with no signal in it.
        reviewer_id: Who decided. S18.3 is explicit that this comes from the
            token and never from the request body; carrying it as a typed field
            here is what lets that test assert on a substitution.
        decided_at: When.
        edited_object: The corrected value, on an `EDIT`. `MCP_INTEGRATION.md`
            §2.7 calls it `edited_object`, and it is an `ObjectValue` for the
            same reason the candidate's is.
        reviewer_note: Free text, optional. Never a substitute for
            `reason_code` - `PRD.md` FR-3.4 wants machine-readable rationale,
            not prose.
    """

    task_id: ReviewTaskId
    action: ReviewAction
    reason_code: str
    reviewer_id: ReviewerId
    decided_at: datetime
    edited_object: ObjectValue | None = None
    reviewer_note: str | None = None


class Diff(GMModel):
    """Proposed against on-record, as the reviewer sees it.

    `DESIGN_SYSTEM.md` §4 gives `<DiffPane>` the props `incumbent`, `proposed`
    and `cardinality`, with the rule that a supersession warning is mandatory
    when cardinality is `ONE`. That warning is derived, not stored: it is true
    exactly when `cardinality is ONE` and an incumbent exists, and a stored flag
    could disagree with the two fields it was derived from.

    Attributes:
        proposed: The candidate awaiting a decision.
        incumbent: What is currently on record, or `None` for a first value -
            `DESIGN_SYSTEM.md` §3.2 shows that case as "No allergies recorded",
            which is a different message from a conflict and needs to stay
            distinguishable.
        cardinality: The predicate's cardinality, which decides whether
            approving this retires the incumbent.
    """

    proposed: MemoryCandidate
    incumbent: StoredAssertion | None
    cardinality: Cardinality
