"""OpenAI, behind `LLMClient`.  BUILD_NOTEBOOK.md S9.1

`requirements/llm.txt` calls this the "fallback provider", and that is its job:
`ARCHITECTURE.md` §4's degradation matrix requires cross-provider fallback, and
a single-provider build cannot satisfy it. S9.3 builds the breaker that switches
over; this is the thing it switches to.

**It is also the control that proves the protocol is a protocol.** Anthropic and
OpenAI disagree about almost every detail this adapter touches - `n` exists on
one and not the other, temperature exists on one and not the other, usage fields
are named differently, the structured-output parameter has a different shape -
and the `LLMResponse` a caller gets back is the same object either way. The
integration test that runs one prompt through all three is what checks that
claim rather than asserting it.

**Three things this provider can do that Anthropic cannot**, each of which shows
up as a *filled* field where the Anthropic adapter writes `None` or fans out:

- `n` is a real parameter, so K samples cost one request rather than K;
- `temperature` is a real parameter, so `MEMORY_ENGINE.md` §1.2's 0.7 spread is
  the spread it asks for;
- `seed` is a real parameter, so a draw is reproducible and `LLMResponse.seed`
  says which one.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

import openai

from guardmem_core.errors import BudgetExceeded, ProviderUnavailable
from guardmem_core.llm.base import LLMResponse
from guardmem_core.llm.providers.common import TierModels, elapsed_ms, require_samples
from guardmem_core.llm.providers.pricing import estimate_cost

if TYPE_CHECKING:
    from openai.types.chat import ParsedChatCompletion
    from pydantic import BaseModel

    from guardmem_core.llm.base import Tier

__all__ = ["OpenAIClient"]

# Fixed, so a draw can be reproduced from the audit record. Not configurable:
# `LLMResponse.seed` exists to say *which* seed ran, and a seed that varied per
# call would make the field a log of randomness rather than a way to repeat it.
# S9.2 may want it per-request; today one value is the honest one.
_SEED: Final = 0


class OpenAIClient:
    """An `LLMClient` over OpenAI's Chat Completions API."""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        models: TierModels,
        timeout_s: float,
    ) -> None:
        """Bind an SDK client to the tier ladder it serves.

        Args:
            client: An `AsyncOpenAI`, injected for the reason `RULES.md` §2.2
                gives - one client per provider, created in lifespan.
            models: Tier to pinned model id. **Note what this means for a
                fallback:** the ids in `GM_MODEL_*` are Claude ids today, so
                pointing a tier at this provider means changing them. S9.3 is
                where per-provider ladders belong; this class takes whatever
                mapping it is handed and asks no questions about the vendor.
            timeout_s: Per-request ceiling, from `settings.llm_timeout_s`.
        """
        self._client = client
        self._models = models
        self._timeout_s = timeout_s

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        """Draw `n` completions from the model serving `tier`.

        Args:
            prompt: The rendered prompt.
            schema: Constrains generation. Passed to `.parse()` as the pydantic
                model itself rather than as a hand-built JSON Schema - the SDK
                converts it, including the `additionalProperties: false` that
                strict mode requires at *every* level, which is the detail a
                hand-rolled conversion gets wrong on the first nested object.
            tier: Which pinned id to use.
            temperature: Sent, and recorded as sent.
            n: How many samples. One request, unlike the other two adapters.

        Returns:
            The samples in the order the API returned them.

        Raises:
            ProviderUnavailable: unreachable, timed out, rate limited, or a 5xx.
            BudgetExceeded: the account's quota is exhausted (429 with an
                `insufficient_quota` code, which OpenAI returns on the same
                status as rate limiting - see `_translate_rate_limit`).
            openai.APIStatusError: 4xx configuration errors propagate unwrapped,
                for the reason the Anthropic adapter's docstring gives at
                length: a wrong API key must not look retryable to a circuit
                breaker.

        `.parse()` rather than `.create()`, and the samples are still read as
        **text**: `parse` gives the SDK's schema conversion and validation, and
        `message.content` is the raw JSON underneath it. The protocol keeps
        parsing with the caller, so the parsed object is deliberately discarded.
        """
        require_samples(n)
        model = self._models[tier]
        started = time.perf_counter()
        completion = await self._call(
            model, prompt=prompt, schema=schema, temperature=temperature, n=n
        )
        return self._response(
            model, completion, temperature=temperature, latency_ms=elapsed_ms(started)
        )

    # --- internals ----------------------------------------------------------

    async def _call(
        self,
        model: str,
        *,
        prompt: str,
        schema: type[BaseModel] | None,
        temperature: float,
        n: int,
    ) -> ParsedChatCompletion[BaseModel]:
        """One request, with the provider's errors translated."""
        try:
            return await self._client.chat.completions.parse(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                # `openai.omit`, not `NOT_GIVEN`: the 3.x SDK types this
                # parameter as `type[ResponseFormatT] | Omit`, and the older
                # sentinel no longer satisfies it.
                response_format=schema if schema is not None else openai.omit,
                temperature=temperature,
                seed=_SEED,
                n=n,
                timeout=self._timeout_s,
            )
        except openai.RateLimitError as exc:
            raise _translate_rate_limit(exc, model) from exc
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            raise ProviderUnavailable(f"openai unreachable for {model}: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailable(f"openai {exc.status_code} for {model}: {exc}") from exc
            raise

    def _response(
        self,
        model: str,
        completion: ParsedChatCompletion[BaseModel],
        *,
        temperature: float,
        latency_ms: float,
    ) -> LLMResponse:
        """Turn one completion into the protocol's response.

        Raises:
            ProviderUnavailable: a choice carried no content - a refusal, or a
                length stop before any text. `LLMResponse.samples` requires at
                least one, and an empty string would fail validation with a
                message about the model rather than about the call.
        """
        samples = [choice.message.content or "" for choice in completion.choices]
        if not samples or not all(samples):
            raise ProviderUnavailable(
                f"openai returned an empty choice for {model} "
                f"(finish_reasons={[c.finish_reason for c in completion.choices]}); "
                "nothing to parse"
            )
        usage = completion.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        return LLMResponse(
            samples=samples,
            model=completion.model,
            temperature=temperature,
            seed=_SEED,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_hit=_cached_tokens(completion) > 0,
            latency_ms=latency_ms,
            cost_usd=estimate_cost(model, tokens_in=tokens_in, tokens_out=tokens_out),
        )


def _cached_tokens(completion: ParsedChatCompletion[BaseModel]) -> int:
    """How many prompt tokens the provider served from its cache.

    Returns:
        The count, or zero when the provider did not report one. Read through
        `getattr` because `prompt_tokens_details` is optional on the usage
        object and absent on older API surfaces - and a missing *detail* means
        "not reported", which is not the same as "not cached" but is the only
        thing that can honestly be written into a boolean.
    """
    usage = completion.usage
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    return int(getattr(details, "cached_tokens", 0) or 0)


def _error_code(exc: openai.APIStatusError) -> str | None:
    """The vendor's own error code, if it sent one.

    Returns:
        The code, or `None`.

    `exc.code` is the SDK's own parsed attribute and is what this reads. The
    first version walked `exc.body["error"]["code"]` instead and always returned
    `None`, because **`body` is the inner error object rather than the whole
    envelope** - `{"message": ..., "code": ...}`, not `{"error": {...}}`. The
    effect was that every quota exhaustion was classified as a plain rate limit
    and would have been retried with backoff until the attempt cap, forever,
    which is exactly the behaviour `_translate_rate_limit` exists to prevent.
    Caught by a test with a realistic body; it does not reproduce against a
    hand-built exception.
    """
    return exc.code


def _translate_rate_limit(exc: openai.RateLimitError, model: str) -> Exception:
    """Separate "slow down" from "you are out of credit".

    Returns:
        `BudgetExceeded` when the body names `insufficient_quota`, and
        `ProviderUnavailable` otherwise.

    OpenAI returns both on **429**, which is the trap: a breaker that read the
    status alone would retry an exhausted account with backoff until the
    attempt cap, every time, for as long as the account stayed empty. The two
    need different responses - one clears on its own and the other needs a human
    - so the code in the body is what decides.
    """
    if _error_code(exc) == "insufficient_quota":
        return BudgetExceeded(f"openai quota exhausted for {model}: {exc}")
    return ProviderUnavailable(f"openai rate limited {model}: {exc}")
