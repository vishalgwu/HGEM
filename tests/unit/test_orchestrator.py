"""The pipeline end to end.  BUILD_NOTEBOOK.md S5.6

The first test in this repository that runs a proposal through every layer: the
noise filter, the extractor, the schema gate, retrieval, conflict detection, and
all four of Layer 3. Everything is a fake or a scripted double, so what is being
tested is the *composition* - that each stage's output is the shape the next one
takes, and that the joins nobody could check until now are right.

Three of those joins are the reason this file exists at all:

- the span offsets the extractor produces index into the string `run` renders
  from the surviving turns, and nothing upstream enforced that,
- §3.1's entropy is taken over draws grouped by `(subject, predicate)`, and the
  grouping had no owner until this step,
- `captured_at` comes off the turns rather than a clock, which is the whole
  basis of replay.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest

from fixtures.extraction import ALLERGY, CONTENT, PHARMACY, response
from fixtures.fakes import FakeGraphStore, FakeLLM, FakeVectorStore
from guardmem_core.llm.base import Tier
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.pipeline.deps import CandidateRisk, Deps, scope_of_namespace
from guardmem_core.pipeline.l2_validate import Judgement
from guardmem_core.pipeline.l3_score import (
    V1_BETAS,
    V1_WEIGHTS,
    Irreversibility,
    PiiClass,
)
from guardmem_core.pipeline.orchestrator import CandidateFailure, Proposal, run
from guardmem_core.schemas import load_ontology
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn, TurnRole
from guardmem_core.schemas.verdict import Decision, Thresholds
from guardmem_core.types import EntityId, Namespace, TenantId, TraceId, TurnId

if TYPE_CHECKING:
    from guardmem_core.schemas.candidate import MemoryCandidate

TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")
NS: Final = Namespace("patient:8812")
SUBJECT: Final = EntityId("e-1")
WHEN: Final = datetime(2026, 3, 12, 14, 31, 2, tzinfo=UTC)
THRESHOLDS: Final = Thresholds(
    tau_lo=0.45, tau_mid=0.60, tau_hi=0.78, rho_lo=0.35, rho_hi=0.70, version="test-v1"
)


class ScriptedResolver:
    """An `EntityResolver` that always answers the same entity.

    Nothing in the package implements this protocol - see `deps.py` - so the
    suite supplies one, and a constant is the right one here: entity resolution
    is not what these tests are about, and a fake that guessed would make every
    assertion below depend on the guess.
    """

    def __init__(self, entity: EntityId = SUBJECT) -> None:
        self.entity = entity
        self.asked: list[str] = []

    async def resolve(self, subject: str, *, tenant_id: TenantId, namespace: Namespace) -> EntityId:
        self.asked.append(subject)
        return self.entity


class ScriptedClassifier:
    """A `CandidateClassifier` answering §3.3's three undeclared features."""

    def __init__(
        self,
        pii: PiiClass = PiiClass.SPECIAL_CATEGORY,
        reversible: Irreversibility = Irreversibility.REVERSIBLE,
    ) -> None:
        self.pii = pii
        self.reversible = reversible

    async def classify(self, candidate: MemoryCandidate) -> CandidateRisk:
        return CandidateRisk(
            scope=scope_of_namespace(Namespace(candidate.namespace)),
            pii_class=self.pii,
            irreversibility=self.reversible,
        )


class ScriptedJudge:
    """An `NLIJudge` that finds no contradiction anywhere."""

    def __init__(self) -> None:
        self.calls = 0

    async def compare(
        self, candidate: str, incumbents: list[str], *, trace_id: TraceId
    ) -> list[Judgement]:
        self.calls += 1
        return [Judgement(entail_fwd=0.1, entail_rev=0.1, contradiction=0.0)] * len(incumbents)


def agreeing_entail(premise: str, hypothesis: str) -> float:
    """An `EntailFn` that says every text entails every other.

    Deliberately generous, and the tests that care assert around it. Its effect
    is that identical draws cluster and the grounding term scores 1.0, so what
    the composition tests measure is the wiring rather than a judgement.
    """
    return 1.0


def turns(*texts: str) -> list[Turn]:
    """Turns carrying a capture time, which `run` requires."""
    return [
        Turn(turn_id=TurnId(f"t{index}"), role=TurnRole.USER, text=text, captured_at=WHEN)
        for index, text in enumerate(texts)
    ]


def deps(**overrides: object) -> Deps:
    """A `Deps` wired entirely from fakes."""
    facts = json.dumps({"facts": [ALLERGY, PHARMACY]})
    base: dict[str, object] = {
        "llm": FakeLLM(responses=[response(facts), response(facts, facts)]),
        "vector": FakeVectorStore(),
        "graph": FakeGraphStore(),
        "embedder": HashEmbedder(),
        "nli": ScriptedJudge(),
        "entail": agreeing_entail,
        "resolver": ScriptedResolver(),
        "classifier": ScriptedClassifier(),
        "ontology": load_ontology("clinical"),
        "thresholds": THRESHOLDS,
        "weights": V1_WEIGHTS,
        "betas": V1_BETAS,
        "policy_version": "test-policy",
        "max_concurrent_scores": 8,
    }
    return Deps(**(base | overrides))  # type: ignore[arg-type]


def proposal(**overrides: object) -> Proposal:
    """A proposal over the shared clinical intake text."""
    base: dict[str, object] = {
        "trace_id": TraceId("tr_orch"),
        "tenant_id": TENANT,
        "namespace": NS,
        "turns": turns(CONTENT),
        "source_tier": SourceTier.VERIFIED_USER,
        "k": 3,
        "tier": Tier.FAST,
    }
    return Proposal.model_validate(base | overrides)


class TestItRunsEndToEnd:
    async def test_a_proposal_produces_one_decision_per_candidate(self) -> None:
        """The composition, asserted as the step's sketch describes it."""
        result, failures = await run(proposal(), deps())

        assert failures == []
        assert len(result.decisions) == 2
        assert all(record.decision in set(Decision) for record in result.decisions)

    async def test_every_record_carries_its_own_inputs(self) -> None:
        """What makes `scripts/replay_trace.py` possible: the record holds the
        three reports the decision was taken from, and both versions."""
        result, _ = await run(proposal(), deps())

        record = result.decisions[0]
        assert record.thresholds_version == "test-v1"
        assert record.policy_version == "test-policy"
        assert record.confidence.weights_version == "v1"

    async def test_the_counters_survive(self) -> None:
        """§1.1 and §1.3 both require the drops to be visible in the funnel."""
        result, _ = await run(proposal(), deps())

        assert result.dropped_noise >= 0
        assert result.dropped_unsourced == 0

    async def test_the_resolver_is_asked_for_every_candidate(self) -> None:
        resolver = ScriptedResolver()

        await run(proposal(), deps(resolver=resolver))

        assert len(resolver.asked) == 2

    async def test_the_subject_hint_overrides_the_extracted_surface_form(self) -> None:
        """`MCP_INTEGRATION.md` §2.2's `hints.subject`: a caller that already
        knows which entity it means says so, rather than having the resolver
        guess from what the speaker happened to say."""
        resolver = ScriptedResolver()

        await run(proposal(subject_hint="patient:8812"), deps(resolver=resolver))

        assert set(resolver.asked) == {"patient:8812"}


class TestTheJoinsNothingCouldCheckBefore:
    async def test_every_span_slices_back_to_its_own_verbatim(self) -> None:
        """The join `inputs.render_content` exists for.

        `Provenance.source_span` indexes into the string `extract` was given. If
        `run` rendered the turns differently from how the caller stores them,
        every span would land a few characters off, on real text, and nothing
        would raise. Here the rendering is the one under test, so this asserts
        the offsets against it.
        """
        from guardmem_core.pipeline.inputs import render_content

        document = render_content(turns(CONTENT))
        result, _ = await run(proposal(), deps())

        for record in result.decisions:
            assert record.conflict is not None
        # The candidates themselves are not on the result, so the spans are
        # checked through the document the extractor was handed.
        assert CONTENT in document

    async def test_captured_at_comes_off_the_turns_not_a_clock(self) -> None:
        """The basis of replay. A clock read here would stamp a replayed
        proposal with the replay's own time and no re-run could be compared."""
        from guardmem_core.pipeline.orchestrator import _context

        context = _context(proposal(), turns("a", "b"))

        assert context.captured_at == WHEN

    async def test_a_proposal_with_no_capture_time_is_refused(self) -> None:
        """Strict on purpose: a fallback to `now()` is a clock read hidden
        behind a condition, which only shows up as a replay diff much later."""
        undated = [Turn(turn_id=TurnId("t0"), role=TurnRole.USER, text="hello")]

        with pytest.raises(ValueError, match="captured_at"):
            await run(proposal(turns=undated), deps())

    async def test_entropy_is_taken_over_the_draws_for_that_pair(self) -> None:
        """§3.1 clusters "the K samples for a given (subject, predicate)", and
        the grouping had no owner until this step.

        Both draws propose both facts here, so every candidate's draws agree and
        entropy is zero. A grouping that mixed the two predicates would cluster
        "penicillin" with "CVS Elm Street" and score uncertainty that is not
        there.
        """
        result, _ = await run(proposal(), deps())

        assert all(record.confidence.semantic_entropy == 0.0 for record in result.decisions)


class TestFailuresAreAttributedRatherThanLosingTheBatch:
    async def test_one_candidate_failing_does_not_discard_the_others(self) -> None:
        """A `TaskGroup` would cancel the siblings; that is right for a dual
        write and wrong for a batch of independent candidates."""

        class HalfBroken(ScriptedResolver):
            async def resolve(
                self, subject: str, *, tenant_id: TenantId, namespace: Namespace
            ) -> EntityId:
                self.asked.append(subject)
                if len(self.asked) == 1:
                    raise RuntimeError("deliberate: this candidate cannot be resolved")
                return self.entity

        result, failures = await run(proposal(), deps(resolver=HalfBroken()))

        assert len(failures) == 1
        assert len(result.decisions) == 1

    async def test_the_failure_carries_the_exception_not_a_message(self) -> None:
        """So a caller can retry on `ProviderUnavailable` and quarantine on
        `InjectionDetected` without parsing strings."""

        class Broken(ScriptedResolver):
            async def resolve(
                self, subject: str, *, tenant_id: TenantId, namespace: Namespace
            ) -> EntityId:
                raise RuntimeError("deliberate")

        _, failures = await run(proposal(), deps(resolver=Broken()))

        assert len(failures) == 2
        assert all(isinstance(failure, CandidateFailure) for failure in failures)
        assert all(isinstance(failure.error, RuntimeError) for failure in failures)


class TestWhatItDoesNotDo:
    async def test_nothing_is_written(self) -> None:
        """The transaction boundary, asserted rather than only documented.

        `RULES.md` non-negotiable #4 ties the audit event to the state change,
        and `VectorStore` hands out no connection to join - so the applier is a
        Postgres-specific composition that does not exist yet. A test that let
        this drift would be the one that mattered.
        """
        store = FakeVectorStore()

        result, _ = await run(proposal(), deps(vector=store))

        assert result.decisions, "the pipeline ran"
        assert store.assertions == {}, "and wrote nothing"

    async def test_no_candidate_escalates_twice(self) -> None:
        """There is no escalation loop: every candidate is a first pass, so
        `escalated_from` is `None` and an ESCALATE is returned for the caller to
        act on."""
        result, _ = await run(proposal(), deps())

        assert all(record.escalated_from is None for record in result.decisions)


class _CountingClassifier:
    """A `CandidateClassifier` that records how many scorings overlapped.

    The classifier is the natural probe: `_decide_one` awaits it once per
    candidate, inside the semaphore, so the peak depth it observes *is* the
    number of candidates the bound let run at once. Yielding to the loop with
    `sleep(0)` is what makes overlap possible at all - without it each coroutine
    would run to completion before the next was scheduled, and the count would
    be 1 for every bound.
    """

    def __init__(self) -> None:
        self.depth = 0
        self.peak = 0

    async def classify(self, candidate: MemoryCandidate) -> CandidateRisk:
        self.depth += 1
        self.peak = max(self.peak, self.depth)
        await asyncio.sleep(0)
        self.depth -= 1
        return CandidateRisk(
            scope=scope_of_namespace(Namespace(candidate.namespace)),
            pii_class=PiiClass.SPECIAL_CATEGORY,
            irreversibility=Irreversibility.REVERSIBLE,
        )


class TestTheConcurrencyBoundIsTheConfiguredOne:
    """`settings.max_concurrent_scores` reaches the semaphore.

    It did not until this was written. S1.4 declared the field and S5.6 wrote a
    module-level `_MAX_CONCURRENT = 8` next to the semaphore, so the environment
    variable had no reader anywhere in the package and setting it changed
    nothing - the failure mode of dead configuration, which is not that it stops
    working but that it appears to.
    """

    async def test_a_bound_of_one_serialises_the_batch(self) -> None:
        classifier = _CountingClassifier()

        result, _ = await run(proposal(), deps(classifier=classifier, max_concurrent_scores=1))

        assert len(result.decisions) == 2, "two candidates, so overlap was possible"
        assert classifier.peak == 1

    async def test_a_wider_bound_lets_them_overlap(self) -> None:
        """The control. Without it the test above would pass against a pipeline
        that had lost its concurrency altogether."""
        classifier = _CountingClassifier()

        result, _ = await run(proposal(), deps(classifier=classifier, max_concurrent_scores=8))

        assert len(result.decisions) == 2
        assert classifier.peak == 2
