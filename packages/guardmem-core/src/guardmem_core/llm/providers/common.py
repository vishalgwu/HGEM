"""What all three adapters share.  BUILD_NOTEBOOK.md S9.1

Three providers, one `LLMClient`. What differs between them is entirely the
transport - the request shape, the usage field names, the exception types. What
is the same is everything the *protocol* promises: which model a tier resolves
to, how many samples a caller may ask for, and how long a call took. That is
what lives here, so the three adapters differ only where the providers actually
do.

**`TierModels` is why `RULES.md` §3's "pinned ids, never floating aliases"
holds.** An adapter is handed a resolved mapping at construction and has no way
to reach a model id any other route: no default, no `getattr(settings, ...)`, no
string built from a tier name. A tier that was never configured raises at
startup rather than resolving to something plausible.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from guardmem_core.llm.base import Tier

if TYPE_CHECKING:
    from guardmem_core.settings import Settings

__all__ = ["MAX_SAMPLES", "TierModels", "elapsed_ms", "require_samples", "tier_models"]

type TierModels = Mapping[Tier, str]

# The ceiling on `n`. `MEMORY_ENGINE.md` §1.2's ladder is 1, 3 or 5 and
# `Settings.default_k` caps itself at 10, so nothing in this repository asks for
# more - but `LLMClient.complete` publishes `n: int` with no bound, and two of
# the three adapters satisfy `n` by issuing that many *concurrent requests*.
# `RULES.md` §2.2 forbids unbounded fan-out, and an unchecked `n` is exactly
# that with a provider's rate limit on the other end.
MAX_SAMPLES: Final = 10


def tier_models(settings: Settings) -> TierModels:
    """Resolve the three pinned ids a tier can select.

    Args:
        settings: The process configuration.

    Returns:
        Every `Tier` mapped to the id configured for it.

    Raises:
        ValueError: a tier's id is blank.

    Read once, at construction, and handed to the adapter - which is `RULES.md`
    §2.4's "settings come from a single object, **injected**" applied to the one
    thing an adapter would otherwise be tempted to look up per call.

    The blank check is not defensive noise. All three ids are *required* fields
    on `Settings`, so pydantic catches a missing one - but not an empty string,
    and an empty model id reaches the provider as a request for a model named
    `""`, whose error message is about the model rather than about the config.
    """
    resolved = {
        Tier.FAST: settings.model_fast,
        Tier.BALANCED: settings.model_balanced,
        Tier.FRONTIER: settings.model_frontier,
    }
    blank = sorted(tier.value for tier, model in resolved.items() if not model.strip())
    if blank:
        raise ValueError(
            f"no model id configured for tier(s) {blank}. Set GM_MODEL_FAST, "
            "GM_MODEL_BALANCED and GM_MODEL_FRONTIER to pinned ids - RULES.md §3 "
            "forbids floating aliases, so `claude-opus-5`, never `claude-latest`."
        )
    return resolved


def require_samples(n: int) -> int:
    """Check `n` against the fan-out ceiling.

    Args:
        n: How many samples the caller asked for.

    Returns:
        `n`, unchanged, when it is within bounds.

    Raises:
        ValueError: `n` is below one or above `MAX_SAMPLES`.

    A `ValueError` rather than a clamp. Silently returning three samples to a
    caller that asked for forty would corrupt `MEMORY_ENGINE.md` §3.1's entropy
    - `H_norm` is normalised by `log K`, so the divisor and the sample count
    would disagree and the score would be wrong rather than merely coarse.
    """
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    if n > MAX_SAMPLES:
        raise ValueError(
            f"n={n} exceeds the fan-out ceiling of {MAX_SAMPLES}. Two of the three "
            "adapters satisfy n by issuing that many concurrent requests, and "
            "RULES.md §2.2 forbids unbounded fan-out. MEMORY_ENGINE.md §1.2's "
            "ladder is 1, 3 or 5."
        )
    return n


def elapsed_ms(started: float) -> float:
    """Milliseconds since `started`, for `LLMResponse.latency_ms`.

    Args:
        started: A `time.perf_counter()` reading from before the call.

    Returns:
        The elapsed wall-clock, never negative.

    `perf_counter` rather than `time.time`: it is monotonic, so a clock
    adjustment mid-request cannot produce a negative latency in the audit
    record. `PRD.md` §6.1's SLO burn alerts read this number.
    """
    return max(0.0, (time.perf_counter() - started) * 1000.0)
