"""Anthropic, behind `LLMClient`.  BUILD_NOTEBOOK.md S9.1

The primary provider (`requirements/llm.txt`: "S2.2 primary provider - extraction
+ adjudication"), through the official `anthropic` SDK rather than raw HTTP. The
SDK owns retries on 408/409/429/5xx, connection handling and the typed exception
hierarchy this module maps from; reimplementing that over `httpx` would be three
of those four done worse.

**There is no temperature, and this is the S9.1 finding that matters.**
`anthropic` 1.4.0's `messages.create` accepts no `temperature`, `top_p` or
`top_k` argument at all - sampling controls were removed on the current Claude
models and the SDK dropped the parameters with them. Verified by introspecting
the installed SDK, not recalled.

That has a consequence for the one measurement this project is built on.
`MEMORY_ENGINE.md` §1.2 draws the canonical sample at temperature 0 and the
other K-1 at 0.7, and §3.1 takes semantic entropy over that spread. On Anthropic
**neither number can be sent.** What survives is still a real measurement -
`_draw` issues K independent requests and a current Claude model is not
deterministic, so the samples do vary - but the variance is the model's own,
**not a spread this system tuned**. Two things follow, and both belong in
CHECKPOINT B's reading rather than in a comment nobody finds:

- a "temperature 0" sample is **not** a determinism claim here, so
  `LLMResponse.temperature` is `None` rather than `0.0`;
- `H_norm` measured on Anthropic and on Ollama are not the same instrument, and
  an AUROC that mixes them is comparing two.

**No `thinking` and no `effort` are sent, deliberately.** Both are per-model
gated in ways that turn a wrong guess into a 400 - `effort` errors on Haiku 4.5,
and disabling thinking on Opus 5 has two documented failure modes, one of which
writes a tool call into visible text. Every model's own default is the safe
choice at this step; tuning them against measured cost is S9.2's and S10.1's
job, with numbers.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Final

import anthropic

from guardmem_core.errors import BudgetExceeded, ProviderUnavailable
from guardmem_core.llm.base import LLMResponse
from guardmem_core.llm.providers.common import (
    TierModels,
    elapsed_ms,
    require_samples,
)
from guardmem_core.llm.providers.pricing import estimate_cost

if TYPE_CHECKING:
    from anthropic.types import OutputConfigParam
    from pydantic import BaseModel

    from guardmem_core.llm.base import Tier

__all__ = ["AnthropicClient"]

# `max_tokens` is required by the Messages API and has no default. Sixteen
# thousand is the non-streaming recommendation: large enough that a K-sample
# extraction batch is not truncated mid-object - which would surface as a
# `ValidationError` on JSON that simply stopped - and small enough to stay well
# inside the SDK's ten-minute HTTP timeout without streaming.
_MAX_TOKENS: Final = 16_000


class AnthropicClient:
    """An `LLMClient` over Anthropic's Messages API.

    Structurally an `LLMClient`, like every other seam in this package: it
    inherits from nothing and registers nowhere, and `mypy --strict` is what
    checks the conformance.
    """

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        models: TierModels,
        timeout_s: float,
    ) -> None:
        """Bind an SDK client to the tier ladder it serves.

        Args:
            client: An `AsyncAnthropic`. **Injected, not constructed here** -
                `RULES.md` §2.2 wants one client per provider created in
                lifespan, because the connection pool lives on it and a
                per-request client discards the pool along with every warm TLS
                session.
            models: Tier to pinned model id, from `common.tier_models`. The only
                route to a model id in this class, which is what makes
                `RULES.md` §3's "never floating aliases" checkable.
            timeout_s: Per-request ceiling, from `settings.llm_timeout_s`.
                Required and not defaulted: `RULES.md` §2.2 makes an outbound
                call without an explicit timeout a CI failure, and a default is
                how a call ends up with one nobody chose.
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
            schema: Constrains generation to this shape. **Parsing stays with
                the caller** - the protocol says so, and the reason is that a
                hallucinated field must raise where `trace_id` is in scope.
            tier: Which pinned id to use.
            temperature: **Accepted and not sent.** See the module docstring:
                the parameter does not exist on this API. Taken rather than
                rejected because the protocol publishes it and two other
                providers honour it; recorded as `None` rather than as the
                requested value, because the request never carried one.
            n: How many samples. Satisfied by `n` concurrent requests, since the
                Messages API has no `n`.

        Returns:
            The samples in draw order, plus the accounting `RULES.md` §3 wants.

        Raises:
            ProviderUnavailable: unreachable, timed out, rate limited, or a 5xx.
                Retryable.
            BudgetExceeded: the account's credit is exhausted. Not retryable -
                the balance does not refill because a caller asked again.
            anthropic.APIStatusError: a 400, 401, 403 or 404 propagates
                **unwrapped**, and that is deliberate. Those are configuration
                or programming errors - a wrong key, an unknown model id, a
                malformed schema - and dressing one as `ProviderUnavailable`
                would tell S9.3's circuit breaker to retry a wrong API key
                forever. The protocol's `Raises` list names the two a caller can
                act on; this is the third, and it is the one that should stop
                the process rather than be absorbed.
        """
        del temperature  # See the docstring. There is no parameter to send it to.
        require_samples(n)
        model = self._model_for(tier)
        started = time.perf_counter()
        messages = await self._draw(model, prompt=prompt, schema=schema, n=n)
        return self._response(model, messages, latency_ms=elapsed_ms(started))

    # --- internals ----------------------------------------------------------

    def _model_for(self, tier: Tier) -> str:
        """The pinned id for `tier`.

        Raises:
            KeyError: the tier is not in the configured mapping, which
                `tier_models` makes impossible for the three that exist and
                which would otherwise be a new `Tier` member nobody configured.
        """
        return self._models[tier]

    async def _draw(
        self, model: str, *, prompt: str, schema: type[BaseModel] | None, n: int
    ) -> list[anthropic.types.Message]:
        """Issue `n` requests concurrently and return every reply.

        Returns:
            The messages, in the order the samples were requested - `gather`
            preserves argument order regardless of completion order, which is
            what makes "sample 0 is canonical" meaningful.

        Raises:
            ProviderUnavailable, BudgetExceeded: as `complete`.

        `gather` without `return_exceptions`, so one failed draw fails the whole
        call. That is the right shape *here* and the opposite of
        `pipeline/orchestrator.py`'s choice, for a reason worth stating: the
        orchestrator fans out over independent candidates, where nineteen good
        results should survive one bad one, while these `n` samples are one
        measurement. `MEMORY_ENGINE.md` §3.1 normalises entropy by `log K`, so
        returning K-1 samples for a K-sample draw would not degrade the score,
        it would silently compute a different one.
        """
        bounded = min(n, require_samples(n))
        return await asyncio.gather(
            *(self._one(model, prompt=prompt, schema=schema) for _ in range(bounded))
        )

    async def _one(
        self, model: str, *, prompt: str, schema: type[BaseModel] | None
    ) -> anthropic.types.Message:
        """One Messages request, with the provider's errors translated.

        Structured output goes through `output_config.format`, which is the
        current parameter - `output_format` is deprecated - and it is the whole
        of `RULES.md` §3's "no regex over free text": the model is constrained to
        the schema rather than asked nicely for JSON.

        `effort` is deliberately absent from `output_config` even though it
        lives there: it errors on Haiku 4.5, which is `GM_MODEL_FAST`.
        """
        # `anthropic.omit`, not `NOT_GIVEN`: the 1.x SDK types the optional
        # parameters as `T | Omit`, and the older sentinel no longer satisfies it.
        output_config: OutputConfigParam | anthropic.Omit = anthropic.omit
        if schema is not None:
            output_config = {
                "format": {"type": "json_schema", "schema": schema.model_json_schema()}
            }
        try:
            # Spelled out rather than splatted from a dict: `**request` erases
            # the return type to `Any`, which `mypy --strict` rejects and which
            # would also have hidden a misspelled parameter behind a runtime
            # `TypeError` on the first real call.
            return await self._client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                output_config=output_config,
                timeout=self._timeout_s,
            )
        except anthropic.RateLimitError as exc:
            raise ProviderUnavailable(f"anthropic rate limited {model}: {exc}") from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailable(f"anthropic unreachable for {model}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise _translate_status(exc, model) from exc

    def _response(
        self, model: str, messages: list[anthropic.types.Message], *, latency_ms: float
    ) -> LLMResponse:
        """Fold `n` replies into the one `LLMResponse` the protocol returns.

        Token counts and the cache flag are summed across the draws rather than
        taken from the first: `n` requests are `n` billable calls, and a ledger
        fed only the first would under-report a K=5 extraction fivefold.

        `cache_hit` is `all`, not `any` - the same reading `extractor.py` uses
        when it folds several responses together. "The prompt cache served this
        call" is only true of the whole draw if it was true of every request in
        it, and `any` would let one warm request describe four cold ones.
        """
        usage = [message.usage for message in messages]
        tokens_in = sum(u.input_tokens for u in usage)
        tokens_out = sum(u.output_tokens for u in usage)
        return LLMResponse(
            samples=[_text_of(message) for message in messages],
            # What actually served the call, read off the response rather than
            # echoed from the request: `ARCHITECTURE.md` §2.8 lets a fallback
            # serve a FAST call from another model, and replay has to know which.
            model=messages[0].model,
            temperature=None,
            # No seed parameter on this API. `None` is the honest statement
            # about reproducibility, exactly as `LLMResponse` documents.
            seed=None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_hit=all((u.cache_read_input_tokens or 0) > 0 for u in usage),
            latency_ms=latency_ms,
            cost_usd=estimate_cost(model, tokens_in=tokens_in, tokens_out=tokens_out),
        )


def _text_of(message: anthropic.types.Message) -> str:
    """The text a sample consists of.

    Returns:
        Every text block, concatenated. Blocks that are not text - `thinking`,
        `tool_use` - are skipped rather than stringified: a caller is about to
        run `model_validate_json` on this, and a thinking block prepended to the
        JSON turns a good completion into a parse error.

    Raises:
        ProviderUnavailable: the reply carried no text at all. That is a
            provider-side outcome rather than a caller error - a refusal, or a
            response truncated at `max_tokens` before any text - and
            `LLMResponse.samples` requires `min_length=1`, so an empty string
            here would fail validation with a message about the model rather
            than about the call.
    """
    text = "".join(block.text for block in message.content if block.type == "text")
    if not text:
        raise ProviderUnavailable(
            f"anthropic returned no text for {message.model} "
            f"(stop_reason={message.stop_reason!r}); nothing to parse"
        )
    return text


def _translate_status(exc: anthropic.APIStatusError, model: str) -> Exception:
    """Decide what an HTTP status means to this system.

    Returns:
        The exception to raise. `ProviderUnavailable` for a 5xx, which is
        transient; `BudgetExceeded` for a 402, which is not; and the SDK's own
        exception for everything else, which is the configuration class - see
        `complete`'s `Raises`.

    `BudgetExceeded` is reached from the *provider's* billing here, which is not
    quite what the error was introduced for - `RULES.md` §2.3 frames it as the
    tenant's cap, and that cap is S10.1's ledger. The two agree on the only
    thing a caller does with it: stop, because asking again does not help.
    """
    if exc.status_code >= 500:
        return ProviderUnavailable(f"anthropic {exc.status_code} for {model}: {exc}")
    if exc.status_code == 402:
        return BudgetExceeded(f"anthropic credit exhausted for {model}: {exc}")
    return exc
