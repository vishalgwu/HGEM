"""S9.1's DONE WHEN: one prompt, all three providers.  BUILD_NOTEBOOK.md S9.1

    "the same prompt runs against all three in an integration test"

That sentence is the specification for this module, and the interesting word is
**same**. Anthropic, OpenAI and Ollama disagree about nearly every detail these
adapters touch:

| | Anthropic | OpenAI | Ollama |
|---|---|---|---|
| SDK | official | official | raw `httpx` |
| `n` | absent; K requests | native | absent; K requests |
| `temperature` | **absent from the API** | sent | sent |
| `seed` | absent | sent | sent, varying per sample |
| structured output | `output_config.format` | `response_format` | `format` |
| usage fields | `input_tokens` | `prompt_tokens` | `prompt_eval_count` |
| cost | metered | metered | genuinely zero |

Every row is a difference a caller must never see, and `test_one_prompt_...` is
what holds that. `guardmem_core.pipeline` is written against `LLMClient` and
nothing else; if the three disagreed about the *result*, every layer above would
have to know which provider ran.

**Mocked at the transport, and that is the whole reason it can run in CI.**
Two of the three providers bill per token and need a secret. `RULES.md` §5 is
explicit - LLM calls are "mocked with recorded fixtures in unit, live only in
the nightly eval job" - and mocking the socket rather than the adapter means the
vendors' own SDKs still parse, validate and type every response. See
`tests/fixtures/providers.py`.

**The live counterpart is `tests/live/`**, deselected by default via the `live`
marker, and it is not decoration: the Ollama adapter was driven against a real
server while this step was built, and that is where the fixed-seed bug was
found - three "independent" samples came back character-identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from fixtures.providers import (
    MODELS,
    PROMPT,
    Capital,
    anthropic_client,
    anthropic_transport,
    ollama_client,
    ollama_transport,
    openai_client,
    openai_transport,
)
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import AnthropicClient, OllamaClient, OpenAIClient

if TYPE_CHECKING:
    from guardmem_core.llm.base import LLMClient, LLMResponse

# Every adapter takes one, per `RULES.md` §2.2. Generous, because a mock
# transport answers instantly and the number is only here to prove the parameter
# is threaded through rather than to bound anything.
_TIMEOUT_S: Final = 30.0


async def _draw(name: str, *, n: int = 1, temperature: float = 0.0) -> LLMResponse:
    """Run the one prompt through one provider and return its response.

    Args:
        name: `"anthropic"`, `"openai"` or `"ollama"`.
        n: How many samples.
        temperature: What to ask for - honoured by two of the three.

    Returns:
        The `LLMResponse`, which is the object this module exists to compare.

    Each client is built and closed per call rather than shared across the
    module. Shared would be faster and would also let one test's transport
    answer another's request, which for a module whose whole subject is "these
    three behave identically" is the one confusion worth paying to avoid.
    """
    if name == "anthropic":
        async with anthropic_client(anthropic_transport()) as sdk:
            client: LLMClient = AnthropicClient(
                sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S
            )
            return await client.complete(
                prompt=PROMPT, schema=Capital, tier=Tier.FAST, temperature=temperature, n=n
            )
    if name == "openai":
        async with openai_client(openai_transport(choices=n)) as sdk_openai:
            client = OpenAIClient(sdk_openai, models=MODELS["openai"], timeout_s=_TIMEOUT_S)
            return await client.complete(
                prompt=PROMPT, schema=Capital, tier=Tier.FAST, temperature=temperature, n=n
            )
    async with ollama_client(ollama_transport()) as http:
        client = OllamaClient(http, models=MODELS["ollama"], timeout_s=_TIMEOUT_S)
        return await client.complete(
            prompt=PROMPT, schema=Capital, tier=Tier.FAST, temperature=temperature, n=n
        )


PROVIDERS: Final = ("anthropic", "openai", "ollama")


class TestTheDoneWhen:
    """ "The same prompt runs against all three"."""

    @pytest.mark.parametrize("provider", PROVIDERS)
    async def test_one_prompt_produces_one_parseable_sample(self, provider: str) -> None:
        """The literal sentence, and the only assertion that matters to a caller.

        `model_validate_json` is the test, not `json.loads`: `Capital` is what
        was handed to the provider as a schema, so parsing back into it is what
        proves the constraint reached the wire in a form that provider honoured -
        three different parameters, one result.
        """
        response = await _draw(provider)

        assert len(response.samples) == 1
        assert Capital.model_validate_json(response.samples[0]) == Capital(
            capital="Paris", country="France"
        )

    @pytest.mark.parametrize("provider", PROVIDERS)
    async def test_every_provider_reports_the_accounting_rules_3_requires(
        self, provider: str
    ) -> None:
        """`RULES.md` §3: model, token counts, cache hit, latency, cost.

        Asserted across all three because this is the half of the protocol that
        is easiest to leave half-filled - an adapter that returned samples and
        zeros would pass every test about samples.
        """
        response = await _draw(provider)

        assert response.model, "which model served the call, read off the reply"
        assert response.tokens_in > 0
        assert response.tokens_out > 0
        assert response.latency_ms >= 0.0
        assert response.cache_hit is False
        assert response.cost_usd >= 0.0

    @pytest.mark.parametrize("provider", PROVIDERS)
    async def test_k_samples_come_back_as_k_samples(self, provider: str) -> None:
        """`MEMORY_ENGINE.md` §1.2's spread, whichever way the provider gets there.

        Two of the three have no `n` and satisfy it with K concurrent requests;
        OpenAI has one and returns K choices. §3.1 normalises entropy by `log K`,
        so a provider that quietly returned fewer would not degrade the score -
        it would compute a different one.
        """
        response = await _draw(provider, n=3, temperature=0.7)

        assert len(response.samples) == 3


class TestWhereTheProvidersHonestlyDiffer:
    """The differences a caller does not see, asserted so they stay deliberate.

    Every one of these is a field that is `None` on one provider and filled on
    another. The temptation with such a field is to default it - to write `0.0`
    where no temperature was sent - and each of these tests exists to make that
    a failure rather than a tidy-up.
    """

    async def test_anthropic_reports_no_temperature_because_it_has_none(self) -> None:
        """The S9.1 finding. `anthropic` 1.4.0's `messages.create` takes no
        `temperature`, `top_p` or `top_k` at all - sampling controls are gone on
        the current Claude models.

        Recording the *requested* 0.7 would put a number in the audit record
        that no provider ever saw, and would tell a reader of CHECKPOINT B that
        §1.2's spread was tuned when it was not.
        """
        response = await _draw("anthropic", n=3, temperature=0.7)

        assert response.temperature is None
        assert response.seed is None, "no seed parameter either"

    @pytest.mark.parametrize("provider", ["openai", "ollama"])
    async def test_the_other_two_record_the_temperature_they_sent(self, provider: str) -> None:
        response = await _draw(provider, n=2, temperature=0.7)

        assert response.temperature == 0.7
        assert response.seed is not None

    async def test_only_the_local_provider_is_free(self) -> None:
        """`cost_usd` is zero for Ollama because local inference *is* free, and
        non-zero for a priced model. `pricing.estimate_cost` keeps that apart
        from the other zero - a model nobody has priced - because a ledger that
        reads an absence as free cannot be exceeded."""
        local = await _draw("ollama")
        metered = await _draw("anthropic")

        assert local.cost_usd == 0.0
        assert metered.cost_usd > 0.0, "claude-haiku-4-5 is in the price table"
