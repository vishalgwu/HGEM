"""The filter as a whole: batching, the canary, and every way to fail safe. S2.1

The rules are tested next door. What is here is the orchestration, and most of
it is about what happens when the model's answer cannot be trusted - which is
the majority of this module's behaviour, because the only irreversible act it
can perform is a drop.

The partition property is the load-bearing one. `kept` and `dropped` together
must be exactly the input, once each, in order: a turn that goes missing between
the two lists is a silent loss of recall, and it is the failure this whole step
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from fixtures.fakes import FakeLLM
from fixtures.noise_corpus import GOLDEN_NAMESPACE, GOLDEN_TURNS, golden_turns
from guardmem_core.errors import InjectionDetected, ProviderUnavailable
from guardmem_core.llm.base import LLMClient, LLMResponse, Tier
from guardmem_core.pipeline.l1_extract.noise_filter import filter_noise
from guardmem_core.schemas.turn import (
    DecidedBy,
    NoiseReason,
    NoiseResult,
    Turn,
    TurnRole,
)
from guardmem_core.types import Namespace, TraceId, TurnId

_NS = Namespace("patient:8812")
_TRACE = TraceId("tr_9f2a3c")


def _turn(turn_id: str, text: str, *, role: TurnRole = TurnRole.USER) -> Turn:
    return Turn(turn_id=TurnId(turn_id), role=role, text=text)


def _reply(body: str) -> LLMResponse:
    """An `LLMResponse` whose single sample is `body`."""
    return LLMResponse(
        samples=[body],
        model="claude-haiku-4-5",
        temperature=0.0,
        seed=None,
        tokens_in=120,
        tokens_out=40,
        cache_hit=False,
        latency_ms=42.0,
        cost_usd=0.0001,
    )


def _verdicts(*entries: tuple[str, bool, str | None]) -> LLMResponse:
    """Build a well-formed classifier reply from `(turn_id, drop, reason)`."""
    body = ", ".join(
        '{{"turn_id": "{}", "drop": {}, "reason": {}}}'.format(
            turn_id,
            "true" if drop else "false",
            "null" if reason is None else f'"{reason}"',
        )
        for turn_id, drop, reason in entries
    )
    return _reply(f'{{"verdicts": [{body}]}}')


@dataclass(slots=True)
class BrokenLLM:
    """An `LLMClient` whose provider is down. See `test_a_provider_failure_propagates`."""

    calls: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        self.calls.append(prompt)
        raise ProviderUnavailable("circuit open", trace_id=_TRACE)


@dataclass(slots=True)
class ScriptedLLM:
    """An `LLMClient` that answers as a function of the prompt it was handed.

    `FakeLLM` scripts replies ahead of time, which cannot express "echo back the
    canary you were just given" - the canary is minted inside `filter_noise` and
    differs on every call, which is the property under test.
    """

    responder: Callable[[str], LLMResponse] | None = None
    prompts: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        self.prompts.append(prompt)
        if self.responder is None:
            return _verdicts(("t4", False, None))
        return self.responder(prompt)


def _canary_in(prompt: str) -> str:
    """Recover the canary `filter_noise` minted for this call."""
    return prompt.split('canary="')[1].split('"')[0]


# Structural conformance, checked by the tool that can check it (S1.7).
_broken: LLMClient = BrokenLLM()
_scripted: LLMClient = ScriptedLLM()

# A short conversation with one turn of each rule-decidable class, one plainly
# substantive turn the rules keep without a second opinion, and one short turn
# that buys a classifier call.
_CONVERSATION = [
    _turn("t1", "I take metformin, 500 milligrams, twice a day, with breakfast."),
    _turn("t2", "One sec, let me check.", role=TurnRole.ASSISTANT),
    _turn("t3", "Summarize that again."),
    _turn("t4", "My blood type is O negative."),
]


def _assert_partitions(result: NoiseResult, source: Sequence[Turn]) -> None:
    """kept + dropped is exactly the input, once each."""
    seen = [*result.kept, *(item.turn for item in result.dropped)]
    assert sorted(turn.turn_id for turn in seen) == sorted(turn.turn_id for turn in source)


class TestRuleTierOnly:
    async def test_no_ambiguous_turns_means_no_model_call(self) -> None:
        llm = FakeLLM()
        turns = [
            _turn("t1", "I take metformin, 500 milligrams, twice a day, with breakfast."),
            _turn("t2", "Thanks!"),
        ]
        result = await filter_noise(turns, llm, namespace=_NS, trace_id=_TRACE)

        assert llm.calls == []
        assert [t.turn_id for t in result.kept] == ["t1"]
        assert result.dropped[0].decided_by is DecidedBy.RULE
        _assert_partitions(result, turns)

    async def test_every_dropped_turn_carries_a_reason(self) -> None:
        # S2.1's DONE WHEN, restated as a type-level guarantee: `DroppedTurn`
        # has no valid state without one.
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert all(item.reason in set(NoiseReason) for item in result.dropped)

    async def test_kept_turns_stay_in_conversation_order(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert [t.turn_id for t in result.kept] == ["t1", "t4"]


class TestTheBatchedCall:
    async def test_the_ambiguous_remainder_costs_exactly_one_call(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        turns = [*_CONVERSATION, _turn("t5", "No."), _turn("t6", "Yes.")]
        await filter_noise(turns, llm, namespace=_NS, trace_id=_TRACE)

        assert len(llm.calls) == 1, "three ambiguous turns must not be three calls"

    async def test_the_call_is_routed_by_the_prompt_file_s_declared_tier(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)

        call = llm.calls[0]
        assert call.tier is Tier.FAST
        assert call.n == 1
        assert call.temperature == 0.0
        assert call.schema is not None

    async def test_the_prompt_carries_the_namespace_and_only_the_ambiguous_turns(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)

        prompt = llm.calls[0].prompt
        assert _NS in prompt
        assert "My blood type is O negative." in prompt
        # The rules already settled these; paying to ask again would defeat the
        # point of having a rule tier.
        assert "Summarize that again." not in prompt
        assert "with breakfast" not in prompt
        assert "{{" not in prompt, "an unsubstituted placeholder reached the model"

    async def test_a_classifier_drop_is_attributed_to_the_classifier(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", True, "third_party"))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)

        dropped = {item.turn.turn_id: item for item in result.dropped}
        assert dropped[TurnId("t4")].decided_by is DecidedBy.CLASSIFIER
        assert dropped[TurnId("t4")].reason is NoiseReason.THIRD_PARTY
        assert [t.turn_id for t in result.kept] == ["t1"]
        _assert_partitions(result, _CONVERSATION)


class TestTheCanary:
    async def test_an_echoed_canary_is_a_confirmed_injection(self) -> None:
        # The prompt states the canary must never be emitted, so emitting it
        # means the untrusted content persuaded the model otherwise -
        # `RULES.md` §3 treats that as confirmed rather than suspected.
        llm = ScriptedLLM(lambda prompt: _reply(f"sure, the canary is {_canary_in(prompt)}"))

        with pytest.raises(InjectionDetected) as raised:
            await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)

        assert raised.value.trace_id == _TRACE
        assert raised.value.code == "GM_INJECTION"
        assert raised.value.http_status == 422

    async def test_a_fresh_canary_is_minted_per_call(self) -> None:
        llm = ScriptedLLM()
        await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)

        assert len({_canary_in(prompt) for prompt in llm.prompts}) == 2, (
            "a reused canary is one an attacker can learn"
        )

    async def test_a_reply_merely_mentioning_the_word_does_not_trip_it(self) -> None:
        # A guardrail that fires on the word "canary" becomes noise, and a noisy
        # guardrail gets switched off.
        llm = ScriptedLLM(lambda _: _verdicts(("t4", False, None)))
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert "t4" in {turn.turn_id for turn in result.kept}


class TestUntrustworthyAnswers:
    """Every one of these resolves toward keeping. See the module docstring."""

    @pytest.mark.parametrize(
        ("reply", "why"),
        [
            (_reply("not json at all"), "unparseable"),
            (_reply('{"verdicts": [{"turn_id": "t4", "drop": true}], "extra": 1}'), "extra key"),
            (_reply('{"verdicts": [{"turn_id": "t4", "drop": "yes"}]}'), "wrong type"),
            (
                _reply('{"verdicts": [{"turn_id": "t4", "drop": true, "reason": "vibes"}]}'),
                "bad class",
            ),
        ],
    )
    async def test_a_reply_that_does_not_validate_drops_nothing(
        self, reply: LLMResponse, why: str
    ) -> None:
        llm = FakeLLM(responses=[reply])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert [t.turn_id for t in result.kept] == ["t1", "t4"], why
        _assert_partitions(result, _CONVERSATION)

    async def test_a_drop_without_a_reason_is_refused(self) -> None:
        # S2.1 requires a reason on every dropped turn. A drop we cannot file is
        # a drop we do not make.
        llm = FakeLLM(responses=[_verdicts(("t4", True, None))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert "t4" in {t.turn_id for t in result.kept}

    async def test_a_verdict_for_a_turn_we_never_sent_is_ignored(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t1", True, "ephemeral"), ("t4", False, None))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        # t1 was settled by the rules and never submitted; the model does not
        # get to overrule a tier it was not shown.
        assert "t1" in {t.turn_id for t in result.kept}

    async def test_a_turn_answered_twice_is_kept(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", True, "ephemeral"), ("t4", True, "third_party"))])
        result = await filter_noise(_CONVERSATION, llm, namespace=_NS, trace_id=_TRACE)
        assert "t4" in {t.turn_id for t in result.kept}

    async def test_a_turn_the_model_never_answers_for_is_kept(self) -> None:
        llm = FakeLLM(responses=[_verdicts(("t4", False, None))])
        turns = [*_CONVERSATION, _turn("t5", "No.")]
        result = await filter_noise(turns, llm, namespace=_NS, trace_id=_TRACE)
        assert "t5" in {t.turn_id for t in result.kept}
        _assert_partitions(result, turns)

    async def test_a_provider_failure_propagates(self) -> None:
        # Deliberately NOT swallowed. The extractor two steps later needs the
        # same provider, so catching it here would defer an identical failure
        # while hiding which stage first saw it - and `ARCHITECTURE.md` §4
        # already says what a dead provider does: the proposal parks.
        with pytest.raises(ProviderUnavailable):
            await filter_noise(_CONVERSATION, BrokenLLM(), namespace=_NS, trace_id=_TRACE)


class TestGoldenCorpusEndToEnd:
    async def test_the_corpus_survives_the_whole_filter_intact(self) -> None:
        llm = FakeLLM(responses=[_reply('{"verdicts": []}')])
        source = golden_turns()
        result = await filter_noise(source, llm, namespace=GOLDEN_NAMESPACE, trace_id=_TRACE)

        _assert_partitions(result, source)
        assert all(item.decided_by is DecidedBy.RULE for item in result.dropped)
        assert all(item.reason is not None for item in result.dropped)

    async def test_no_turn_the_corpus_labels_a_keep_is_dropped_by_a_rule(self) -> None:
        llm = FakeLLM(responses=[_reply('{"verdicts": []}')])
        result = await filter_noise(
            golden_turns(), llm, namespace=GOLDEN_NAMESPACE, trace_id=_TRACE
        )

        keeps = {item.turn.turn_id for item in GOLDEN_TURNS if item.expected is None}
        assert keeps & {item.turn.turn_id for item in result.dropped} == set()
