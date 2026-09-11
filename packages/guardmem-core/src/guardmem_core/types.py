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
it is meant to be visible. Identifiers for concepts that do not exist yet -
review tasks, policy versions - arrive with the step that introduces them
rather than being declared here in advance.
"""

from __future__ import annotations

from typing import NewType

__all__ = [
    "AssertionId",
    "CandidateId",
    "EntityId",
    "Namespace",
    "TenantId",
    "TraceId",
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
