"""Ollama, behind `LLMClient`.  BUILD_NOTEBOOK.md S9.1

`requirements/llm.txt` states the one design decision this module rests on:
"Ollama needs no SDK - it is called over its HTTP API with httpx." So this is
the one adapter that speaks the wire itself, and the request and response shapes
below were read off a **running Ollama 0.34.0**, not recalled.

**It is the provider that makes S9.1's DONE WHEN reachable at all.** "The same
prompt runs against all three in an integration test" needs three providers a
test can actually call; two of them bill per token and need credentials that are
secrets. Ollama runs on the machine, costs nothing, and holds still - so the
contract test that proves one prompt produces one `LLMResponse` shape across
three very different APIs has somewhere to run for real rather than only against
recorded HTTP.

**What it buys the project beyond a fallback**: an offline provider means
CHECKPOINT B's discrimination gate can be *rehearsed* without spending money or
shipping a key, and `MEMORY_ENGINE.md` §1.2's temperature spread is a real
spread here - `options.temperature` and `options.seed` both exist, which is not
true of the primary provider. What it does not buy is a comparable number: a
7-billion-parameter local model and Claude Opus are not the same instrument, and
an AUROC measured on one says nothing about the other.

**Ollama compiles a JSON Schema into a grammar, and the compiler has limits
the schema layer knows nothing about.** `_grammar_safe` is that seam - see it
for the measurement. It is the one place this adapter alters what the caller
asked for, and it alters only what is *sent*, never what the reply is checked
against.

**Model ids are tags, and a tag is not a digest.** `RULES.md` §3 wants pinned
ids; `llama3.1:8b` pins a family and a size but not a build, and `:latest` pins
nothing at all. `_reject_floating_tag` refuses the worst case rather than
pretending the rest are immutable - see it for what that costs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Final

import httpx

from guardmem_core.errors import ProviderUnavailable
from guardmem_core.llm.base import LLMResponse
from guardmem_core.llm.providers.common import (
    TierModels,
    draw_samples,
    elapsed_ms,
    require_samples,
)
from guardmem_core.llm.providers.pricing import estimate_cost

if TYPE_CHECKING:
    from pydantic import BaseModel

    from guardmem_core.llm.base import Tier

__all__ = ["BASE_SEED", "OllamaClient"]

# The chat endpoint, relative to the base URL the injected client carries.
_CHAT: Final = "/api/chat"

# A tag that names no build. `:latest` moves under you on the next `ollama pull`,
# which makes every recorded `model` in the audit log ambiguous and every replay
# a guess - exactly what `RULES.md` §3 forbids.
_FLOATING: Final = "latest"

# Ollama turns `format` into a GBNF grammar, and a bounded length or item count
# becomes a *repetition* in that grammar. Repetitions of 2000 or more are
# refused outright, with `400 Failed to initialize samplers: failed to parse
# grammar` - the whole schema, not the one field.
#
# Measured against Ollama 0.34.0 with `llama3.1:8b`, by bisection: **1999
# compiles and 2000 does not**, for `maxLength`, `minLength` and `maxItems`
# alike. A sharp boundary at a round number, so it is a constant in the
# compiler rather than a size blow-up.
#
# This is not hypothetical. `ExtractedFact.verbatim` declares `max_length=2000`,
# mirroring `Provenance.verbatim`, which put the extraction schema exactly one
# over the line - so **no extraction ran on Ollama at all** until this was
# found, and CHECKPOINT B was recorded twice as runnable locally when it was
# not.
_GRAMMAR_REPETITION_LIMIT: Final = 2000

# The keywords that become repetitions. `minimum`/`maximum` do not - they bound
# a numeric *value*, not a count - and were measured to pass at 2000.
_REPETITION_KEYWORDS: Final = frozenset({"maxItems", "maxLength", "minItems", "minLength"})


class OllamaClient:
    """An `LLMClient` over a local Ollama server."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        models: TierModels,
        timeout_s: float,
    ) -> None:
        """Bind an HTTP client to the tier ladder it serves.

        Args:
            client: An `httpx.AsyncClient` whose `base_url` points at the Ollama
                server (`http://localhost:11434` by default). Injected for the
                same reason the SDK clients are - `RULES.md` §2.2 puts client
                creation in lifespan, and a per-request client throws away the
                connection pool.

                Plain `httpx`, not `httpx2`: `guardmem-core` declares `httpx`
                and this is its own client. The `anthropic` SDK is built on
                `httpx2` and owns its own transport; the two never meet.
            models: Tier to pinned model id.
            timeout_s: Per-request ceiling, from `settings.llm_timeout_s`.
                Passed explicitly on every request rather than left to the
                client's default, per `RULES.md` §2.2. **A local model is slow**
                - a 7B model answering a cold prompt took over a minute on the
                machine this was written on - so a timeout tuned for a hosted
                API will fire here. That is a configuration problem rather than
                a code one, and it is named so nobody debugs it twice.

        Raises:
            ValueError: a configured model id carries a floating tag.
        """
        for tier, model in models.items():
            _reject_floating_tag(tier, model)
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
            schema: Sent as Ollama's `format`, which takes a JSON Schema
                directly - so `model_json_schema()` goes over the wire as-is,
                with no vendor wrapper around it.
            tier: Which pinned id to use.
            temperature: Sent as `options.temperature`, and recorded as sent.
            n: How many samples. Satisfied by `n` concurrent requests - the chat
                endpoint returns one message.

        Returns:
            The samples in draw order, plus the accounting.

        Raises:
            ProviderUnavailable: the server is unreachable, timed out, or
                answered with an error status. Every failure of a local provider
                is retryable in the sense that matters: the operator can start
                it.

        No `BudgetExceeded` is reachable here, and that is not an oversight -
        local inference has no account to exhaust. It is also why `cost_usd` is
        a true zero rather than the absence `pricing.estimate_cost` warns about.
        """
        require_samples(n)
        model = self._models[tier]
        started = time.perf_counter()
        # `offset` is the sample index, which is what varies the seed per draw -
        # see `_one`. `draw_samples` supplies it and cancels the siblings when
        # one fails, which for a local server is queue time rather than money
        # but is the same orphaned-task rule from `RULES.md` §2.2.
        replies = await draw_samples(
            lambda index: self._one(
                model, prompt=prompt, schema=schema, temperature=temperature, offset=index
            ),
            n,
        )
        return self._response(model, replies, temperature=temperature, elapsed=elapsed_ms(started))

    # --- internals ----------------------------------------------------------

    async def _one(
        self,
        model: str,
        *,
        prompt: str,
        schema: type[BaseModel] | None,
        temperature: float,
        offset: int,
    ) -> dict[str, Any]:
        """One `/api/chat` request, seeded `BASE_SEED + offset`.

        Returns:
            The decoded body. Shape verified against Ollama 0.34.0:
            `message.content`, `prompt_eval_count`, `eval_count`,
            `prompt_eval_cached_count`, `model`, `done_reason`.

        Raises:
            ProviderUnavailable: transport failure, a non-2xx status, or a body
                that is not JSON.

        `stream: false`, so the body is one JSON object rather than a sequence
        of them. Streaming would be the right choice for a chat UI and is the
        wrong one here: the caller wants a complete document to validate against
        a schema, and reassembling one from chunks is work with no payoff.

        **`offset` is why the K samples differ, and leaving it out silently
        destroyed the measurement this project is built on.** The first version
        sent one fixed seed on every request in a draw. Ollama honours a seed
        exactly: same seed, same prompt, same output - *whatever the
        temperature*. Measured, at temperature 0.7 with K=3, the three samples
        came back character-identical.

        `MEMORY_ENGINE.md` §3.1 takes semantic entropy over those samples, so
        identical samples are one meaning cluster, so `H_norm` is 0, so §3.2's
        `w_H(1 - H_norm)` term scores **maximum confidence on every candidate
        forever**. Nothing raises; the number is in range and always the same.
        That is exactly the failure CHECKPOINT B's own list calls "a scorer that
        produces plausible numbers with no discriminative power ... invisible to
        unit tests".

        Seeding `BASE_SEED + offset` keeps both properties that were wanted:
        the draw is still reproducible as a whole (the same base gives the same
        K samples), and the samples within it are independent draws.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            # See `_one`'s docstring: the seed VARIES ACROSS THE DRAW. Both
            # live under `options`, Ollama's name for the sampler's parameters.
            "options": {"temperature": temperature, "seed": BASE_SEED + offset},
        }
        if schema is not None:
            payload["format"] = _grammar_safe(schema.model_json_schema())
        try:
            response = await self._client.post(_CHAT, json=payload, timeout=self._timeout_s)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailable(
                f"ollama {exc.response.status_code} for {model}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"ollama unreachable for {model} at {self._client.base_url}: {exc}. "
                "Is `ollama serve` running?"
            ) from exc
        except ValueError as exc:
            raise ProviderUnavailable(
                f"ollama returned a non-JSON body for {model}: {exc}"
            ) from exc
        return body

    def _response(
        self,
        model: str,
        replies: list[dict[str, Any]],
        *,
        temperature: float,
        elapsed: float,
    ) -> LLMResponse:
        """Fold `n` replies into one `LLMResponse`.

        Raises:
            ProviderUnavailable: a reply carried no content.

        `cache_hit` reads `prompt_eval_cached_count`, which Ollama reports as the
        number of prompt tokens it did not have to re-evaluate, and is `all`
        across the draws for the reason the Anthropic adapter gives: a flag that
        describes the whole call is only true if it was true of every request in
        it.
        """
        samples = [_content_of(model, reply) for reply in replies]
        tokens_in = sum(int(reply.get("prompt_eval_count", 0) or 0) for reply in replies)
        tokens_out = sum(int(reply.get("eval_count", 0) or 0) for reply in replies)
        return LLMResponse(
            samples=samples,
            # Read back from the reply, as the other two adapters do.
            model=str(replies[0].get("model", model)),
            temperature=temperature,
            seed=BASE_SEED,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_hit=all(
                int(reply.get("prompt_eval_cached_count", 0) or 0) > 0 for reply in replies
            ),
            latency_ms=elapsed,
            # True, not unknown: local inference has no per-token price. See
            # `pricing.estimate_cost`, which keeps those two zeros apart.
            cost_usd=estimate_cost(model, tokens_in=tokens_in, tokens_out=tokens_out, local=True),
        )


# The base of the per-draw seed sequence: sample `i` is seeded `BASE_SEED + i`.
# Fixed, so that a draw can be repeated from the audit record - `LLMResponse.seed`
# records this base, and the offsets are positional and therefore implied.
#
# **Not a single seed shared by every sample.** See `OllamaClient._one`: that was
# the first version, and it made every sample in a draw identical, which sets
# `MEMORY_ENGINE.md` §3.1's entropy to zero on every candidate.
BASE_SEED: Final = 0


def _content_of(model: str, reply: dict[str, Any]) -> str:
    """The assistant text of one reply.

    Raises:
        ProviderUnavailable: the reply carried no content - which for a local
            model usually means the context window truncated the prompt away.
            `LLMResponse.samples` requires at least one non-empty sample.
    """
    content = str(reply.get("message", {}).get("content", ""))
    if not content:
        raise ProviderUnavailable(
            f"ollama returned empty content for {model} "
            f"(done_reason={reply.get('done_reason')!r}); nothing to parse"
        )
    return content


def _reject_floating_tag(tier: Tier, model: str) -> None:
    """Refuse `:latest`, and say what the rest of the tags do not guarantee.

    Args:
        tier: For the message - which variable to fix.
        model: The configured id.

    Raises:
        ValueError: the tag is `latest`, or there is no tag at all (which Ollama
            resolves *to* `latest`).

    **This is a partial guard and says so.** `RULES.md` §3 wants pinned ids so a
    recorded model id means one thing forever. Ollama's fully pinned form is a
    digest (`llama3.1@sha256:...`); a tag like `llama3.1:8b` names a family and a
    size and can still be re-pulled to a different build. Refusing every tag
    would make the provider unusable for the case it exists to serve, so this
    refuses the tag that is *guaranteed* to move and leaves the rest - which
    means an audit record naming an Ollama tag is weaker evidence than one
    naming a Claude id. Worth knowing before comparing two runs.
    """
    tag = model.rpartition(":")[2] if ":" in model else _FLOATING
    if tag == _FLOATING:
        raise ValueError(
            f"the {tier.value} tier is configured as {model!r}, whose tag floats. "
            "RULES.md §3 forbids floating aliases: `:latest` moves on the next "
            "`ollama pull`, so every audit record naming it becomes ambiguous and "
            "every replay becomes a guess. Pin a tag (`llama3.1:8b`) or, better, a "
            "digest (`llama3.1@sha256:...`)."
        )


def _grammar_safe(schema: object) -> Any:
    """Drop the schema keywords Ollama's grammar compiler cannot take.

    Args:
        schema: A JSON Schema, or any fragment of one. Not mutated - pydantic
            caches `model_json_schema()` and hands back the same object every
            time, so editing it in place would corrupt the schema for every
            other caller in the process, including the validation this exists to
            preserve.

    Returns:
        A copy with every `maxLength`, `minLength`, `maxItems` and `minItems`
        of `_GRAMMAR_REPETITION_LIMIT` or more removed, at every depth.
        Everything else is carried through unchanged, including the same
        keywords at values the compiler accepts.

    **Why dropping is sound, and why clamping is not.** The grammar constrains
    *generation*; `RULES.md` §3 makes the caller validate the reply against the
    full schema, and it still does - `ExtractionBatch.model_validate_json` will
    reject a 2001-character `verbatim` whether or not the grammar could have
    prevented it. So the constraint is not lost, it moves from prevention to
    detection for the one field the compiler could not express.

    Clamping to 1999 instead was considered and is wrong. It would *forbid* a
    legitimate 2500-character value under a `maxLength: 5000` schema - the model
    could not produce it, and nothing would say why. Over-constraining silently
    is worse than under-constraining loudly.

    Recursive over lists as well as objects because these keywords live inside
    `$defs`, `items`, `anyOf` arms and `properties` - `ExtractedFact.verbatim`
    is two levels down a `$ref`, which is exactly why a top-level pass would
    have found nothing and looked like it worked.
    """
    if isinstance(schema, dict):
        return {
            key: _grammar_safe(value)
            for key, value in schema.items()
            if not (
                key in _REPETITION_KEYWORDS
                and isinstance(value, int)
                and value >= _GRAMMAR_REPETITION_LIMIT
            )
        }
    if isinstance(schema, list):
        return [_grammar_safe(item) for item in schema]
    return schema
