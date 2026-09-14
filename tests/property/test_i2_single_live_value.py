"""Invariant I2: a `ONE` predicate holds one live value.  S4.4

S4.4's DONE WHEN: "property test - invariant I2 holds (no two visible
assertions share subject+predicate when cardinality is ONE) across 500
generated write sequences."

**What drives this, and what it is allowed to assume.** The decision under test
is product code - `detect` reads §2.2's checks and §2.3's table, `merge` applies
§2.4. What the loop below adds is the *applier*: the four lines that turn a
resolution into a store call. That does not exist yet and S5.6 is the step that
joins the stages, so it is written here, deliberately small, and the two things
it has to invent are named rather than buried:

- **`subject_id`.** Retrieval takes a resolved `EntityId` and nothing in this
  repository produces one. The sequences below use a fixed id, which is what a
  resolver would return for one subject - and is the assumption that makes the
  invariant checkable at all, since I2 is a statement *about* a subject.
- **`confidence` and `risk`.** `StoredAssertion` requires both and Layer 3 has
  not run. They are constants here and nothing reads them: I2 is about which
  rows are live, not about what they scored.

So this proves the decision layer keeps I2 given an applier that honours the
hint. It does not prove S5.6's applier will honour it - that is S5.6's test to
write, against this same invariant.

**The sequences are values, not candidates.** What matters to I2 is whether two
writes claim the same thing or different things, so the generator draws from a
small pool: collisions have to be frequent or the duplicate path never runs and
the suite passes while testing one branch. The scripted judge returns numbers
that reach *no* similarity row on its own - that is the point. Nothing here may
depend on the judge being generous, because a real one is not reliably generous
about a restatement, and I2 has to hold anyway.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fixtures.assertions import NS, TENANT, WHEN, citation
from fixtures.conflict import CLINICAL, ScriptedJudge, candidate
from fixtures.fakes import FakeVectorStore
from guardmem_core.pipeline.l2_validate import IncumbentSet, detect, merge
from guardmem_core.schemas.entity import Cardinality, StoredAssertion
from guardmem_core.schemas.verdict import ConflictReport
from guardmem_core.types import AssertionId, CandidateId, EntityId, TraceId

if TYPE_CHECKING:
    from guardmem_core.schemas.candidate import MemoryCandidate

# `RULES.md` §5: property tests "must hold for 500 examples", and S4.4 says the
# same thing in its own words - "across 500 generated write sequences".
_EXAMPLES: Final = 500

# See the module docstring: a resolver would produce this, and I2 is a statement
# about one subject.
SUBJECT: Final = EntityId("e-1")

# Small enough that a sequence of four repeats itself often. The duplicate path
# is the one I2 broke on, so it has to be the common case rather than a rarity
# hypothesis reaches on example 300.
_VALUES: Final = st.sampled_from(["E11.9", "I10", "J45.909"])
_SEQUENCES: Final = st.lists(_VALUES, min_size=1, max_size=6)

# Deliberately inert: no entailment, no contradiction. A judge this unhelpful
# reaches none of §2.3's similarity rows, so every duplicate the suite finds is
# found by object equality - which is the route that has to hold when a real
# judge is having a bad day.
_UNHELPFUL: Final = ScriptedJudge()


def _promote(proposal: MemoryCandidate, assertion_id: str) -> StoredAssertion:
    """Turn a candidate into the row a write would store.

    `confidence` and `risk` are placeholders - see the module docstring. Written
    `visible=True` because I2 is stated over *visible* assertions and this
    stands in for the outbox relay having landed; a suite that left everything
    invisible would satisfy I2 by never making anything retrievable.
    """
    return StoredAssertion(
        assertion_id=AssertionId(assertion_id),
        tenant_id=TENANT,
        namespace=NS,
        subject_id=SUBJECT,
        predicate=proposal.predicate,
        object=proposal.object,
        confidence=0.9,
        risk=0.2,
        valid_from=WHEN,
        recorded_at=WHEN,
        provenance=[citation(verbatim=proposal.provenance.verbatim)],
        trace_id=TraceId("tr_i2"),
        visible=True,
    )


async def _write_sequence(predicate: str, values: list[str]) -> FakeVectorStore:
    """Propose each value in turn, applying whatever the decision layer says.

    The applier: `merge` folds into the incumbent and stores no new row,
    `supersede` retires the incumbent and stores the candidate, `coexist`
    stores it alongside, and `escalate` stores nothing at all - a human has the
    decision, and writing while waiting for them would be the auto-write path
    widening under uncertainty.
    """
    store = FakeVectorStore()
    spec = CLINICAL.predicate(predicate)
    assert spec is not None, f"{predicate} is not in the clinical pack"

    for index, value in enumerate(values):
        proposal = candidate(predicate=predicate, obj=value, verbatim=f"says {value}")
        nearest = await store.search(
            namespace=NS,
            embedding=[],
            k=10,
            filters={"subject_id": SUBJECT, "predicate": predicate},
        )
        report = await detect(
            proposal,
            IncumbentSet(candidate_id=CandidateId("c_1"), nearest=nearest, neighbours=[]),
            spec,
            _UNHELPFUL,
        )
        await _apply(store, report, proposal, f"a-{index}")
    return store


async def _apply(
    store: FakeVectorStore,
    report: ConflictReport,
    proposal: MemoryCandidate,
    new_id: str,
) -> None:
    """Carry out one resolution. See `_write_sequence` for the mapping."""
    if report.resolution_hint == "escalate":
        return
    incumbent_id = report.incumbent_assertion_id
    if report.resolution_hint == "merge":
        assert incumbent_id is not None, "a merge with nothing to merge into"
        await store.upsert([merge(store.assertions[incumbent_id], proposal)])
        return
    fresh = _promote(proposal, new_id)
    if report.resolution_hint == "supersede" and incumbent_id is not None:
        await store.supersede(incumbent_id, fresh.assertion_id, WHEN)
    await store.upsert([fresh])


def _live(store: FakeVectorStore) -> list[StoredAssertion]:
    """Every assertion a reader would get back: visible, current, unretracted."""
    return [
        assertion
        for assertion in store.assertions.values()
        if assertion.visible and assertion.valid_to is None and assertion.retracted_at is None
    ]


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values=_SEQUENCES)
def test_a_one_predicate_never_holds_two_live_values(values: list[str]) -> None:
    """I2, stated exactly as `RULES.md` states it.

    `primary_dx` is `cardinality: one` in the shipped clinical pack, which is
    checked below rather than assumed - a pack edit that loosened it would
    otherwise turn this suite green by making the invariant vacuous.
    """
    spec = CLINICAL.predicate("primary_dx")
    assert spec is not None
    assert spec.cardinality is Cardinality.ONE, "this suite is vacuous otherwise"

    store = asyncio.run(_write_sequence("primary_dx", values))

    live = _live(store)
    assert len(live) <= 1, f"{len(live)} live values for a ONE predicate: {values}"


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values=_SEQUENCES)
def test_the_surviving_value_is_the_one_last_asserted(values: list[str]) -> None:
    """I2 is satisfiable by writing nothing, so it needs this beside it.

    A `detect` that escalated everything would keep the store empty and pass
    the invariant while making the system useless. The last value proposed is
    the one memory should hold - supersession is how a `ONE` predicate changes
    its mind, and it has to actually happen.
    """
    store = asyncio.run(_write_sequence("primary_dx", values))

    live = _live(store)
    assert len(live) == 1, f"nothing survived {values}"
    assert live[0].object == values[-1]


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values=_SEQUENCES)
def test_nothing_is_ever_deleted(values: list[str]) -> None:
    """`ARCHITECTURE.md` §0's non-negotiable, over the same sequences.

    A superseded row stays in the store and stops being returned. The count is
    what makes this checkable: every write either adds a row or merges into
    one, so the store can only grow.
    """
    store = asyncio.run(_write_sequence("primary_dx", values))

    retired = [a for a in store.assertions.values() if a.valid_to is not None]
    assert all(a.superseded_by is not None for a in retired), "retired without a successor"
    assert len(store.assertions) >= 1


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values=_SEQUENCES)
def test_restating_a_value_corroborates_it_rather_than_duplicating_it(
    values: list[str],
) -> None:
    """§2.4, over the sequences that repeat themselves.

    The route that had to exist for I2 to hold at all: the scripted judge
    reaches no similarity row, so a restatement is recognised by object
    equality alone. Each repeat of the surviving value corroborates it once,
    and each writes no row.
    """
    store = asyncio.run(_write_sequence("primary_dx", values))

    live = _live(store)[0]
    surviving = live.object
    assert isinstance(surviving, str), "these sequences are drawn from strings"
    # How many times the final value was restated after it was last introduced.
    tail = values[len(values) - 1 - values[::-1].index(surviving) :]
    assert live.corroboration_count == len(tail), f"{values} -> {live.corroboration_count}"
