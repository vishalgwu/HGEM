"""The two write tools, and the dispatch around all four.  S6.2

Split from `test_mcp_tools.py` at `RULES.md` §2.4's 400-line cap, along the seam
the tool surface has: `memory.search` and `memory.get_entity` read governed
memory; `memory.propose` and `memory.commit` write to it - or would.

**`propose` governs for real now.** It runs the whole pipeline and returns real
numbers, so what these tests assert changed with it: the arguments it refuses
before spending a model call, and that the result never claims more than it did.
`applied: false` and the absent `assertion_id` are the two that matter - a
`memory.propose` returning `auto_write` in a way a caller reads as "stored"
would be the single most harmful thing this repository could ship, because the
product's whole claim is that a fact was governed before it was believed.

`commit` still refuses, and **for a reason rather than a gap**: §2.3 skips
extraction, so §3.1's entropy has no samples to be taken over, and that is 0.35
of `C` that would otherwise be handed over free.

The class these replaced walked a `MISSING_DEPENDENCIES` tuple to prove the
tools declined. That tuple outlived its contents - all three entries were closed
and the tool went on citing them - which is the drift this suite exists to catch
and did not.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fixtures.assertions import ENTITY
from fixtures.conflict import candidate
from fixtures.decisions import conflict, risk
from fixtures.extraction import response
from fixtures.fakes import FakeLLM
from fixtures.mcp import context
from guardmem_core.errors import ProviderUnavailable
from guardmem_core.pipeline.orchestrator import PipelineResult
from guardmem_core.pipeline.per_candidate import CandidateFailure, GovernedCandidate
from guardmem_core.schemas.verdict import ConfidenceReport, Decision, DecisionRecord
from guardmem_core.types import CandidateId, TraceId
from mcp_server.tools.context import ToolContext, ToolRefusedError
from mcp_server.tools.governing import result_of
from mcp_server.tools.pipeline import _K_BY_RISK_HINT, run_commit, run_propose


def governing_context(k: int = 3) -> ToolContext:
    """A context whose model extracts nothing, so `propose` runs and decides.

    Args:
        k: How many samples the risk hint will ask for. §1.2's ladder is
            low 1, default 3, high 5.

    Returns:
        A `ToolContext` whose `propose` reaches a real, empty result.

    **The point is that it runs.** These tests used to prove "this argument is
    accepted" by asserting the call reached a `pipeline is not wired` refusal,
    which stopped being a signal the moment the pipeline was wired. Extracting
    zero facts is the cheapest way to reach a real result: no candidates means
    no resolver, no store and no scoring, so the assertion is about argument
    handling and nothing else.

    **`k` is a parameter because `extract` refuses a short sample set**, and
    that refusal is load-bearing rather than fussy - `MEMORY_ENGINE.md` §3.1
    sets `H_norm := 0` at K = 1, so absorbing a provider that returned fewer
    samples than asked would *raise* confidence exactly when the provider was
    misbehaving. The canonical draw is one sample and the spread draw is the
    other `k - 1`.

    **Three replies, and the first is the noise filter.** Measured rather than
    assumed: `run()` calls `NoiseClassification`, then the canonical
    `ExtractionBatch` at temperature 0, then the spread at 0.7 with `n = k - 1`.
    Omitting the first made `FakeLLM` repeat its last reply, so the canonical
    draw received the spread's sample set and `extract` refused with "asked for
    3 samples and received 4" - a confusing failure and the right one.

    At `k = 1` there is no spread draw at all - `_draw` returns after the
    canonical one - so scripting an empty third reply would fail on
    `LLMResponse`'s own `min_length=1`, which is the model refusing to describe
    a completion that returned nothing.
    """
    nothing = json.dumps({"facts": []})
    keep_everything = json.dumps({"verdicts": []})
    draws = [response(nothing)] if k == 1 else [response(nothing), response(*[nothing] * (k - 1))]
    return context(llm=FakeLLM(responses=[response(keep_everything), *draws]))


def _empty_result() -> PipelineResult:
    """A result with no candidates, for the fields that do not depend on one."""
    return PipelineResult(
        trace_id=TraceId("tr_1"),
        governed=[],
        quarantined=[],
        rejected=[],
        dropped_noise=0,
        dropped_unsourced=0,
    )


def _one_governed() -> PipelineResult:
    """A result carrying one candidate and the decision about it."""
    return PipelineResult(
        trace_id=TraceId("tr_1"),
        governed=[
            GovernedCandidate(
                candidate=candidate(predicate="allergy", obj="penicillin"),
                subject_id=ENTITY,
                record=DecisionRecord(
                    decision=Decision.HITL_REVIEW,
                    reason_codes=["R_ABOVE_RHO_HI"],
                    confidence=ConfidenceReport(
                        semantic_entropy=0.1,
                        grounding=0.9,
                        schema_fit=1.0,
                        corroboration=0.0,
                        consistency=1.0,
                        confidence=0.91,
                        weights_version="v1",
                    ),
                    risk=risk(),
                    conflict=conflict(),
                    thresholds_version="v1",
                    policy_version="none",
                ),
            )
        ],
        quarantined=[],
        rejected=[],
        dropped_noise=0,
        dropped_unsourced=0,
    )


class TestProposeGovernsAndSaysWhatItDidNotDo:
    """`memory.propose` reaches a real decision as of S6.2's write half.

    What is unit-testable here is the *edges*: the arguments it refuses before
    spending a model call, and the result shape it produces. The full path -
    noise filter, K-sample extraction, resolver, scoring - needs a database and
    is `tests/integration/test_mcp_memory_tools.py`'s.

    The class this replaced asserted that `propose` refused, walking a
    `MISSING_DEPENDENCIES` tuple. That tuple outlived its contents: all three
    entries were closed and the tool went on citing them, which is the shape of
    failure this suite exists to catch and did not.
    """

    async def test_it_validates_arguments_before_spending_a_model_call(self) -> None:
        """A caller whose call was malformed should learn that first, and an
        extraction is the expensive thing to do before finding out."""
        with pytest.raises(ToolRefusedError, match="`source_tier` must be one of"):
            await run_propose(context(), {"content": "x", "source_tier": "gossip"})

    async def test_async_mode_is_refused_because_nothing_would_decide(self) -> None:
        """§2.2's *default* mode, refused: it means "return now, decide later",
        and the worker that decides later is S8.4."""
        with pytest.raises(ToolRefusedError, match=r"S8\.4"):
            await run_propose(context(), {"content": "x", "mode": "async"})

    async def test_an_empty_result_still_reports_that_nothing_was_applied(self) -> None:
        """`applied` is not conditional on there being candidates.

        A caller has to be able to read "nothing was stored" off any answer,
        including one where the noise filter ate everything - otherwise the
        field means "we had something and did not write it" rather than "this
        server does not write".
        """
        empty = PipelineResult(
            trace_id=TraceId("tr_1"),
            governed=[],
            quarantined=[],
            rejected=[],
            dropped_noise=3,
            dropped_unsourced=0,
        )

        payload = result_of(empty, [])

        assert payload["applied"] is False
        assert payload["candidates"] == []
        assert payload["dropped_noise"] == 3
        assert payload["status"] == "decided"

    async def test_a_decision_carries_the_fact_it_was_about(self) -> None:
        """`MEMORY_ENGINE.md` §0 gives `DecisionRecord` no identity, so the
        predicate and object can only come from the candidate. Reading them off
        the pair is what stops one verdict being printed beside another's
        fact."""
        payload = result_of(_one_governed(), [])

        entry = payload["candidates"][0]
        assert entry["predicate"] == "allergy"
        assert entry["object"] == "penicillin"
        assert entry["decision"] == "hitl_review"

    async def test_no_candidate_carries_an_assertion_id(self) -> None:
        """§2.2's example shows one on an `auto_write`. Nothing writes, so
        there is no id - and inventing one would be the single most harmful
        thing this tool could return."""
        payload = result_of(_one_governed(), [])

        assert "assertion_id" not in payload["candidates"][0]

    async def test_a_failed_candidate_is_reported_rather_than_dropped(self) -> None:
        """`run()` returns failures beside decisions so one bad provider reply
        does not discard the batch. Omitting them here would mean a fact the
        caller submitted vanished from the answer."""
        failure = CandidateFailure(
            candidate_id=CandidateId("c_9"),
            error=ProviderUnavailable("ollama is not running"),
        )

        payload = result_of(_empty_result(), [failure])

        assert payload["failed"] == [{"candidate_id": "c_9", "code": "GM_PROVIDER"}]

    async def test_a_failure_carries_a_code_and_never_the_message(self) -> None:
        """`RULES.md` §1.5 keeps exception strings - which can hold a DSN or a
        span of untrusted source text - out of anything a caller sees."""
        failure = CandidateFailure(
            candidate_id=CandidateId("c_9"),
            error=RuntimeError("postgresql://user:hunter2@db/prod"),
        )

        payload = result_of(_empty_result(), [failure])

        assert payload["failed"][0]["code"] == "GM_UNKNOWN"
        assert "hunter2" not in str(payload)


class TestCommitRefusesForAReasonRatherThanAGap:
    async def test_commit_refuses_an_unsourced_assertion_before_anything_else(self) -> None:
        """§2.3: "there is no unsourced write path in this API". That rule needs
        no pipeline, so it is enforced with no pipeline - the caller is told the
        real objection rather than a missing dependency."""
        with pytest.raises(ToolRefusedError, match="no unsourced write path"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": [],
                        }
                    ]
                },
            )

    async def test_commit_refuses_provenance_that_cannot_be_quoted(self) -> None:
        with pytest.raises(ToolRefusedError, match="verbatim"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": [{"source_hash": "sha256:abc"}],
                        }
                    ]
                },
            )

    async def test_commit_requires_the_four_documented_keys(self) -> None:
        with pytest.raises(ToolRefusedError, match="missing"):
            await run_commit(context(), {"assertions": [{"subject": "s"}]})


class TestEveryPublishedConstraintIsEnforced:
    """One test per property §2.2 and §2.3 publish.

    Each of these is a promise the `inputSchema` makes to a caller, and the SDK
    keeps none of them: it validates that `arguments` is an object and leaves
    the property types to the server. So a published `enum` that nothing checks
    is a lie, and these are what make it true.
    """

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "`content` is required"),
            ({"content": "   "}, "`content` is required"),
            ({"content": 42}, "`content` is required"),
            ({"content": "x", "risk_hint": "extreme"}, "`risk_hint` must be one of"),
            ({"content": "x", "mode": "eventually"}, "`mode` must be one of"),
            ({"content": "x", "mode": "strict", "hints": []}, "`hints` must be an object"),
            (
                {"content": "x", "mode": "strict", "hints": {"subject": 7}},
                "`hints.subject` must be a string",
            ),
            (
                {"content": "x", "mode": "strict", "hints": {"predicates_of_interest": "allergy"}},
                "predicates_of_interest",
            ),
            (
                {"content": "x", "mode": "strict", "idempotency_key": 1},
                "`idempotency_key` must be a string",
            ),
        ],
    )
    async def test_propose_refuses_bad_arguments(
        self, arguments: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ToolRefusedError, match=message):
            await run_propose(context(), arguments)

    async def test_propose_accepts_every_published_source_tier(self) -> None:
        """All five reach the pipeline gate rather than being refused on the
        way - a tier the document publishes and the server rejects would be a
        schema that lies."""
        for tier in (
            "trusted_system",
            "verified_user",
            "unverified_user",
            "tool_output",
            "retrieved_web",
        ):
            payload = await run_propose(
                governing_context(), {"content": "x", "mode": "strict", "source_tier": tier}
            )

            assert payload["status"] == "decided"

    async def test_propose_accepts_every_published_risk_hint(self) -> None:
        for hint in ("low", "default", "high"):
            payload = await run_propose(
                governing_context(_K_BY_RISK_HINT[hint]),
                {"content": "x", "mode": "strict", "risk_hint": hint},
            )

            assert payload["status"] == "decided"

    async def test_well_formed_hints_are_accepted(self) -> None:
        """`hints.subject` reaches `Proposal.subject_hint`, which ADR-0008 makes
        the caller's way of naming the entity outright."""
        payload = await run_propose(
            governing_context(),
            {
                "content": "x",
                "mode": "strict",
                "hints": {"subject": "e-1", "predicates_of_interest": ["allergy"]},
                "idempotency_key": "idem-1",
            },
        )

        assert payload["status"] == "decided"

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "`assertions` is required"),
            ({"assertions": []}, "`assertions` is required"),
            ({"assertions": "not-an-array"}, "`assertions` is required"),
            ({"assertions": ["not-an-object"]}, "must be an object"),
        ],
    )
    async def test_commit_refuses_bad_assertions(
        self, arguments: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ToolRefusedError, match=message):
            await run_commit(context(), arguments)

    async def test_commit_accepts_a_single_provenance_object(self) -> None:
        """§2.3's item schema names `provenance` and does not say it is a list.
        A caller sending one object rather than a list of one is not making a
        mistake, and refusing it would be this server inventing a constraint the
        document does not state."""
        with pytest.raises(ToolRefusedError, match="no K samples"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": {"verbatim": "allergic to latex"},
                        }
                    ]
                },
            )
