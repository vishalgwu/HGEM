"""Which adapter a composition root builds.  S6.2, S9.1

Two roots need this and they need the same answer: `mcp_server/lifespan.py`,
which serves `memory.propose`, and `scripts/checkpoint_b_generate.py`, which
fills the gate's corpus. A second copy of the mapping would be free to drift on
which provider a blank key falls back to - and "it quietly ran on a different
model than you asked for" is the one thing that makes two AUROCs incomparable.

**This does not violate the package rule above it.** `providers/__init__.py`
says adapters are "constructed nowhere in this package [...] every adapter takes
an already-built transport, because `RULES.md` §2.2 puts client creation in
lifespan". That still holds: this builds no transport. The caller owns the
`httpx.AsyncClient` and the SDK client's lifetime; this only decides *which
adapter* wraps what it is handed, from configuration, which is
`ARCHITECTURE.md` §2.4's "the backend is an operator decision" applied to
models rather than to stores.

**It refuses rather than falling back.** Asking for Anthropic with no key is a
misconfiguration, and the tempting kindness - quietly using the local model - is
the failure this module exists to prevent. `MEMORY_ENGINE.md` §3.1's entropy is
the model's own variance, so a number measured on `llama3.1:8b` and one measured
on Claude are different numbers; a silent substitution makes every audit record
and every AUROC a claim about a provider nobody chose.

**This is not a router.** S9.2 picks a provider *per tier* and S9.3 adds the
breaker and the cross-provider fallback `ARCHITECTURE.md` §4 requires. Until
then a process talks to one provider for every tier, which for Ollama means a
BALANCED judge call and a FAST extraction hit the same weights - §3.5's cost
ladder is not being exercised, and that is worth knowing before quoting a cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anthropic
import openai

from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers.anthropic_client import AnthropicClient
from guardmem_core.llm.providers.common import tier_models
from guardmem_core.llm.providers.ollama_client import OllamaClient
from guardmem_core.llm.providers.openai_client import OpenAIClient

if TYPE_CHECKING:
    import httpx

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.settings import Settings

__all__ = ["build_llm"]


def build_llm(settings: Settings, http: httpx.AsyncClient) -> LLMClient:
    """Build the adapter `settings.llm_provider` names.

    Args:
        settings: The process configuration. `llm_provider` selects; the
            credential and model fields for that provider are read from it.
        http: A client whose `base_url` is the Ollama server. Required even when
            another provider is selected, because the caller owns its lifetime
            and opening one conditionally would put an `if` around an
            `async with` in every composition root. Unused for the SDK
            providers, which build their own transports.

    Returns:
        The adapter, bound to the tier ladder and the timeout from `settings`.

    Raises:
        ValueError: the selected provider has no credential, or a tier's model
            id is blank (`tier_models`). Both are configuration errors and both
            say which environment variable to set - a `401` from a vendor four
            layers down says neither.

    **Ollama gets one model for all three tiers**, because `model_fast` and its
    siblings are Claude ids and a local run has one model. That collapses
    §3.5's ladder; see the module docstring.

    `llm_timeout_s` is the ceiling for the SDK providers. Ollama is given more:
    a local 7B model answering a cold prompt took over a minute on the machine
    this was written on, so the 20-second default tuned for a hosted API fires
    on a correct local setup. `OllamaClient`'s docstring records the
    measurement.
    """
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError(
                "GM_LLM_PROVIDER is 'anthropic' and GM_ANTHROPIC_API_KEY is empty. "
                "Set the key, or set GM_LLM_PROVIDER=ollama - this will not "
                "quietly run on a different model than the one configured."
            )
        return AnthropicClient(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            models=tier_models(settings),
            timeout_s=settings.llm_timeout_s,
        )
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "GM_LLM_PROVIDER is 'openai' and GM_OPENAI_API_KEY is empty. "
                "Set the key, or set GM_LLM_PROVIDER=ollama."
            )
        return OpenAIClient(
            openai.AsyncOpenAI(api_key=settings.openai_api_key),
            models=tier_models(settings),
            timeout_s=settings.llm_timeout_s,
        )
    return OllamaClient(
        http,
        models=dict.fromkeys(Tier, settings.ollama_model),
        timeout_s=settings.ollama_timeout_s,
    )
