"""`build_llm`, the other composition root.  S9.1, S7.2

At S7.2 this was the least-covered module in `guardmem-core` - 61%, with both
SDK arms unexercised. That is a coverage number describing a real gap rather
than an accounting one: `selection.py` is what decides *which model answers*,
and `MEMORY_ENGINE.md` §3.1's entropy is the model's own variance, so a wrong
answer here makes every audit record and every AUROC a claim about a provider
nobody chose.

**No network, and none needed.** Constructing `AsyncAnthropic` or `AsyncOpenAI`
opens no connection - it builds a transport and waits - so the arms can be
entered, the adapter type checked, and the transport closed, entirely offline.
That is also what makes the closing assertion possible, and closing is the whole
reason `build_llm` became a context manager: it returned a bare adapter over an
SDK client nobody held until the S6.x hardening pass found the leak.

`tests/live/test_live_providers.py` is where a real key and a real call live,
deselected by default.
"""

from __future__ import annotations

import httpx
import pytest

from fixtures.mcp import settings
from guardmem_core.llm.providers import (
    AnthropicClient,
    OllamaClient,
    OpenAIClient,
    build_llm,
)


async def http() -> httpx.AsyncClient:
    """The client the Ollama arm is handed and the SDK arms ignore."""
    return httpx.AsyncClient(base_url="http://ollama.test")


class TestItBuildsWhatTheConfigurationNames:
    async def test_the_anthropic_arm_yields_an_anthropic_adapter(self) -> None:
        """The default provider, and the one `model_fast` and its siblings are
        ids for."""
        chosen = settings(llm_provider="anthropic", anthropic_api_key="k")

        async with await http() as transport, build_llm(chosen, transport) as client:
            assert isinstance(client, AnthropicClient)

    async def test_the_openai_arm_yields_an_openai_adapter(self) -> None:
        chosen = settings(llm_provider="openai", openai_api_key="k")

        async with await http() as transport, build_llm(chosen, transport) as client:
            assert isinstance(client, OpenAIClient)

    async def test_the_ollama_arm_yields_an_ollama_adapter(self) -> None:
        """The arm that needs no credential, which is what makes CHECKPOINT B
        runnable on a machine with no key."""
        chosen = settings(llm_provider="ollama")

        async with await http() as transport, build_llm(chosen, transport) as client:
            assert isinstance(client, OllamaClient)


class TestItClosesWhatItOpened:
    """The reason this is a context manager at all.

    `AsyncAnthropic` and `AsyncOpenAI` each own an `httpx.AsyncClient`, and
    `build_llm` used to return a bare adapter over one - so nothing in the
    process held a reference that could close it. The SDKs install a `__del__`
    that closes on collection, which is why it never showed a symptom and why a
    test has to look at the transport rather than at a warning.
    """

    async def test_the_anthropic_transport_is_closed_on_the_way_out(self) -> None:
        chosen = settings(llm_provider="anthropic", anthropic_api_key="k")

        async with await http() as transport:
            async with build_llm(chosen, transport) as client:
                # Narrowed rather than cast: `build_llm` yields the protocol,
                # and the transport being asserted on belongs to one concrete
                # adapter. An adapter of the wrong type is a failure this should
                # name, rather than an `AttributeError` on the next line.
                assert isinstance(client, AnthropicClient)
                sdk = client._client
                assert not sdk.is_closed()

            assert sdk.is_closed()

    async def test_the_openai_transport_is_closed_on_the_way_out(self) -> None:
        chosen = settings(llm_provider="openai", openai_api_key="k")

        async with await http() as transport:
            async with build_llm(chosen, transport) as client:
                assert isinstance(client, OpenAIClient)
                sdk = client._client
                assert not sdk.is_closed()

            assert sdk.is_closed()

    async def test_the_ollama_arm_never_closes_the_caller_s_client(self) -> None:
        """The asymmetry that makes the manager worth having. The Ollama adapter
        is *handed* a transport whose lifetime belongs to the composition root,
        so closing it here would shut down a client the caller is still using -
        `lifespan` opens one unconditionally and both arms share it.
        """
        chosen = settings(llm_provider="ollama")

        transport = await http()
        async with build_llm(chosen, transport):
            pass

        assert not transport.is_closed
        await transport.aclose()


class TestItRefusesRatherThanFallingBack:
    """The decision this module exists to make correctly.

    Quietly using the local model when a key is missing is the tempting
    kindness, and it makes two AUROCs incomparable: §3.1's entropy is the
    model's own variance, so a number measured on `llama3.1:8b` and one measured
    on Claude are different numbers wearing one name.
    """

    async def test_anthropic_without_a_key_names_the_variable(self) -> None:
        chosen = settings(llm_provider="anthropic", anthropic_api_key="")

        async with await http() as transport:
            with pytest.raises(ValueError, match="GM_ANTHROPIC_API_KEY"):
                async with build_llm(chosen, transport):
                    pass

    async def test_openai_without_a_key_names_the_variable(self) -> None:
        chosen = settings(llm_provider="openai", openai_api_key="")

        async with await http() as transport:
            with pytest.raises(ValueError, match="GM_OPENAI_API_KEY"):
                async with build_llm(chosen, transport):
                    pass

    async def test_a_blank_model_id_is_refused_before_a_client_is_built(self) -> None:
        """`tier_models` runs first on purpose, so a misconfigured tier fails
        without having opened a connection pool to complain from. A blank id
        would otherwise reach the provider as a request for a model named `""`,
        whose error is about the model rather than about the config.
        """
        chosen = settings(llm_provider="anthropic", anthropic_api_key="k", model_balanced="  ")

        async with await http() as transport:
            with pytest.raises(ValueError, match="GM_MODEL_FAST"):
                async with build_llm(chosen, transport):
                    pass
