"""Real providers, real tokens.  BUILD_NOTEBOOK.md S9.1

    uv run pytest -m live

**Deselected by default and never skipped.** `RULES.md` §5 requires the
integration suite to be "green with no skips on main", and it also says LLM
calls are "live only in the nightly eval job" - the `live` marker in
`pyproject.toml` satisfies both at once, because a deselected test is not
collected at all. S27.1's nightly job is what should select it.

**This file is not decoration, and the reason is a bug it found.** While S9.1
was being built, the Ollama adapter sent one fixed seed on every request in a
K-sample draw. At temperature 0.7 with K=3 the three "independent" samples came
back **character-identical**, because Ollama honours a seed exactly. That sets
`MEMORY_ENGINE.md` §3.1's semantic entropy to zero, which sets §3.2's
`w_H(1 - H_norm)` term to maximum confidence, on every candidate, forever -
CHECKPOINT B's own failure list calls that "a scorer that produces plausible
numbers with no discriminative power ... invisible to unit tests", and it was
invisible to unit tests. A mock transport returns whatever it was told to; only
a real model could have shown it.

`test_the_samples_in_a_draw_actually_differ` is that bug, pinned.

**What a green run here does not establish.** Passing against a 7-billion
parameter local model says the adapter is correct, not that the *scoring* is:
CHECKPOINT B measures whether `C` separates good candidates from bad, and a
local model and Claude Opus are not the same instrument. See
`providers/ollama_client.py`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

import httpx
import pytest

from fixtures.providers import PROMPT, Capital
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import AnthropicClient, OllamaClient
from guardmem_core.pipeline.l1_extract.extractor import ExtractionBatch

if TYPE_CHECKING:
    from guardmem_core.llm.base import LLMResponse

pytestmark = pytest.mark.live

# Generous. A cold 7B model on CPU took nineteen seconds to answer a one-line
# prompt on the machine this was written on, which is roughly the whole of
# `settings.llm_timeout_s`'s default - the adapter's docstring says so, and this
# is the number that proves it was not an exaggeration.
_LOCAL_TIMEOUT_S: Final = 300.0

_OLLAMA_URL: Final = os.environ.get("GM_OLLAMA_URL", "http://localhost:11434")

# A pinned tag, not `:latest` - the adapter refuses that outright. Overridable
# because the model a developer has pulled is a property of their machine.
_OLLAMA_MODEL: Final = os.environ.get("GM_OLLAMA_MODEL", "llama3.1:8b")


async def _ollama_draw(*, n: int, temperature: float) -> LLMResponse:
    """One real draw against a local Ollama."""
    async with httpx.AsyncClient(base_url=_OLLAMA_URL) as http:
        client = OllamaClient(
            http,
            models=dict.fromkeys(Tier, _OLLAMA_MODEL),
            timeout_s=_LOCAL_TIMEOUT_S,
        )
        return await client.complete(
            prompt=PROMPT, schema=Capital, tier=Tier.FAST, temperature=temperature, n=n
        )


class TestOllama:
    """The provider that needs no credential, so it is the one that runs."""

    async def test_a_real_model_answers_in_the_shape_it_was_given(self) -> None:
        """Structured output, end to end, against a model nobody scripted.

        This is the assertion a mock cannot make: the JSON came back in
        `Capital`'s shape because the *model* was constrained to it, not because
        a fixture said so.
        """
        response = await _ollama_draw(n=1, temperature=0.0)

        parsed = Capital.model_validate_json(response.samples[0])
        assert parsed.capital.strip().lower() == "paris"
        assert response.tokens_in > 0
        assert response.tokens_out > 0
        assert response.cost_usd == 0.0, "local inference is genuinely free"

    async def test_the_samples_in_a_draw_actually_differ(self) -> None:
        """**The regression test for the bug this file exists to have caught.**

        A K-sample draw must produce K *independent* samples. With one fixed
        seed it produced K identical ones, silently, and `H_norm` would have been
        zero on every candidate the system ever scored.

        Asserted as "not all identical" rather than "all distinct": a real model
        at temperature 0.7 may legitimately repeat itself on a short answer, and
        a test demanding three different strings would flake. All three being
        byte-identical is the signature of the bug, and that is what is checked.
        """
        response = await _ollama_draw(n=3, temperature=1.0)

        assert len(response.samples) == 3
        assert len(set(response.samples)) > 1, (
            "every sample in the draw is identical - the per-sample seed offset "
            "has regressed, and MEMORY_ENGINE.md §3.1's entropy is now zero for "
            "every candidate. See OllamaClient._one."
        )

    async def test_the_real_extraction_schema_compiles_to_a_grammar(self) -> None:
        """**The second bug in this file that only a real model could show.**

        Ollama turns `format` into a GBNF grammar, and a repetition of 2000 or
        more is refused with `400 ... failed to parse grammar` - the whole
        schema, not the offending field. `ExtractedFact.verbatim` declares
        `max_length=2000`, mirroring `Provenance.verbatim`, so **extraction
        never ran on Ollama at all** and the README and the notebook both
        recorded CHECKPOINT B as runnable locally when it was not.

        `_grammar_safe` strips the keyword the compiler cannot take; the caller
        still validates the reply against the full schema, so the cap moves from
        prevention to detection rather than being lost.

        Asserted against the *real* `ExtractionBatch` rather than a small model
        that happens to have a long field. The bug was in the interaction
        between a real schema and a real compiler, and a hand-built stand-in
        would drift from the thing that actually goes over the wire.
        """
        async with httpx.AsyncClient(base_url=_OLLAMA_URL) as http:
            client = OllamaClient(
                http,
                models=dict.fromkeys(Tier, _OLLAMA_MODEL),
                timeout_s=_LOCAL_TIMEOUT_S,
            )
            response = await client.complete(
                prompt="Return an empty list of facts.",
                schema=ExtractionBatch,
                tier=Tier.FAST,
            )

        ExtractionBatch.model_validate_json(response.samples[0])

    async def test_a_wrong_model_id_fails_as_a_provider_error(self) -> None:
        """The error path, against a real server. Ollama answers 404 for a model
        it has not pulled, and the adapter must turn that into the domain error
        rather than an `httpx.HTTPStatusError` the pipeline has no case for."""
        from guardmem_core.errors import ProviderUnavailable

        async with httpx.AsyncClient(base_url=_OLLAMA_URL) as http:
            client = OllamaClient(
                http,
                models=dict.fromkeys(Tier, "no-such-model:1b"),
                timeout_s=_LOCAL_TIMEOUT_S,
            )
            with pytest.raises(ProviderUnavailable, match="ollama"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)


class TestAnthropic:
    """Runs only where a key exists, and is deselected with the rest by default.

    `GM_ANTHROPIC_API_KEY` is blank in this repository and has been since S0.2.
    The `skipif` here is inside an already-deselected module, so it adds no skip
    to any default run - it is what stops `-m live` from failing on a machine
    that has Ollama and no Anthropic key, which is every machine this has been
    developed on.
    """

    @pytest.mark.skipif(
        not os.environ.get("GM_ANTHROPIC_API_KEY"),
        reason="GM_ANTHROPIC_API_KEY is not set; this test spends real money",
    )
    async def test_a_real_claude_call_records_no_temperature(self) -> None:
        """The S9.1 finding, confirmed against the live API rather than the SDK's
        signature: there is no temperature to record, so `None` is what the audit
        gets. Also the first real Claude call this repository will have made -
        which is CHECKPOINT B's blocker, and why S9.1 came before S6.3."""
        import anthropic

        from guardmem_core.settings import get_settings

        settings = get_settings()
        async with anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        ) as sdk:
            client = AnthropicClient(
                sdk,
                models={
                    Tier.FAST: settings.model_fast,
                    Tier.BALANCED: settings.model_balanced,
                    Tier.FRONTIER: settings.model_frontier,
                },
                timeout_s=settings.llm_timeout_s,
            )
            response = await client.complete(
                prompt=PROMPT, schema=Capital, tier=Tier.FAST, temperature=0.7, n=1
            )

        assert Capital.model_validate_json(response.samples[0]).capital.strip() == "Paris"
        assert response.temperature is None
        assert response.seed is None
        assert response.cost_usd > 0.0, "a metered provider reports a real cost"
