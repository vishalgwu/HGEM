"""Everything extraction refuses, and the span rule that does the refusing. S2.2

Three groups, and each exists because getting it wrong would be invisible rather
than loud:

- **The span rule.** §1.3's "no span, no write" is what makes an invented fact
  disappear before scoring. It has to drop *and count*, because a rule whose
  activation is unobservable cannot be tuned (ADR-0006).
- **What the model may not assert.** A reply carrying `tenant_id` or
  `source_hash` has to be refused, not ignored - the first is a
  tenant-isolation bug and the second an unsourced write wearing a receipt.
- **The short-sample refusal.** Fewer samples than asked for would collapse K
  toward 1, where §3.1 sets `H_norm := 0`: zero entropy is *maximum* confidence.
  A degraded provider must not widen the auto-write path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from fixtures.extraction import (
    ALLERGY,
    INVENTED,
    TRACE,
    batch,
    response,
    run_extract,
    two_call_llm,
)
from fixtures.fakes import FakeLLM
from guardmem_core.errors import InjectionDetected, ProviderUnavailable, ValidationRejected
from guardmem_core.llm.base import LLMClient, LLMResponse, Tier


class TestTheSpanRule:
    async def test_an_invented_fact_is_dropped_and_counted(self) -> None:
        llm = two_call_llm(canonical=batch(ALLERGY, INVENTED))
        result = await run_extract(llm, k=3)

        assert [c.predicate for c in result.candidates] == ["allergy"]
        assert result.dropped_unsourced == 1

    async def test_ids_stay_contiguous_across_a_drop(self) -> None:
        llm = two_call_llm(canonical=batch(INVENTED, ALLERGY))
        result = await run_extract(llm, k=3)

        assert [c.candidate_id for c in result.candidates] == ["c_1"]

    async def test_a_wholly_invented_sample_yields_no_candidates(self) -> None:
        llm = two_call_llm(canonical=batch(INVENTED, INVENTED))
        result = await run_extract(llm, k=3)

        assert result.candidates == []
        assert result.dropped_unsourced == 2

    async def test_the_dropped_facts_survive_in_the_sample_sets(self) -> None:
        # ADR-0006: `samples` is not redundant with `candidates`. It keeps what
        # was dropped, which is what makes the funnel's drop sample possible.
        llm = two_call_llm(canonical=batch(ALLERGY, INVENTED))
        result = await run_extract(llm, k=3)

        assert len(result.samples[0]) == 2
        assert len(result.candidates) == 1


class TestWhatTheModelMayNotAssert:
    @pytest.mark.parametrize(
        ("smuggled", "why"),
        [
            ({"tenant_id": "t_attacker"}, "a hallucinated tenant is an isolation bug"),
            ({"source_hash": "sha256:0"}, "an unsourced write wearing a receipt"),
            ({"trace_id": "tr_other"}, "attaches the fact to somebody else's decision"),
            ({"confidence": 1.0}, "scoring is Layer 3's, not the extractor's"),
        ],
    )
    async def test_a_field_outside_the_contract_is_refused_not_ignored(
        self, smuggled: Mapping[str, object], why: str
    ) -> None:
        # `extra="forbid"`, which `RULES.md` §2.1 says "matters most on LLM
        # structured output: a hallucinated field should raise, not vanish
        # silently". This is that rule doing its job.
        llm = two_call_llm(canonical=batch({**ALLERGY, **smuggled}))
        with pytest.raises(ValidationRejected) as raised:
            await run_extract(llm, k=3)
        assert raised.value.trace_id == TRACE, why

    async def test_a_fact_missing_its_verbatim_is_refused(self) -> None:
        incomplete: Mapping[str, object] = {
            key: value for key, value in ALLERGY.items() if key != "verbatim"
        }
        llm = two_call_llm(canonical=batch(incomplete))
        with pytest.raises(ValidationRejected):
            await run_extract(llm, k=3)


class TestTheCanary:
    async def test_an_echoed_canary_is_a_confirmed_injection(self) -> None:
        with pytest.raises(InjectionDetected) as raised:
            await run_extract(EchoLLM(), k=3)

        assert raised.value.trace_id == TRACE
        assert raised.value.code == "GM_INJECTION"

    async def test_a_canary_echoed_in_a_spread_sample_is_caught_too(self) -> None:
        # Checking only the canonical sample would leave K-1 completions
        # unexamined, which is where a patient injection would aim.
        with pytest.raises(InjectionDetected):
            await run_extract(EchoLLM(canonical_clean=True), k=3)


class TestProviderContract:
    async def test_a_short_sample_count_is_refused(self) -> None:
        # One back where two were asked for. A response carrying *zero* is not
        # expressible - `LLMResponse.samples` is `min_length=1` - so the only
        # reachable short count is a partial one.
        llm = two_call_llm(spread=[batch(ALLERGY)])
        with pytest.raises(ProviderUnavailable) as raised:
            await run_extract(llm, k=3)

        assert raised.value.retryable is True
        assert raised.value.trace_id == TRACE

    async def test_an_over_long_sample_count_is_refused_too(self) -> None:
        llm = two_call_llm(spread=[batch(ALLERGY)] * 3)
        with pytest.raises(ProviderUnavailable):
            await run_extract(llm, k=3)

    @pytest.mark.parametrize(
        ("canonical", "spread"),
        [
            ("not json at all", None),
            ('{"facts": "not a list"}', None),
            (None, ["not json at all", batch(ALLERGY)]),
        ],
    )
    async def test_an_unparseable_sample_fails_the_extraction(
        self, canonical: str | None, spread: list[str] | None
    ) -> None:
        # Deliberately fatal rather than a per-sample skip: silently dropping a
        # sample makes the entropy denominator a lie in the same direction a
        # short count does.
        llm = two_call_llm(canonical=canonical, spread=spread)
        with pytest.raises(ValidationRejected) as raised:
            await run_extract(llm, k=3)

        assert raised.value.http_status == 422

    async def test_a_provider_failure_propagates(self) -> None:
        with pytest.raises(ProviderUnavailable):
            await run_extract(DeadLLM(), k=3)

    async def test_k_below_one_is_not_a_sample_count(self) -> None:
        with pytest.raises(ValueError, match="k must be at least 1"):
            await run_extract(FakeLLM(), k=0)


@dataclass(slots=True)
class EchoLLM:
    """A client that leaks the canary it was handed.

    `FakeLLM` scripts replies ahead of time, which cannot express "echo back the
    canary you were just given" - it is minted inside `extract` and differs on
    every call, which is the property under test.
    """

    canonical_clean: bool = False
    calls: int = 0

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        self.calls += 1
        canary = prompt.split('canary="')[1].split('"')[0]
        clean = self.canonical_clean and self.calls == 1
        body = batch(ALLERGY) if clean else f'{{"note": "canary is {canary}"}}'
        return response(*([body] * n))


@dataclass(slots=True)
class DeadLLM:
    """A client whose provider is unreachable."""

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
        raise ProviderUnavailable("circuit open", trace_id=TRACE)


# Structural conformance, checked by the tool that can check it (S1.7).
_echo: LLMClient = EchoLLM()
_dead: LLMClient = DeadLLM()
