"""Which adapter a composition root builds.  S6.2, S9.1

Two roots need this and they need the same answer: `mcp_server/lifespan.py`,
which serves `memory.propose`, and `scripts/checkpoint_b_generate.py`, which
fills the gate's corpus. A second copy of the mapping would be free to drift on
which provider a blank key falls back to - and "it quietly ran on a different
model than you asked for" is the one thing that makes two AUROCs incomparable.

**A context manager, because two of the three providers need a transport built
and nothing else can build it.** `providers/__init__.py` says adapters are
"constructed nowhere in this package [...] every adapter takes an already-built
transport, because `RULES.md` §2.2 puts client creation in lifespan". The
adapters still do. This does not, and for eight steps it said it did: the
Anthropic and OpenAI SDK clients each own an `httpx.AsyncClient` internally, and
`build_llm` constructed one and returned a bare adapter, so nothing in the
process held a reference that could close it. The SDKs install a `__del__` that
closes on collection, which is why this never showed up as a symptom - it is
also non-deterministic, runs on whatever loop happens to be alive, and is the
same "one orphan per restart is invisible, a crash loop is not" argument
`mcp_server/lifespan.py` makes about the Postgres pool.

Yielding rather than returning is what puts that lifetime back where `RULES.md`
§2.2 wants it: the composition root writes `async with build_llm(...) as llm:`,
and the transport is closed on the way out whether the block ended cleanly or
not. The Ollama arm still builds nothing - it is handed the caller's
`httpx.AsyncClient` and must not close it, which is precisely why the decision
of *what to close* belongs to the thing that knows which arm it took.

Choosing *which adapter* from configuration is `ARCHITECTURE.md` §2.4's "the
backend is an operator decision" applied to models rather than to stores.

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

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anthropic
import openai

from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers.anthropic_client import AnthropicClient
from guardmem_core.llm.providers.common import tier_models
from guardmem_core.llm.providers.ollama_client import OllamaClient
from guardmem_core.llm.providers.openai_client import OpenAIClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.settings import Settings

__all__ = ["build_llm"]


@asynccontextmanager
async def build_llm(settings: Settings, http: httpx.AsyncClient) -> AsyncIterator[LLMClient]:
    """Open the adapter `settings.llm_provider` names, and close what it opened.

    Args:
        settings: The process configuration. `llm_provider` selects; the
            credential and model fields for that provider are read from it.
        http: A client whose `base_url` is the Ollama server. Required even when
            another provider is selected, because the caller owns its lifetime
            and opening one conditionally would put an `if` around an
            `async with` in every composition root. **Never closed here** - it
            arrives owned, and the Ollama arm is the one case where this manager
            has nothing of its own to release.

    Yields:
        The adapter, bound to the tier ladder and the timeout from `settings`.
        For the two SDK providers the transport underneath it is closed when the
        block exits; see the module docstring for why that is not the caller's
        job to remember.

    Raises:
        ValueError: the selected provider has no credential, or a tier's model
            id is blank (`tier_models`). Both are configuration errors and both
            say which environment variable to set - a `401` from a vendor four
            layers down says neither. Raised on entry, before anything is
            allocated, so a refused provider leaves nothing to clean up.

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
        # `tier_models` before the client, so a blank model id fails without
        # having opened a connection pool to complain from.
        models = tier_models(settings)
        async with anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        ) as sdk:
            yield AnthropicClient(sdk, models=models, timeout_s=settings.llm_timeout_s)
        return
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError(
                "GM_LLM_PROVIDER is 'openai' and GM_OPENAI_API_KEY is empty. "
                "Set the key, or set GM_LLM_PROVIDER=ollama."
            )
        models = tier_models(settings)
        async with openai.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) as sdk:
            yield OpenAIClient(sdk, models=models, timeout_s=settings.llm_timeout_s)
        return
    yield OllamaClient(
        http,
        models=dict.fromkeys(Tier, settings.ollama_model),
        timeout_s=settings.ollama_timeout_s,
    )
