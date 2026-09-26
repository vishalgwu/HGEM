"""One budget for a whole request, decremented as it is spent.  ADR-0012

ADR-0012 closes by naming what it deliberately did not build:

    "A true end-to-end request budget is a different mechanism (one deadline,
    threaded through and decremented) and belongs with the gateway at S8.2, where
    a request first has a deadline to thread."

This is that mechanism. It was skipped at S8.2 and is built here, before S8.4,
because S8.4's DONE WHEN is a p95 and a per-operation ceiling cannot produce one.

**The distinction ADR-0012 draws, restated.** `store_timeout_s` and
`llm_timeout_s` bound *one interaction*: no single wait exceeds the ceiling. They
say nothing about how many waits a request makes. `tenant_transaction` can already
spend its ceiling twice and a bounded acquire makes three, so a request that
touches the store four times has a worst case of twelve times a number nobody
chose as a request budget. `PRD.md` §6.1 asks for `p95 < 80 ms` on the async
accept; a stack of independent ceilings cannot be reasoned about against that.

A deadline can: it is one instant, fixed when the request arrives, and every
downstream call gets the *smaller* of its own ceiling and what remains. That is
the decrementing - not subtraction bookkeeping, but taking the minimum, which is
the only version that composes.

**Monotonic, not wall clock.** `time.monotonic()` cannot go backwards; wall clock
can, and an NTP correction mid-request would either extend a budget or expire it
instantly. A deadline that moved because a clock moved is worse than no deadline,
because it would be blamed on the handler.

**Exceeding the budget raises rather than returning a sentinel.** A caller that
had to check a return value is a caller that can forget to, and the forgetting
looks like a request that ignored its budget - which is the bug this exists to
prevent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from guardmem_core.errors import DeadlineExceeded

# `DeadlineExceeded` lives in `guardmem_core.errors` and is re-exported here so a
# caller of this module imports one name from one place. It is NOT defined here:
# `RULES.md` §2.3 is one table mapping every domain error to a status and an MCP
# code, and "handlers never invent status codes" forbids a service declaring its
# own. Adding it there meant updating the rule, the hierarchy and the drift guard
# that exists to make exactly that happen.
__all__ = ["Deadline", "DeadlineExceeded"]

# Below this, a remaining budget is not worth spending on a network round trip:
# the call would be issued and then abandoned, which costs the downstream service
# the work and the caller the latency without any chance of a useful answer.
_FLOOR_S: Final = 0.005


@dataclass(frozen=True, slots=True)
class Deadline:
    """An instant by which a request must be finished.

    Attributes:
        expires_at: A `time.monotonic()` reading. Not a wall-clock time and not a
            duration - an instant, because a duration would have to be paired with
            a start that every holder could disagree about.

    Frozen, so a handler cannot extend its own budget. A request that needs longer
    is a request whose budget was set wrong, and moving it at the point of failure
    hides that from whoever set it.
    """

    expires_at: float

    @classmethod
    def after(cls, budget_s: float) -> Deadline:
        """A deadline `budget_s` from now.

        Args:
            budget_s: How long the whole request may take.

        Returns:
            The deadline.
        """
        return cls(expires_at=time.monotonic() + budget_s)

    @property
    def remaining_s(self) -> float:
        """How much budget is left, never negative.

        Returns:
            Seconds remaining, floored at zero.

        Clamped rather than allowed to go negative because every consumer wants
        "how long may I wait", and a negative timeout is an error in some clients
        and unbounded in others - asyncpg treats `None` as forever, which is the
        worst possible reading of an exhausted budget.
        """
        return max(0.0, self.expires_at - time.monotonic())

    @property
    def expired(self) -> bool:
        """Whether there is too little left to be worth a call.

        Returns:
            True when the remaining budget is under `_FLOOR_S`.

        Not `remaining_s == 0`. A millisecond of budget buys nothing but still
        passes a naive check, so the call is issued and abandoned - the downstream
        service does the work and the caller waits for an answer it will discard.
        """
        return self.remaining_s < _FLOOR_S

    def for_call(self, ceiling_s: float) -> float:
        """The timeout one downstream call should use.

        Args:
            ceiling_s: That call's own configured ceiling - `store_timeout_s`,
                `llm_timeout_s`, whichever applies.

        Returns:
            The smaller of the ceiling and what remains.

        Raises:
            DeadlineExceeded: the budget is spent. Raised *before* the call rather
                than letting it run and time out, because a call issued with no
                budget left cannot succeed and its failure would be reported as a
                store or provider fault rather than as a budget one - sending the
                first responder to the wrong system, which is the specific
                complaint ADR-0012 makes about unnameable failures.

        **The minimum, not the remainder.** Handing a call the whole remaining
        budget would let one slow interaction consume everything and leave nothing
        for the writes that follow, so a request that needed three calls would fail
        on the second with no budget accounted to the first. Taking the minimum
        keeps each call's own ceiling meaningful *and* bounds the total.
        """
        if self.expired:
            raise DeadlineExceeded(
                f"request budget exhausted with {self.remaining_s * 1000:.1f}ms left; "
                "no downstream call was attempted"
            )
        return min(ceiling_s, self.remaining_s)
