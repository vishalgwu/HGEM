"""The provider adapters, piece by piece.  BUILD_NOTEBOOK.md S9.1

`tests/integration/test_provider_contract.py` asserts the one thing S9.1's DONE
WHEN asks for - the same prompt through all three. This module covers what that
cannot: the failure paths, the bounds, and the two guards that exist because
getting them wrong is silent.

The order below is roughly by how badly a regression would hurt:

1. **The seed varies across a draw.** Getting this wrong makes every sample in a
   K-sample draw identical and every entropy score zero, and nothing raises.
2. **Errors map to the right domain type.** A retryable class and a
   configuration class look alike over HTTP and need opposite responses - S9.3's
   circuit breaker is the thing that would otherwise retry a wrong API key until
   its attempt cap, every time.
3. **`0.0` cost means two different things**, and `pricing` keeps them apart.
4. Bounds and pinning - `n`, blank model ids, floating tags.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import anthropic
import httpx
import openai
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
from guardmem_core.errors import BudgetExceeded, ProviderUnavailable
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import AnthropicClient, OllamaClient, OpenAIClient, tier_models
from guardmem_core.llm.providers.common import MAX_SAMPLES, require_samples
from guardmem_core.llm.providers.ollama_client import BASE_SEED
from guardmem_core.llm.providers.pricing import PRICES, estimate_cost
from guardmem_core.settings import Settings

_TIMEOUT_S: Final = 5.0


def ollama(transport: httpx.MockTransport) -> tuple[OllamaClient, httpx.AsyncClient]:
    """An Ollama adapter and the client to close afterwards."""
    http = ollama_client(transport)
    return OllamaClient(http, models=MODELS["ollama"], timeout_s=_TIMEOUT_S), http


class TestTheSeedVariesAcrossADraw:
    """The bug a mock could not have found, pinned so it cannot come back.

    Found by running the adapter against a real Ollama: one fixed seed made
    three "independent" samples at temperature 0.7 come back character-identical,
    because Ollama honours a seed exactly. `MEMORY_ENGINE.md` §3.1 clusters those
    samples and §3.2 reads the entropy, so identical samples mean `H_norm = 0`
    and maximum confidence on every candidate - silently.

    A mock transport returns what it is told, so it cannot reproduce the
    *symptom*. What it can check is the **cause**, which is what these assert:
    the requests that went out carried different seeds.
    """

    async def test_each_request_in_a_draw_carries_its_own_seed(self) -> None:
        seen: list[dict[str, Any]] = []
        client, http = ollama(ollama_transport(seen=seen))

        async with http:
            await client.complete(prompt=PROMPT, tier=Tier.FAST, temperature=0.7, n=4)

        seeds = [request["options"]["seed"] for request in seen]
        assert seeds == [BASE_SEED, BASE_SEED + 1, BASE_SEED + 2, BASE_SEED + 3]
        assert len(set(seeds)) == 4, "identical seeds would make the samples identical"

    async def test_a_single_sample_draw_uses_the_base_seed(self) -> None:
        """So a K=1 extraction is reproducible from the recorded seed alone."""
        seen: list[dict[str, Any]] = []
        client, http = ollama(ollama_transport(seen=seen))

        async with http:
            response = await client.complete(prompt=PROMPT, tier=Tier.FAST, n=1)

        assert seen[0]["options"]["seed"] == BASE_SEED
        assert response.seed == BASE_SEED, "the base is what the audit records"

    async def test_the_temperature_asked_for_is_the_one_sent(self) -> None:
        """`MEMORY_ENGINE.md` §1.2's 0.7 spread, on a provider that has the knob."""
        seen: list[dict[str, Any]] = []
        client, http = ollama(ollama_transport(seen=seen))

        async with http:
            await client.complete(prompt=PROMPT, tier=Tier.FAST, temperature=0.7, n=2)

        assert {request["options"]["temperature"] for request in seen} == {0.7}

    async def test_the_schema_reaches_the_wire(self) -> None:
        """`RULES.md` §3 forbids regex over free text: the model is *constrained*
        to the shape rather than asked for JSON in the prompt."""
        seen: list[dict[str, Any]] = []
        client, http = ollama(ollama_transport(seen=seen))

        async with http:
            await client.complete(prompt=PROMPT, schema=Capital, tier=Tier.FAST)

        assert seen[0]["format"]["properties"].keys() == {"capital", "country"}
        assert seen[0]["stream"] is False


class TestErrorsMapToTheRightDomainType:
    """Retryable and not-retryable look alike over HTTP and need opposite answers."""

    async def test_anthropic_5xx_is_retryable(self) -> None:
        sdk = anthropic_client(anthropic_transport(status=503))
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is True

    async def test_anthropic_402_is_a_budget_error_and_is_not_retryable(self) -> None:
        """The balance does not refill because a caller asked again."""
        sdk = anthropic_client(anthropic_transport(status=402))
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(BudgetExceeded) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is False

    async def test_anthropic_401_propagates_unwrapped(self) -> None:
        """**Deliberate, and the most arguable decision in the adapter.** A wrong
        API key is a configuration error, and dressing it as `ProviderUnavailable`
        would tell S9.3's breaker it is worth retrying - forever, since a key
        does not become correct."""
        sdk = anthropic_client(anthropic_transport(status=401))
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(anthropic.AuthenticationError):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_openai_quota_exhaustion_is_a_budget_error_not_a_rate_limit(self) -> None:
        """Both arrive as **429**, and that is the trap: a breaker reading the
        status alone would back off and retry an empty account until its cap."""
        sdk = openai_client(
            openai_transport(
                status=429, body={"error": {"message": "quota", "code": "insufficient_quota"}}
            )
        )
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(BudgetExceeded):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_openai_plain_rate_limiting_stays_retryable(self) -> None:
        """The control for the test above - same status, different code."""
        sdk = openai_client(
            openai_transport(status=429, body={"error": {"message": "slow down", "code": None}})
        )
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is True

    async def test_openai_5xx_is_retryable(self) -> None:
        sdk = openai_client(openai_transport(status=502))
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_openai_400_propagates_unwrapped(self) -> None:
        sdk = openai_client(openai_transport(status=400))
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(openai.BadRequestError):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_an_unreachable_ollama_says_so(self) -> None:
        """Including the hint, because "connection refused" to localhost has one
        overwhelmingly likely cause and naming it saves a support round trip."""

        def refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client, http = ollama(ollama_transport(handler=refuse))

        async with http:
            with pytest.raises(ProviderUnavailable, match="ollama serve"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_an_ollama_error_status_is_a_provider_error(self) -> None:
        client, http = ollama(ollama_transport(status=404))

        async with http:
            with pytest.raises(ProviderUnavailable, match="404"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)


class TestAnEmptyReplyIsAProviderProblem:
    """`LLMResponse.samples` requires one non-empty sample.

    A refusal, or a response truncated at `max_tokens` before any text, produces
    none. Left alone that surfaces as a pydantic `ValidationError` about
    `min_length`, which reads as a bug in this code rather than as something the
    model did.
    """

    async def test_anthropic_with_no_text_block(self) -> None:
        sdk = anthropic_client(anthropic_transport(answer=""))
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable, match="no text"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_openai_with_an_empty_choice(self) -> None:
        sdk = openai_client(openai_transport(answer=""))
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable, match="empty choice"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_ollama_with_empty_content(self) -> None:
        client, http = ollama(ollama_transport(answer=""))

        async with http:
            with pytest.raises(ProviderUnavailable, match="empty content"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_ollama_with_a_body_that_is_not_json(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>proxy error</html>")

        client, http = ollama(ollama_transport(handler=html))

        async with http:
            with pytest.raises(ProviderUnavailable, match="non-JSON"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)


class TestZeroCostMeansTwoDifferentThings:
    """A ledger that reads an absence as free is a ledger that cannot be exceeded."""

    def test_a_priced_model_costs_what_the_table_says(self) -> None:
        cost = estimate_cost("claude-haiku-4-5", tokens_in=1_000_000, tokens_out=0)

        assert cost == PRICES["claude-haiku-4-5"].usd_in

    def test_input_and_output_are_priced_separately(self) -> None:
        """Output is five times input on every model in the table, so a function
        that summed the tokens first would be wrong by a factor that varies with
        the workload - and would look plausible on any single example."""
        price = PRICES["claude-opus-5"]

        cost = estimate_cost("claude-opus-5", tokens_in=1_000_000, tokens_out=1_000_000)

        assert cost == pytest.approx(price.usd_in + price.usd_out)
        assert price.usd_out > price.usd_in

    def test_a_local_model_is_free_and_says_nothing_about_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            cost = estimate_cost("llama3.1:8b", tokens_in=999, tokens_out=999, local=True)

        assert cost == 0.0
        assert not caplog.records, "local inference is genuinely free, not unpriced"

    def test_an_unpriced_model_is_zero_and_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """The other zero. S10.1's ledger must not read this as a spend."""
        with caplog.at_level(logging.WARNING):
            cost = estimate_cost("gpt-9-nonexistent", tokens_in=999, tokens_out=999)

        assert cost == 0.0
        assert any("ABSENCE" in record.message for record in caplog.records)


class TestBoundsAndPinning:
    def test_n_is_bounded_because_two_adapters_fan_out(self) -> None:
        """`RULES.md` §2.2 forbids unbounded fan-out, and `LLMClient.complete`
        publishes `n: int` with no ceiling of its own."""
        with pytest.raises(ValueError, match="fan-out ceiling"):
            require_samples(MAX_SAMPLES + 1)

    def test_n_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            require_samples(0)

    def test_a_blank_model_id_is_refused_at_construction(self) -> None:
        """Pydantic catches a *missing* required id and not an empty one, and an
        empty id reaches the provider as a request for a model named `""`."""
        # Built through a `dict[str, Any]`, as `test_settings.py` does. The DSN
        # fields are typed `PostgresDsn`/`RedisDsn`/`AnyUrl`, which pydantic
        # coerces from a string at runtime and `mypy --strict` rejects at the
        # keyword.
        fields: dict[str, Any] = {
            "database_url": "postgresql+asyncpg://localhost:5432/db",
            "redis_url": "redis://localhost:6379/0",
            "neo4j_uri": "bolt://localhost:7687",
            "neo4j_user": "neo4j",
            "neo4j_password": "unused-by-these-tests",  # pragma: allowlist secret
            "model_fast": "",
            "model_balanced": "claude-sonnet-5",
            "model_frontier": "claude-opus-5",
            "embed_model": "text-embedding-3-large",
        }
        settings = Settings(_env_file=None, **fields)

        with pytest.raises(ValueError, match="no model id configured"):
            tier_models(settings)

    @pytest.mark.parametrize("floating", ["llama3.1:latest", "llama3.1"])
    def test_a_floating_ollama_tag_is_refused(self, floating: str) -> None:
        """`RULES.md` §3: a recorded model id has to mean one thing forever, and
        `:latest` moves on the next `ollama pull`. A bare name resolves *to*
        `:latest`, so it is the same failure spelled shorter."""
        http = ollama_client(ollama_transport())

        with pytest.raises(ValueError, match="floats"):
            OllamaClient(http, models=dict.fromkeys(Tier, floating), timeout_s=_TIMEOUT_S)

    def test_a_pinned_tag_is_accepted(self) -> None:
        """The control. Refusing every tag would make the provider unusable."""
        http = ollama_client(ollama_transport())

        assert OllamaClient(http, models=MODELS["ollama"], timeout_s=_TIMEOUT_S) is not None

    async def test_each_tier_selects_its_own_pinned_id(self) -> None:
        """The whole point of `TierModels`: a tier is the *only* route to a model
        id, so `RULES.md` §3's "never floating aliases" is checkable in one place."""
        seen: list[dict[str, Any]] = []
        ladder = {
            Tier.FAST: "llama3.1:8b",
            Tier.BALANCED: "mistral:7b",
            Tier.FRONTIER: "qwen2:7b",
        }
        http = ollama_client(ollama_transport(seen=seen))
        client = OllamaClient(http, models=ladder, timeout_s=_TIMEOUT_S)

        async with http:
            for tier in Tier:
                await client.complete(prompt=PROMPT, tier=tier)

        assert [request["model"] for request in seen] == list(ladder.values())


class TestTheTimeoutIsThreadedThrough:
    """`RULES.md` §2.2 makes an outbound call without an explicit timeout a CI
    failure, and a timeout that is accepted and dropped is worse than none - it
    reads as compliant."""

    async def test_ollama_sends_the_configured_timeout(self) -> None:
        recorded: list[Any] = []

        def capture(request: httpx.Request) -> httpx.Response:
            recorded.append(request.extensions.get("timeout"))
            return httpx.Response(
                200,
                json={
                    "model": "llama3.1:8b",
                    "message": {"role": "assistant", "content": "{}"},
                    "done": True,
                    "prompt_eval_count": 1,
                    "eval_count": 1,
                },
            )

        client, http = ollama(ollama_transport(handler=capture))

        async with http:
            await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert recorded[0] is not None, "no timeout reached the transport"
