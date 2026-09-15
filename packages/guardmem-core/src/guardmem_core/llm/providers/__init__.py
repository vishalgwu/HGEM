"""Three providers, one `LLMClient`.  BUILD_NOTEBOOK.md S9.1

`llm/base.py` declared the Protocol at S1.7 and nothing implemented it for eight
steps - which is why CHECKPOINT B is recorded as BLOCKED, why `memory.propose`
declines, and why nothing in this repository had ever made a real model call.
This package is the other end of that.

| Provider | Client | `n` | `temperature` | `seed` | Cost |
|---|---|---|---|---|---|
| Anthropic | official SDK | K requests | **absent from the API** | absent | metered |
| OpenAI | official SDK | native | sent | sent | metered |
| Ollama | `httpx` | K requests | sent | sent | genuinely zero |

The middle two columns are the reason `LLMResponse.temperature` and `.seed` are
both nullable: the providers do not agree about what a caller may control, and
the audit record has to say which one served a call rather than flatten them.
`providers/anthropic_client.py` carries the consequence for
`MEMORY_ENGINE.md` §3.1's entropy, and it is worth reading before any AUROC is
quoted.

**Constructed nowhere in this package.** Every adapter takes an already-built
transport, because `RULES.md` §2.2 puts client creation in lifespan - one per
provider, never per request. `common.tier_models` is the only thing that reads
`Settings`, and it is called once.

S9.2 routes between these, S9.3 adds the breaker and the cross-provider
fallback `ARCHITECTURE.md` §4 requires, and S10.1 turns `cost_usd` into a
per-tenant ledger. None of that is here: this package answers "can we call a
model at all", which until now was no.
"""

from guardmem_core.llm.providers.anthropic_client import AnthropicClient
from guardmem_core.llm.providers.common import MAX_SAMPLES, TierModels, tier_models
from guardmem_core.llm.providers.ollama_client import OllamaClient
from guardmem_core.llm.providers.openai_client import OpenAIClient
from guardmem_core.llm.providers.pricing import PRICES, estimate_cost

__all__ = [
    "MAX_SAMPLES",
    "PRICES",
    "AnthropicClient",
    "OllamaClient",
    "OpenAIClient",
    "TierModels",
    "estimate_cost",
    "tier_models",
]
