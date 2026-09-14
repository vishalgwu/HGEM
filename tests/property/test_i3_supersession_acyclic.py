"""Invariant I3: supersession is acyclic.  `RULES.md` §5

The fourth of the four invariants `RULES.md` §5 says are property-tested, and
the one that was not. CHECKPOINT B's sign-off block lists "I1/I2/I4 green" under
automated checks against a requirement that names I1-I4, and `tests/property/`
held three files - which is the way a missing test fails: silently, and only
where somebody writes down what was supposed to have run.

**What I3 is a statement about.** `StoredAssertion.superseded_by` points at the
assertion that replaced this one, so the retirements in a tenant form a directed
graph. A cycle in it means A was replaced by B and B, transitively, by A - and
then "what is believed now?" has no answer, because every candidate for the
answer has been retired by another candidate. `MEMORY_ENGINE.md` §2.3's
supersession is what writes those edges and `VectorStore.supersede` is the only
call that does.

**Two facts hold the invariant up, and only one of them is in the store.**

1. `supersede` refuses an `old_id` that is not live - `UPDATE ... WHERE valid_to
   IS NULL`, a zero-row update, `ConcurrencyConflict`. So no assertion is ever
   retired twice and every node has **out-degree at most one**. That is in the
   store, and `test_the_store_refuses_to_retire_an_assertion_twice` pins it.
2. The successor is an assertion that did not exist when the predecessor was
   written. That is **not** in the store, and cannot be: `supersede` takes two
   `AssertionId`s and has no way to ask which of them is older.

Out-degree one alone does not give acyclicity - a functional graph is exactly
what a cycle lives in - so (2) is load-bearing, and it belongs to the applier.
`TestTheStoreDoesNotEnforceThisByItself` drives the store into a two-cycle
directly to make that concrete, because a property test over the applier reads
like a proof about the store otherwise, and the next person to write an applier
would inherit the wrong guarantee.

**So this proves I3 of a write path that mints its successor, which is the only
write path the spec describes** - §2.3 supersedes an incumbent with the
*candidate*, and a candidate is new by construction. It is the same shape as
`test_i2_single_live_value.py`: the applier is four lines, written here rather
than imported, because `pipeline/orchestrator.py` returns decisions and nothing
in this repository carries them out yet.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import TYPE_CHECKING, Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fixtures.assertions import NS, TENANT, WHEN, stored_assertion
from fixtures.fakes import FakeVectorStore
from guardmem_core.errors import ConcurrencyConflict
from guardmem_core.types import AssertionId

if TYPE_CHECKING:
    from guardmem_core.schemas.entity import StoredAssertion

# `RULES.md` §5: property tests "must hold for 500 examples".
_EXAMPLES: Final = 500

# How many supersessions one generated history performs. Small, and deliberately
# so: a cycle needs at least two edges, and chains of three or four exercise the
# transitive case without the walk below becoming the thing under test.
_HISTORIES: Final = st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=6)


def _cycle_in(assertions: dict[AssertionId, StoredAssertion]) -> list[AssertionId] | None:
    """Return a `superseded_by` cycle, or `None` if the graph is acyclic.

    Args:
        assertions: Every assertion written, live and retired, by id.

    Returns:
        The ids on the first cycle found, in the order they were walked, so a
        hypothesis failure names the loop rather than merely asserting one
        exists. `None` when there is none.

    Every node has out-degree at most one, so this is a walk rather than a
    search: follow `superseded_by` from each node and stop at the first id seen
    twice on *this* walk. An edge pointing at an id nothing wrote is not a cycle
    and terminates the walk - `supersede` accepts any `AssertionId` for its
    successor, including one that was never stored.
    """
    for start in assertions:
        path: list[AssertionId] = []
        seen: set[AssertionId] = set()
        current: AssertionId | None = start
        while current is not None and current in assertions:
            if current in seen:
                return [*path, current]
            seen.add(current)
            path.append(current)
            current = assertions[current].superseded_by
    return None


async def _apply(history: list[int]) -> FakeVectorStore:
    """Write one fact and supersede it `len(history)` times.

    Args:
        history: One entry per supersession. The value picks which *already
            written* assertion the successor is proposed against, modulo the
            number written so far - so the generator can aim a supersession at a
            retired assertion as readily as at the live one, which is how a
            naive applier would build a cycle.

    Returns:
        The store, holding every assertion the history wrote.

    The applier is the two lines after the `try`: mint a successor, retire the
    incumbent in its favour. `ConcurrencyConflict` is swallowed rather than
    raised because it is the *expected* answer when the history aims at an
    already-retired assertion - S3.2 made that a refusal, and a write path that
    hit one would re-read and re-propose. What must not happen either way is a
    cycle, and that is what the caller asserts.
    """
    store = FakeVectorStore()
    written = [stored_assertion(predicate="allergy", obj="penicillin")]
    await store.upsert(written)

    for index in history:
        incumbent = written[index % len(written)]
        successor = stored_assertion(predicate="allergy", obj=f"value-{len(written)}")
        await store.upsert([successor])
        written.append(successor)
        try:
            await store.supersede(incumbent.assertion_id, successor.assertion_id, WHEN)
        except ConcurrencyConflict:
            # The incumbent was already retired. The real applier re-reads; this
            # one moves on, because the invariant has to hold in both branches.
            continue
    return store


@settings(max_examples=_EXAMPLES, deadline=None)
@given(history=_HISTORIES)
def test_supersession_never_forms_a_cycle(history: list[int]) -> None:
    """I3 over generated write sequences.

    `asyncio.run` per example rather than an async test: hypothesis drives this
    synchronously, and the alternative - an `async def` under
    `asyncio_mode = "auto"` - runs the whole 500-example campaign inside one
    event loop and reports a hypothesis failure as a loop error.
    """

    store = asyncio.run(_apply(history))

    cycle = _cycle_in(store.assertions)

    assert cycle is None, f"superseded_by formed a cycle: {cycle}"


class TestWhatHoldsTheInvariantUp:
    """The two facts named in the module docstring, each asserted on its own."""

    async def test_the_store_refuses_to_retire_an_assertion_twice(self) -> None:
        """Fact 1: out-degree at most one, enforced by `WHERE valid_to IS NULL`.

        Without this a single assertion could carry two `superseded_by` values
        over its lifetime, the second silently overwriting the first - and the
        overwritten edge is a retirement the audit trail can no longer explain.
        """
        store = FakeVectorStore()
        incumbent = stored_assertion()
        first, second = stored_assertion(), stored_assertion()
        await store.upsert([incumbent, first, second])

        await store.supersede(incumbent.assertion_id, first.assertion_id, WHEN)

        with pytest.raises(ConcurrencyConflict, match="not live"):
            await store.supersede(incumbent.assertion_id, second.assertion_id, WHEN)
        assert store.assertions[incumbent.assertion_id].superseded_by == first.assertion_id

    async def test_a_chain_of_supersessions_stays_a_chain(self) -> None:
        """Fact 2, in its simplest form: each successor is newly minted, so the
        walk from the oldest assertion reaches the newest and stops."""
        store = FakeVectorStore()
        chain = [stored_assertion(obj=f"value-{index}") for index in range(4)]
        await store.upsert(chain)
        for older, newer in pairwise(chain):
            await store.supersede(older.assertion_id, newer.assertion_id, WHEN)

        assert _cycle_in(store.assertions) is None
        assert store.assertions[chain[-1].assertion_id].superseded_by is None, (
            "the head of the chain is the live assertion"
        )


class TestTheStoreDoesNotEnforceThisByItself:
    """The negative half, and the reason the module docstring names an applier.

    Read the property test above as "the store keeps I3" and the next write path
    inherits a guarantee that is not there. `supersede` compares nothing about
    the two ids it is handed - it cannot, they are both `AssertionId` over `str`
    - so a caller that proposes an *existing* assertion as the successor of a
    live one closes a loop, and every layer below accepts it.
    """

    async def test_two_assertions_can_be_made_to_supersede_each_other(self) -> None:
        store = FakeVectorStore()
        first, second = stored_assertion(obj="a"), stored_assertion(obj="b")
        await store.upsert([first, second])

        await store.supersede(first.assertion_id, second.assertion_id, WHEN)
        await store.supersede(second.assertion_id, first.assertion_id, WHEN)

        assert _cycle_in(store.assertions) == [
            first.assertion_id,
            second.assertion_id,
            first.assertion_id,
        ], (
            "if this ever stops finding a cycle the store has grown a check, and "
            "the property test above should be restated against it"
        )
        assert all(stored.valid_to is not None for stored in store.assertions.values()), (
            "and nothing is believed any more, which is what makes a cycle a bug"
        )


def test_the_fixtures_this_rests_on_are_the_tenant_and_namespace_under_test() -> None:
    """A guard on the builder rather than on the invariant.

    `stored_assertion` defaults to one tenant and one namespace, and I3 is
    stated per tenant. If those defaults ever varied per call the histories above
    would be spread across tenants, no supersession would find its incumbent,
    and every example would pass while testing nothing.
    """
    first, second = stored_assertion(), stored_assertion()

    assert first.tenant_id == second.tenant_id == TENANT
    assert first.namespace == second.namespace == NS
    assert first.assertion_id != second.assertion_id, "but the ids are distinct"
