"""The LLM client contract.  BUILD_NOTEBOOK.md S1.7

`ARCHITECTURE.md` §2.2: the evaluator "runs identically in the Gateway (strict
mode), the Worker (async mode), and the eval harness - this is why it has no
framework dependencies. Pure-ish: side effects confined to injected `LLMClient`,
`VectorStore`, `GraphStore` protocols, which makes `replay_trace.py` possible
with recorded fixtures."

`RULES.md` §2.1 asks for Protocols rather than ABCs here: structural typing
keeps `guardmem-core` free of driver imports, so no provider SDK is ever a
dependency of the decision engine. A conforming client is one whose signatures
match - it does not inherit from anything and it does not register itself.

Deliberately **not** `@runtime_checkable`. That decorator makes `isinstance()`
work, but it only checks that the *names* exist - not the signatures, not the
types, not whether the methods are async. An `isinstance` gate that passes for
an object with a `complete` attribute of any shape is worse than no gate,
because it reads like a guarantee. Conformance is checked by `mypy --strict`,
which checks all of it; `tests/fixtures/fakes.py` carries the assertion.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from guardmem_core.schemas.base import GMModel

__all__ = ["LLMClient", "LLMResponse", "Tier"]


class Tier(StrEnum):
    """Which class of model serves a call.

    `ARCHITECTURE.md` §2.8 and `MEMORY_ENGINE.md` §3.5. The ladder is the cost
    story: FAST handles noise filtering and low-risk extraction, BALANCED runs
    conflict adjudication and NLI, FRONTIER is reached only on `ESCALATE` and is
    budgeted at ≤6% of candidates.

    The tier is a routing input, not a model id. `settings.model_fast` and its
    two siblings hold the pinned ids, so that `RULES.md` §3's "pinned ids, never
    floating aliases" survives - a tier picks which pin to use, it never names a
    model itself.
    """

    FAST = "fast"
    BALANCED = "balanced"
    FRONTIER = "frontier"


class LLMResponse(GMModel):
    """One completion, and everything `RULES.md` §3 requires be recorded of it.

    §3: "Every LLM call records: model, prompt version, temperature, seed (if
    supported), token counts, cache hit, latency, and cost estimate." All but
    one of those are here. **`prompt_version` is not, and that is not an
    omission**: the client is handed a rendered `prompt` string and has no way
    to know which versioned file under `prompts/` produced it. The caller
    selected that file, so the caller records it - `MemoryCandidate` and
    `ConfidenceReport` both carry a version field for exactly this reason. The
    §3 requirement is met jointly, and neither half can invent the other's data.

    Attributes:
        samples: The completions, in draw order, at least one. Where `n > 1`
            these are the K samples semantic entropy clusters over
            (`MEMORY_ENGINE.md` §3.1), and **sample 0 is canonical**: §1.2 draws
            it at temperature 0 and the rest at 0.7, so order is meaningful and
            a client must not sort or deduplicate them.
        model: The pinned id that served the call, e.g. `"claude-haiku-4-5"`.
            Recorded rather than derived from the tier, because a fallback
            (`ARCHITECTURE.md` §2.8) can serve a FAST call from another
            provider entirely and replay has to know which one did.
        temperature: What was actually used, or `None` where the provider has
            **no temperature parameter to use**. The second case is not
            hypothetical and S9.1 is where it landed: `anthropic` 1.4.0's
            `messages.create` has no `temperature`, `top_p` or `top_k` argument
            at all - sampling controls were removed on the current Claude models
            (Opus 5, Sonnet 5, and the 4.7/4.8 family), and the SDK reflects it.
            Recording the value a *caller asked for* on a request that never
            carried one would put a number in the audit record that no provider
            ever saw, which is the failure `RULES.md` §3 exists to prevent.

            The same reasoning as `seed` below, and deliberately the same
            shape - `None` is a statement about what could be controlled, and it
            has a consequence worth reading before trusting an entropy score:
            see `providers/anthropic_client.py` on what `MEMORY_ENGINE.md`
            §1.2's temperature-0.7 spread means on a model that has no
            temperature.
        seed: The seed, where the provider supports one. `None` means it does
            not - which is a statement about reproducibility, so it is recorded
            rather than defaulted to zero.
        tokens_in: Prompt tokens billed.
        tokens_out: Completion tokens billed.
        cache_hit: Whether a prompt cache or the semantic response cache served
            this. Feeds the ≥40% cache-hit assumption in `PRD.md` §6.5.
        latency_ms: Wall-clock for the call, for the SLO burn alerts in
            `PRD.md` §6.1.
        cost_usd: Estimated spend, for the per-tenant ledger and the $0.0009
            blended envelope in `PRD.md` §6.5.
    """

    samples: list[str] = Field(min_length=1)
    model: str
    temperature: float | None = Field(default=None, ge=0.0)
    seed: int | None
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cache_hit: bool
    latency_ms: float = Field(ge=0.0)
    cost_usd: float = Field(ge=0.0)


class LLMClient(Protocol):
    """A model provider, as the pipeline sees it.

    Implementations live in `llm/providers/` from S9.1 and are wrapped by the
    router, circuit breaker and budget ledger. Nothing in the pipeline imports
    one.
    """

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        """Draw `n` completions of `prompt` from the model serving `tier`.

        Args:
            prompt: The fully rendered prompt. Rendering happens at the call
                site from a versioned file - `RULES.md` §3 forbids f-string
                assembly in business logic, and the client is downstream of that
                rule rather than a party to it.
            schema: When given, the provider must constrain generation to it
                (structured output or tool-calling; `RULES.md` §3 forbids regex
                over free text) and every sample is JSON that validates against
                it. **Parsing stays with the caller**, deliberately: `GMModel`
                is `extra="forbid"`, so a hallucinated field must raise, and
                that `ValidationError` belongs where `trace_id` and
                `candidate_id` are in scope to attach to it.
            tier: Which class of model to route to. Keyword-only and
                undefaulted, so no call site can drift onto FRONTIER by
                omission - that is the expensive mistake this ladder exists to
                prevent.
            temperature: 0.0 for the canonical sample; `MEMORY_ENGINE.md` §1.2
                draws the other K-1 at 0.7. Lexical variance at temperature 0 is
                not uncertainty, so K samples all drawn at 0 would make the
                entropy term meaningless rather than merely noisy.
            n: How many samples to draw. `settings.default_k` is the default
                arm of the risk-hint ladder, not a value this protocol assumes.

        Returns:
            The samples plus the accounting `RULES.md` §3 requires.

        Raises:
            ProviderUnavailable: The provider is unreachable, timed out, or its
                circuit breaker is open. Retryable with jittered backoff.
            BudgetExceeded: The tenant's cap is reached. Not retryable - the cap
                does not clear because a caller asked again.

        Every implementation passes an explicit timeout to its transport
        (`settings.llm_timeout_s`); `RULES.md` §2.2 makes a call without one a
        CI failure. It is not a parameter here because it is configuration, not
        a per-call decision, and a protocol that let each call site pick its own
        timeout would make the SLO unenforceable.
        """
        ...
