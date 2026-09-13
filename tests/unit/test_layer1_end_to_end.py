"""Day 2's closing check: raw turns in, sourced candidates out.  S2.3

The notebook ends Day 2 with one line: *"raw text in -> candidates out, each
with a span, K samples retained."* Everything else in the suite tests a stage;
this tests that the two stages of Layer 1 actually compose.

There is no orchestrator yet - `pipeline/orchestrator.py` is S5.6 - so the join
between them is performed here, and performing it is what makes the seam
visible. Three things have to line up, and none of them is checked by either
stage on its own:

1. **The document the spans index into is the *denoised* one.** `filter_noise`
   removes turns; whatever is left is joined into the string `extract` hashes
   and offsets into. Join it one way here and another way in S5.6 and every
   stored span silently points at the wrong characters.
2. **`dropped_noise` has to be handed across.** `MEMORY_ENGINE.md` §0 puts the
   count on `ExtractionResult`, and the filter that produced it is a separate
   call. A caller who forgets reports zero, and nothing complains.
3. **The candidates must still be sourced**, which is where invariant I1 meets
   the fact that the source is no longer the original transcript.
"""

from __future__ import annotations

from fixtures.extraction import CONTEXT, ONTOLOGY, batch, response
from fixtures.fakes import FakeLLM
from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.l1_extract import extract, filter_noise
from guardmem_core.schemas import ExtractionResult, Turn, TurnRole
from guardmem_core.types import TurnId

_TURNS = [
    Turn(turn_id=TurnId("t1"), role=TurnRole.ASSISTANT, text="Good morning."),
    Turn(
        turn_id=TurnId("t2"),
        role=TurnRole.USER,
        text="I'm allergic to penicillin - it gives me hives.",
    ),
    Turn(turn_id=TurnId("t3"), role=TurnRole.USER, text="One sec, let me check."),
    Turn(turn_id=TurnId("t4"), role=TurnRole.USER, text="Summarize that again."),
    Turn(
        turn_id=TurnId("t5"),
        role=TurnRole.USER,
        text="I use the CVS on Elm Street now.",
    ),
    Turn(
        turn_id=TurnId("t6"),
        role=TurnRole.USER,
        text="If I moved to Austin, would my coverage change?",
    ),
]

_ALLERGY = {
    "subject": "patient:8812",
    "predicate": "allergy",
    "object": "penicillin",
    "verbatim": "allergic to penicillin",
}
_PHARMACY = {
    "subject": "patient:8812",
    "predicate": "preferred_pharmacy",
    "object": "CVS Elm Street",
    "verbatim": "the CVS on Elm Street",
}
# The model claims a fact that survives nowhere in the denoised document.
_INVENTED = {
    "subject": "patient:8812",
    "predicate": "insurance_plan",
    "object": "Blue Cross",
    "verbatim": "my insurance is Blue Cross",
}


def _join(turns: list[Turn]) -> str:
    """The denoised document. S5.6 owns this join; it is spelled out here."""
    return "\n".join(turn.text for turn in turns)


async def _run_layer_1() -> tuple[str, ExtractionResult]:
    """Noise filter, then extraction, the way the orchestrator will."""
    noise = await filter_noise(
        _TURNS,
        FakeLLM(responses=[response('{"verdicts": []}')]),
        namespace=CONTEXT.namespace,
        trace_id=CONTEXT.trace_id,
    )
    content = _join(noise.kept)
    canonical = batch(_ALLERGY, _PHARMACY, _INVENTED)
    extraction = await extract(
        content,
        FakeLLM(responses=[response(canonical), response(canonical, canonical)]),
        context=CONTEXT,
        ontology_yaml=ONTOLOGY,
        k=3,
        tier=Tier.FAST,
        dropped_noise=len(noise.dropped),
    )
    return content, extraction


class TestEndOfDayTwo:
    async def test_raw_turns_become_sourced_candidates(self) -> None:
        content, result = await _run_layer_1()

        assert [c.predicate for c in result.candidates] == ["allergy", "preferred_pharmacy"]
        for candidate in result.candidates:
            start, end = candidate.provenance.source_span
            assert content[start:end] == candidate.provenance.verbatim

    async def test_the_noise_filter_ate_what_it_should_have(self) -> None:
        _, result = await _run_layer_1()

        # Greeting, filler, imperative, hypothetical - four of the six turns.
        assert result.dropped_noise == 4

    async def test_k_samples_are_retained(self) -> None:
        _, result = await _run_layer_1()

        assert result.k_samples == 3 == len(result.samples)
        assert all(len(sample) == 3 for sample in result.samples)

    async def test_the_invented_fact_does_not_survive_the_layer(self) -> None:
        # It is in every sample and in none of the candidates. That is the whole
        # of Layer 1 in one assertion.
        _, result = await _run_layer_1()

        assert result.dropped_unsourced == 1
        assert "insurance_plan" in {f.predicate for f in result.samples[0]}
        assert "insurance_plan" not in {c.predicate for c in result.candidates}

    async def test_the_spans_index_into_the_denoised_document_not_the_transcript(self) -> None:
        # The seam most likely to be got wrong later. The dropped turns are gone
        # from the document the offsets are relative to, so a span resolved
        # against the original transcript would land several characters off -
        # and land on real text, which is why it would not look like a bug.
        content, result = await _run_layer_1()
        transcript = _join(_TURNS)

        assert content != transcript
        candidate = result.candidates[0]
        start, end = candidate.provenance.source_span
        assert content[start:end] == "allergic to penicillin"
        assert transcript[start:end] != "allergic to penicillin"
