"""K-sample extraction: the ladder, the prompt, and what comes out.  S2.2

S2.2's DONE WHEN: "with `FakeLLM` returning fixed samples, `extract()` returns K
sample sets and the canonical candidate list; a canary in the output raises
`InjectionDetected`." The canary and the other refusals are next door in
`test_extractor_refusals.py`; this file is the path where nothing goes wrong.

The sampling-ladder assertions are the ones worth defending. §1.2 draws sample 0
at temperature 0 and the rest at 0.7, and a single call at 0.7 would satisfy
every *other* assertion in this file while leaving no canonical text at all -
which makes §3.1's minority-cluster drop, "a candidate that appears in zero
clusters containing sample 0's meaning", arbitrary. So the calls themselves are
asserted, not only their results.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from fixtures.extraction import (
    ALLERGY,
    CONTENT,
    CONTEXT,
    ONTOLOGY,
    PHARMACY,
    batch,
    response,
    run_extract,
    two_call_llm,
)
from fixtures.fakes import FakeLLM
from guardmem_core.llm.base import Tier
from guardmem_core.schemas import ExtractionResult, SourceTier


class TestTheSamplingLadder:
    async def test_k_above_one_draws_the_canonical_sample_at_temperature_zero(self) -> None:
        llm = two_call_llm()
        await run_extract(llm, k=3)

        assert [(call.temperature, call.n) for call in llm.calls] == [(0.0, 1), (0.7, 2)]

    async def test_k_of_one_is_a_single_call_at_temperature_zero(self) -> None:
        llm = FakeLLM(responses=[response(batch(ALLERGY))])
        result = await run_extract(llm, k=1)

        assert [(call.temperature, call.n) for call in llm.calls] == [(0.0, 1)]
        assert result.k_samples == 1

    async def test_k_of_five_asks_for_four_spread_samples(self) -> None:
        llm = two_call_llm(spread=[batch(ALLERGY)] * 4)
        result = await run_extract(llm, k=5)

        assert llm.calls[1].n == 4
        assert result.k_samples == 5 == len(result.samples)

    async def test_both_calls_send_the_identical_prompt(self) -> None:
        # §1.2 draws the samples "with the same prompt". A prompt that varied
        # between them would make the spread a measure of the prompt rather
        # than of the model's uncertainty.
        llm = two_call_llm()
        await run_extract(llm, k=3)

        assert llm.calls[0].prompt == llm.calls[1].prompt

    async def test_the_tier_is_the_caller_s_and_is_passed_through(self) -> None:
        # Unlike the noise filter, the tier is NOT read from the prompt file:
        # §1.2 makes it a function of K, so it is a routing decision.
        llm = two_call_llm(spread=[batch(ALLERGY)] * 4)
        await run_extract(llm, k=5, tier=Tier.BALANCED)

        assert {call.tier for call in llm.calls} == {Tier.BALANCED}


class TestThePrompt:
    async def test_it_carries_the_ontology_the_content_and_a_canary(self) -> None:
        llm = two_call_llm()
        await run_extract(llm, k=3)

        prompt = llm.calls[0].prompt
        assert ONTOLOGY in prompt
        assert CONTENT in prompt
        assert 'canary="' in prompt
        assert "{{" not in prompt, "an unsubstituted placeholder reached the model"

    async def test_it_constrains_generation_to_the_declared_schema(self) -> None:
        llm = two_call_llm()
        await run_extract(llm, k=3)

        assert llm.calls[0].schema is not None
        assert llm.calls[0].schema.__name__ == "ExtractionBatch"

    async def test_a_fresh_canary_is_minted_per_extraction(self) -> None:
        first, second = two_call_llm(), two_call_llm()
        await run_extract(first, k=3)
        await run_extract(second, k=3)

        canaries = {
            llm.calls[0].prompt.split('canary="')[1].split('"')[0] for llm in (first, second)
        }
        assert len(canaries) == 2, "a reused canary is one an attacker can learn"


class TestTheCanonicalCandidates:
    async def test_candidates_come_from_sample_zero_with_contiguous_ids(self) -> None:
        # `MCP_INTEGRATION.md` §2.2 publishes `c_1`, `c_2`, and
        # `schemas/review.py` identifies a decision by (trace_id, candidate_id).
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        assert [c.candidate_id for c in result.candidates] == ["c_1", "c_2"]
        assert [c.predicate for c in result.candidates] == ["allergy", "preferred_pharmacy"]

    async def test_every_candidate_carries_a_span_that_slices_back_to_its_quote(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        for candidate in result.candidates:
            start, end = candidate.provenance.source_span
            assert CONTENT[start:end] == candidate.provenance.verbatim

    async def test_the_source_hash_is_of_the_content_the_spans_index_into(self) -> None:
        # If these two ever describe different strings, every span in the audit
        # record points into a document nobody kept.
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        expected = f"sha256:{hashlib.sha256(CONTENT.encode('utf-8')).hexdigest()}"
        assert {c.provenance.source_hash for c in result.candidates} == {expected}

    async def test_the_caller_s_context_is_carried_and_never_inferred(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        candidate = result.candidates[0]
        assert candidate.tenant_id == CONTEXT.tenant_id
        assert candidate.namespace == CONTEXT.namespace
        assert candidate.trace_id == CONTEXT.trace_id
        assert candidate.provenance.source_tier is SourceTier.VERIFIED_USER
        assert candidate.provenance.captured_at == CONTEXT.captured_at

    async def test_extracted_by_is_the_model_that_actually_served_the_call(self) -> None:
        # Not the tier's configured pin: a fallback can serve a FAST call from
        # another provider entirely, and replay has to know which one did.
        llm = FakeLLM(
            responses=[
                response(batch(ALLERGY), model="gpt-4o-mini"),
                response(batch(ALLERGY), batch(ALLERGY)),
            ]
        )
        result = await run_extract(llm, k=3)

        assert result.candidates[0].extracted_by == "gpt-4o-mini"

    async def test_the_prompt_version_ties_a_candidate_to_the_file_that_made_it(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        assert result.candidates[0].prompt_version == "extract_memories@v1"

    async def test_an_empty_extraction_is_a_valid_answer(self) -> None:
        # The prompt says so explicitly. A model that believes it must return
        # something will invent it, which is the failure §1.3 then has to catch.
        llm = two_call_llm(canonical=batch(), spread=[batch(), batch()])
        result = await run_extract(llm, k=3)

        assert result.candidates == []
        assert result.dropped_unsourced == 0


class TestTheSampleSets:
    async def test_every_sample_is_carried_in_draw_order_canonical_first(self) -> None:
        llm = two_call_llm(
            canonical=batch(ALLERGY),
            spread=[batch(PHARMACY), batch(ALLERGY, PHARMACY)],
        )
        result = await run_extract(llm, k=3)

        assert [[f.predicate for f in sample] for sample in result.samples] == [
            ["allergy"],
            ["preferred_pharmacy"],
            ["allergy", "preferred_pharmacy"],
        ]

    async def test_k_samples_agrees_with_what_is_carried(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        assert result.k_samples == len(result.samples) == 3

    def test_a_result_may_not_claim_a_k_it_did_not_draw(self) -> None:
        """ADR-0006's validator. The failure it prevents is a silent one.

        `k_samples` is the denominator in §3.1's ``H_norm = H / log K``. If it
        disagrees with what was drawn, entropy is normalised against a sample
        set that never existed - and the result is a plausible number in the
        right range, so nothing downstream looks wrong.
        """
        with pytest.raises(ValidationError, match="sample sets were carried"):
            ExtractionResult(
                candidates=[],
                samples=[[], []],
                k_samples=5,
                dropped_noise=0,
                dropped_unsourced=0,
                tokens_in=0,
                tokens_out=0,
                cache_hit=False,
            )


class TestBilling:
    async def test_tokens_are_summed_across_both_calls(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3)

        assert (result.tokens_in, result.tokens_out) == (200, 60)

    async def test_a_partial_cache_hit_is_not_a_hit(self) -> None:
        # Reporting one would overstate the >=40% assumption in `PRD.md` §6.5.
        llm = FakeLLM(
            responses=[
                response(batch(ALLERGY), cache_hit=True),
                response(batch(ALLERGY), batch(ALLERGY), cache_hit=False),
            ]
        )
        result = await run_extract(llm, k=3)

        assert result.cache_hit is False

    async def test_the_noise_count_travels_through_to_the_result(self) -> None:
        llm = two_call_llm()
        result = await run_extract(llm, k=3, dropped_noise=17)

        assert result.dropped_noise == 17
