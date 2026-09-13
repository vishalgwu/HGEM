"""Domain identifier types.  BUILD_NOTEBOOK.md S1.5

`RULES.md` §2.1: "Domain IDs are `NewType`, not `str` [...] Passing a raw `str`
where an `AssertionId` is expected must be a type error."

Every one of these is a `str` at runtime - `NewType` has no runtime cost and no
runtime effect. The whole value is at type-check time, and it is not academic:
this system routinely holds five different opaque strings in scope at once, and
`supersede(new_id, old_id)` with the arguments the wrong way round is a silent
data-corruption bug that no test would obviously catch. `mypy --strict` rejects
it before it runs.

Because the protection is purely static, it only holds where the annotations
are. Constructing one from a plain string is explicit and intentional::

    assertion_id = AssertionId(row["id"])

That call site is the boundary where an untyped string becomes a typed id, and
it is meant to be visible. Identifiers for concepts that do not exist yet
arrive with the step that introduces them rather than being declared here in
advance - which is why `ReviewTaskId` and `ReviewerId` were added at S1.6, the
step that introduced `schemas/review.py`, and `TurnId` at S2.1 with
`schemas/turn.py`. A policy *version* is still a plain
`str`, because `MEMORY_ENGINE.md` §0 types `DecisionRecord.policy_version` that
way and the spec of record decides.
"""

from __future__ import annotations

from typing import NewType

__all__ = [
    "AssertionId",
    "CandidateId",
    "EntityId",
    "Namespace",
    "ReviewTaskId",
    "ReviewerId",
    "TenantId",
    "TraceId",
    "TurnId",
]

# The tenant that owns a row. Carried on every assertion and audit event, and
# the value Postgres RLS is set from (ARCHITECTURE.md 2.1).
TenantId = NewType("TenantId", str)

# One proposal's journey through the pipeline. Present on every span, every log
# line, every audit event and every error - MCP_INTEGRATION.md 6: "Every error
# carries trace_id so a failure in an agent log is one lookup away from the full
# decision record."
TraceId = NewType("TraceId", str)

# A proposed assertion that has not been decided yet.
CandidateId = NewType("CandidateId", str)

# A stored assertion. Distinct from CandidateId on purpose: the transition from
# candidate to assertion is exactly the moment governance has happened, and the
# two are not interchangeable before it.
AssertionId = NewType("AssertionId", str)

# A node in the entity graph - the subject or object of an assertion.
EntityId = NewType("EntityId", str)

# The isolation scope a fact belongs to: "patient:8812", "org:acme",
# "session:xyz", or the "quarantine:<tenant>" namespace that failed guardrail
# content lands in (ARCHITECTURE.md 2).
Namespace = NewType("Namespace", str)

# One human review task in the HITL queue - MCP_INTEGRATION.md 2.7 takes it as
# `task_id`, and MCP_INTEGRATION.md 2.2 returns it to the agent as
# `review_task_id`.
ReviewTaskId = NewType("ReviewTaskId", str)

# One message in a proposal, before anything has been extracted from it - added
# at S2.1, the step that introduced `schemas/turn.py`. A turn is the unit the
# noise filter keeps or drops, and a `DroppedTurn` has to name which one it ate
# (MEMORY_ENGINE.md 1.1: "you must be able to see what the filter is eating").
TurnId = NewType("TurnId", str)

# The human who decided a review task, e.g. "rn:sarah.r"
# (MCP_INTEGRATION.md 2.1). Distinct from TaskId for the usual reason: both are
# opaque strings, both appear together on every ReviewDecision, and BUILD
# NOTEBOOK S18.3 makes reviewer identity a security property - it comes from the
# token, never from the request body.
ReviewerId = NewType("ReviewerId", str)
