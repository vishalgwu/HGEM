"""Three providers on a mock transport.  BUILD_NOTEBOOK.md S9.1

S9.1's DONE WHEN is "the same prompt runs against all three in an integration
test", and two of the three bill per token and need a credential that is a
secret. `RULES.md` §5 settles how that is reconciled - "no network in unit
tests; LLM calls mocked with recorded fixtures in unit, live only in the nightly
eval job" - and this module is the recorded fixtures.

**Mocked at the transport, not at the adapter.** Every body below is handed to
the provider's own SDK, which parses and validates it before the adapter ever
sees it. So the test exercises the real request construction, the real response
model, and the real error types; what it replaces is the socket. A hand-built
`LLMResponse` would have tested the test.

Both vendor SDKs run on `httpx2` and the Ollama adapter on `httpx`, so there are
two `MockTransport` flavours here and they are not interchangeable - passing one
to the other's client fails at request time with a type error about the
transport rather than anything to do with this project.

The Ollama body is the shape a **running Ollama 0.34.0** actually returned,
field for field, including `prompt_eval_cached_count`. The other two are the
shapes their SDKs validate.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import anthropic
import httpx
import httpx2
import openai
from pydantic import BaseModel

from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import OllamaClient

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "MODELS",
    "PROMPT",
    "Capital",
    "anthropic_client",
    "anthropic_transport",
    "ollama_adapter",
    "ollama_client",
    "ollama_transport",
    "openai_client",
    "openai_transport",
]


class Capital(BaseModel):
    """The one schema all three providers are asked to fill.

    Deliberately trivial. What the contract test measures is that three very
    different wire formats produce one `LLMResponse`; a schema with nested
    objects would add a second thing being tested - whose JSON Schema conversion
    is right - to a test about the protocol.
    """

    capital: str
    country: str


# One prompt, three providers. The whole point of S9.1's DONE WHEN.
PROMPT: Final = "What is the capital of France?"

# What the providers answer. Valid `Capital` JSON, so a caller's
# `model_validate_json` succeeds - which is what proves `schema=` reached the
# provider in a form it honoured.
ANSWER: Final = json.dumps({"capital": "Paris", "country": "France"})

# Every tier on one id per provider. A real deployment gives the three tiers
# three different models; these tests are about the adapter, and a ladder with
# three distinct ids would make every assertion name one arbitrarily.
# Short: a mock transport answers instantly, and a generous timeout here would
# only slow a hang down.
_TIMEOUT_S: Final = 5.0

MODELS: Final = {
    "anthropic": dict.fromkeys(Tier, "claude-haiku-4-5"),
    "openai": dict.fromkeys(Tier, "gpt-4o-mini"),
    # A pinned tag. `:latest` is refused by the adapter - see
    # `_reject_floating_tag` - so a fixture using it would test the guard rather
    # than the provider.
    "ollama": dict.fromkeys(Tier, "llama3.1:8b"),
}


def anthropic_transport(
    *, answer: str = ANSWER, status: int = 200, body: dict[str, Any] | None = None
) -> httpx2.MockTransport:
    """A transport that answers the Messages API.

    Args:
        answer: The assistant text.
        status: HTTP status. Anything but 200 returns `body` as the error.
        body: The error body for a non-200.

    Returns:
        A transport for `AsyncAnthropic(http_client=...)`.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        if status != 200:
            return httpx2.Response(status, json=body or {"error": {"message": "mock"}})
        return httpx2.Response(
            200,
            json={
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "content": [{"type": "text", "text": answer}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        )

    return httpx2.MockTransport(handler)


def openai_transport(
    *,
    answer: str = ANSWER,
    status: int = 200,
    body: dict[str, Any] | None = None,
    choices: int = 1,
) -> httpx2.MockTransport:
    """A transport that answers Chat Completions.

    Args:
        answer: The assistant content of every choice.
        status: HTTP status.
        body: The error body for a non-200.
        choices: How many choices to return - OpenAI satisfies `n` natively, so
            this is what a multi-sample draw looks like on the wire.

    Returns:
        A transport for `AsyncOpenAI(http_client=...)`.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        if status != 200:
            return httpx2.Response(status, json=body or {"error": {"message": "mock"}})
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl_mock",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": index,
                        "message": {"role": "assistant", "content": answer, "refusal": None},
                        "finish_reason": "stop",
                        "logprobs": None,
                    }
                    for index in range(choices)
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            },
        )

    return httpx2.MockTransport(handler)


def ollama_transport(
    *,
    answer: str = ANSWER,
    status: int = 200,
    seen: list[dict[str, Any]] | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> httpx.MockTransport:
    """A transport that answers `/api/chat`.

    Args:
        answer: The assistant content.
        status: HTTP status.
        seen: When given, every decoded request body is appended to it - which is
            how a test asserts that the seed *varied across a draw*, the bug that
            made every sample in a K-sample draw identical.
        handler: A complete replacement, for the cases a canned body cannot
            express (a transport error, a body that is not JSON).

    Returns:
        A transport for `httpx.AsyncClient(transport=...)`.

    The body is Ollama 0.34.0's, field for field. `prompt_eval_cached_count` is
    the field `LLMResponse.cache_hit` reads and is the one most easily got wrong
    from memory - it is a token *count*, not a boolean.
    """

    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, text="mock failure")
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:8b",
                "created_at": "2026-09-15T00:00:00Z",
                "message": {"role": "assistant", "content": answer},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 11,
                "prompt_eval_cached_count": 0,
                "eval_count": 7,
                "total_duration": 1_000_000,
            },
        )

    return httpx.MockTransport(handler or respond)


def anthropic_client(transport: httpx2.MockTransport) -> anthropic.AsyncAnthropic:
    """An `AsyncAnthropic` that talks to `transport` and never to the network.

    The key is a placeholder: the transport never checks it, and the SDK refuses
    to construct without one. `max_retries=0` because the SDK retries 429s and
    5xxs by default, which would turn a one-line error-mapping test into four
    identical requests and a slow suite.
    """
    return anthropic.AsyncAnthropic(
        api_key="test-key-not-a-secret",  # pragma: allowlist secret
        http_client=httpx2.AsyncClient(transport=transport),
        max_retries=0,
    )


def openai_client(transport: httpx2.MockTransport) -> openai.AsyncOpenAI:
    """An `AsyncOpenAI` on `transport`. Same reasoning as `anthropic_client`."""
    return openai.AsyncOpenAI(
        api_key="test-key-not-a-secret",  # pragma: allowlist secret
        http_client=httpx2.AsyncClient(transport=transport),
        max_retries=0,
    )


def ollama_client(transport: httpx.MockTransport) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` on `transport`, with the base URL the adapter joins to."""
    return httpx.AsyncClient(transport=transport, base_url="http://ollama.test")


def ollama_adapter(transport: httpx.MockTransport) -> tuple[OllamaClient, httpx.AsyncClient]:
    """An `OllamaClient` on `transport`, and the http client to close afterwards.

    Here rather than in one test module because two of them build it now:
    `test_llm_providers.py` for the cross-provider contract, and
    `test_ollama_grammar.py` for the `format` payload. A second copy would be
    free to drift on the timeout or the model map, which is what `MODELS` is
    above this for.
    """
    http = ollama_client(transport)
    return OllamaClient(http, models=MODELS["ollama"], timeout_s=_TIMEOUT_S), http
