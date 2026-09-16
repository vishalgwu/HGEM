"""What a provider failure becomes by the time the pipeline sees it.  S9.1, S7.2

Split from `test_llm_providers.py` at S7.2, when the error branches this module
exists for pushed that one past `RULES.md` §2.4's 400-line cap. The seam is
real: everything here answers one question - *what does this failure mean to
the caller* - while what remains there is about sampling, accounting, bounds and
timeouts.

**The distinction the whole module turns on is retryable versus not.** Over HTTP
the two look alike: a 429 that means "slow down" and a 429 that means "your
account is empty" arrive identically, and a breaker reading the status alone
would back off and retry an exhausted account until its cap. `ARCHITECTURE.md`
§2.8's fallback and S9.3's circuit breaker both read the domain type rather than
the status, so a wrong translation here is a wrong decision three layers up.

**And the case with no status code at all.** A connection that never opened is
not a 5xx - a 5xx means the provider is there and unwell, this means it is not
there - and both adapters have to produce the same domain error for it, or the
router's fallback would depend on which provider happened to fail first.
"""

from __future__ import annotations

from typing import Final

import anthropic
import httpx
import openai
import pytest

from fixtures.providers import (
    MODELS,
    PROMPT,
    anthropic_client,
    anthropic_transport,
    ollama_adapter,
    ollama_transport,
    openai_client,
    openai_transport,
    unreachable_transport,
)
from guardmem_core.errors import BudgetExceeded, ProviderUnavailable
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import AnthropicClient, OpenAIClient

_TIMEOUT_S: Final = 5.0


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

    async def test_anthropic_rate_limiting_is_retryable(self) -> None:
        """A 429 says "later", not "no". `ARCHITECTURE.md` §2.8 lets the breaker
        back off and try another provider; a non-retryable error here would make
        it give up on a provider that is merely busy."""
        sdk = anthropic_client(anthropic_transport(status=429))
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is True

    async def test_anthropic_that_cannot_be_reached_at_all_is_retryable(self) -> None:
        """The case no status code can express, because nothing answered.

        Distinct from a 5xx: that means the provider is there and unwell, this
        means it is not there - a DNS failure, a dead socket, a proxy that
        refused. Both are retryable and both must reach the caller as the same
        domain error, or a breaker has to learn two shapes for one outcome.
        """
        sdk = anthropic_client(unreachable_transport())
        client = AnthropicClient(sdk, models=MODELS["anthropic"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is True
        assert "unreachable" in str(caught.value)

    async def test_openai_that_cannot_be_reached_at_all_is_retryable(self) -> None:
        """The same absence, on the other adapter. Two adapters that disagreed
        about what "nothing answered" means would make the router's fallback
        depend on which provider failed first."""
        sdk = openai_client(unreachable_transport())
        client = OpenAIClient(sdk, models=MODELS["openai"], timeout_s=_TIMEOUT_S)

        async with sdk:
            with pytest.raises(ProviderUnavailable) as caught:
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

        assert caught.value.retryable is True
        assert "unreachable" in str(caught.value)

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

        client, http = ollama_adapter(ollama_transport(handler=refuse))

        async with http:
            with pytest.raises(ProviderUnavailable, match="ollama serve"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_an_ollama_error_status_is_a_provider_error(self) -> None:
        client, http = ollama_adapter(ollama_transport(status=404))

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
        client, http = ollama_adapter(ollama_transport(answer=""))

        async with http:
            with pytest.raises(ProviderUnavailable, match="empty content"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)

    async def test_ollama_with_a_body_that_is_not_json(self) -> None:
        def html(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>proxy error</html>")

        client, http = ollama_adapter(ollama_transport(handler=html))

        async with http:
            with pytest.raises(ProviderUnavailable, match="non-JSON"):
                await client.complete(prompt=PROMPT, tier=Tier.FAST)
