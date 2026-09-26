"""The end-to-end request budget.  ADR-0012

ADR-0012 defers this mechanism and names it precisely: "one deadline, threaded
through and decremented". Both halves are tested here - the arithmetic of one
deadline, and the taking-the-minimum that makes threading it compose.

**What a per-operation ceiling cannot do, stated as the thing under test.**
`store_timeout_s` bounds one interaction. A request making four of them has a
worst case of four times a number nobody chose as a request budget, and
`PRD.md` §6.1 asks for `p95 < 80 ms`. `test_a_call_never_gets_more_than_what_remains`
is the property that closes the gap.

No Redis, no Postgres, no HTTP: this is arithmetic over a monotonic clock. The
only sleeps are small and are comparisons of remaining budget against elapsed
time, not timing assertions about a server.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from gateway.deadline import Deadline, DeadlineExceeded


class TestTheBudgetIsOneInstant:
    """Not a duration, so no two holders can disagree about when it started."""

    def test_a_fresh_deadline_has_roughly_its_budget_left(self) -> None:
        """Roughly, because constructing it costs a little time.

        Asserted as a band rather than an equality: an exact comparison against a
        clock is a test that fails on a slow machine for no reason.
        """
        deadline = Deadline.after(5.0)

        assert 4.9 < deadline.remaining_s <= 5.0

    def test_remaining_is_never_negative(self) -> None:
        """A negative timeout means "forever" to asyncpg and "error" to others.

        Clamping is what makes an exhausted budget safe to hand to any client -
        the worst possible reading of a spent budget is an unbounded wait.
        """
        assert Deadline.after(-10.0).remaining_s == 0.0

    def test_the_deadline_cannot_be_extended(self) -> None:
        """Frozen, so a handler cannot give itself more time.

        A request needing longer is a request whose budget was set wrong, and
        moving it at the point of failure hides that from whoever set it.
        """
        deadline = Deadline.after(1.0)

        with pytest.raises(AttributeError):
            deadline.expires_at = time.monotonic() + 100  # type: ignore[misc]


class TestTheBudgetIsMonotonic:
    """A clock correction must not move a request's budget."""

    def test_it_is_built_from_the_monotonic_clock(self) -> None:
        """Wall clock can go backwards; an NTP step mid-request would either
        extend a budget or expire it instantly, and the handler would be blamed.

        Compared against `time.monotonic()` rather than by inspecting the source,
        so the test fails if the implementation switches to `time.time()` - the
        two are far apart in absolute value, which is what makes this detectable.
        """
        deadline = Deadline.after(10.0)

        assert abs(deadline.expires_at - (time.monotonic() + 10.0)) < 0.1


class TestThreadingItThroughTakesTheMinimum:
    """The half of ADR-0012's sentence that makes a budget compose."""

    def test_a_generous_budget_leaves_the_call_ceiling_intact(self) -> None:
        """With plenty of budget, a call still gets only its own ceiling.

        Handing it the whole remaining budget would let one slow interaction
        consume everything and leave nothing for the writes that follow - so a
        three-call request would fail on the second with none of the time
        accounted to the first.
        """
        deadline = Deadline.after(30.0)

        assert deadline.for_call(5.0) == 5.0

    def test_a_call_never_gets_more_than_what_remains(self) -> None:
        """The property a per-operation ceiling cannot provide.

        This is the whole point of the mechanism: the store's ceiling is 5s, the
        request has 0.5s left, and the call gets 0.5s. Without this the request
        would spend 5s on a call it had no budget for and report a store fault.
        """
        deadline = Deadline.after(0.5)

        assert deadline.for_call(5.0) == pytest.approx(0.5, abs=0.05)

    async def test_the_budget_shrinks_as_it_is_spent(self) -> None:
        """Two calls in sequence do not each get the original budget."""
        deadline = Deadline.after(0.5)
        first = deadline.for_call(5.0)

        await asyncio.sleep(0.2)
        second = deadline.for_call(5.0)

        assert second < first


class TestAnExhaustedBudgetRefusesBeforeCalling:
    """Refusing early is what makes the failure nameable."""

    def test_for_call_raises_once_the_budget_is_spent(self) -> None:
        """Raised *before* the call, not by letting it time out.

        A call issued with no budget left cannot succeed, and its failure would be
        reported as a store or provider fault - sending the first responder to the
        wrong system, which is the specific complaint ADR-0012 makes.
        """
        with pytest.raises(DeadlineExceeded):
            Deadline.after(-1.0).for_call(5.0)

    def test_a_budget_too_small_to_be_useful_counts_as_spent(self) -> None:
        """Not `remaining == 0`.

        A millisecond passes a naive check, so the call is issued and abandoned:
        the downstream service does the work and the caller waits for an answer it
        will discard. The floor is what prevents paying for nothing.
        """
        deadline = Deadline.after(0.001)

        assert deadline.expired
        with pytest.raises(DeadlineExceeded):
            deadline.for_call(5.0)

    def test_the_message_names_the_budget_and_not_the_store(self) -> None:
        """An operator reading this must not go and look at Postgres."""
        with pytest.raises(DeadlineExceeded, match=r"budget exhausted"):
            Deadline.after(-1.0).for_call(5.0)


class TestItIsTheErrorTheContractPublishes:
    """`RULES.md` §2.3 is one table, and this is in it."""

    def test_it_is_a_504_and_retryable(self) -> None:
        """504 rather than 503: the service was reached and accepted the request,
        and what failed was finishing in time. A client acts on those differently,
        and only this one is a hint to send a smaller request.
        """
        assert DeadlineExceeded.http_status == 504
        assert DeadlineExceeded.retryable

    def test_it_lives_in_the_one_hierarchy(self) -> None:
        """Defined in `guardmem_core.errors`, not in the gateway.

        "Handlers never invent status codes" - so a service declaring its own
        domain error with its own status would be the violation. `gateway.deadline`
        re-exports it; `tests/unit/test_errors.py` guards the membership.
        """
        from guardmem_core.errors import DeadlineExceeded as Canonical

        assert DeadlineExceeded is Canonical
