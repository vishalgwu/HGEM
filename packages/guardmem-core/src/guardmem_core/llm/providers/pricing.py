"""What a completion cost.  BUILD_NOTEBOOK.md S9.1, RULES.md §3

`RULES.md` §3: "Every LLM call records: model, prompt version, temperature, seed
(if supported), token counts, cache hit, latency, and **cost estimate**." The
first six come off the provider's own response; this is the seventh, and it is
the only one this repository has to supply itself.

**A static table, and it is stale the moment a vendor changes a price.** That is
not a reason to leave it out - a ledger with no numbers cannot enforce
`PRD.md` §6.5's $0.0009 blended envelope - but it is a reason to say where the
numbers came from and when. Each entry cites its source and date. S10.1 builds
the per-tenant ledger on top of this and is the step that should decide whether
the table stays here, moves to configuration, or is fetched.

**An unpriced model reports `0.0`, and `0.0` does not mean free.** Two different
things produce that number: a local Ollama model, where zero is the *correct*
marginal cost, and a model nobody has added a price for, where zero is an
absence. `estimate_cost` logs a warning naming the model in the second case and
never in the first, because a budget ledger that silently treats "unknown" as
"free" is a budget ledger that cannot be exceeded. **S10.1 must not read a zero
as a spend.**
"""

from __future__ import annotations

import logging
from typing import Final, NamedTuple

__all__ = ["LOCAL", "PRICES", "Price", "estimate_cost"]

_LOGGER: Final = logging.getLogger(__name__)

# Warn once per unknown model rather than once per call. A pipeline run makes
# several calls per candidate and a per-call warning would bury the rest of the
# log on the first misconfigured tier.
_WARNED: Final[set[str]] = set()


class Price(NamedTuple):
    """US dollars per million tokens, in and out.

    Attributes:
        usd_in: Input, per 1M tokens.
        usd_out: Output, per 1M tokens.

    Per *million* rather than per token, because that is the unit every vendor
    publishes. Converting at the source is one place to get the exponent wrong;
    converting at the point of use is one place, in `estimate_cost`.
    """

    usd_in: float
    usd_out: float


# Zero, and correctly zero: an Ollama model runs on hardware you already own, so
# the marginal cost of a completion really is nothing. Named rather than written
# as a bare `Price(0.0, 0.0)` so a reader can tell it from the absence that
# `estimate_cost` warns about.
LOCAL: Final = Price(0.0, 0.0)

# Anthropic list prices, first-party API, as published 2026-06-24. Only the
# three ids `.env.example` pins are listed - `GM_MODEL_FAST`, `_BALANCED` and
# `_FRONTIER` - because a price for a model this deployment cannot select is a
# row nobody will ever check and everybody will eventually trust.
#
# **No OpenAI chat prices, deliberately.** The OpenAI adapter exists as the
# cross-provider fallback `ARCHITECTURE.md` §4 requires, and no `GM_MODEL_*` is
# an OpenAI id today - so every price here would be one written from memory
# against a model nothing selects, which is how a wrong number gets into a
# ledger. Add them in the commit that first points a tier at OpenAI, from the
# vendor's page, with the date.
PRICES: Final[dict[str, Price]] = {
    # Anthropic, https://www.anthropic.com/pricing - read 2026-06-24.
    "claude-opus-5": Price(5.00, 25.00),
    "claude-sonnet-5": Price(2.00, 10.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
}


def estimate_cost(model: str, *, tokens_in: int, tokens_out: int, local: bool = False) -> float:
    """Estimate what one completion cost, in US dollars.

    Args:
        model: The pinned id that served the call, exactly as the provider
            reported it - not the tier, and not what was requested. A fallback
            can serve a FAST call from another model entirely
            (`ARCHITECTURE.md` §2.8) and the bill follows what ran.
        tokens_in: Prompt tokens billed.
        tokens_out: Completion tokens billed.
        local: True when the model runs on hardware the operator already owns,
            which makes zero the right answer rather than a missing one.

    Returns:
        The estimate, never negative. `0.0` for a local model, and `0.0` with a
        logged warning for a model with no price - see the module docstring on
        why those two are not the same thing.

    Deliberately not cached and deliberately not raising. A wrong price should
    show up as a number an operator can question in the ledger; a *raise* here
    would take down a governed write over an accounting detail, which inverts
    what matters.
    """
    if local:
        return 0.0
    price = PRICES.get(model)
    if price is None:
        if model not in _WARNED:
            _WARNED.add(model)
            _LOGGER.warning(
                "no price for model %r; reporting 0.0, which is an ABSENCE and not a "
                "cost. Add it to guardmem_core.llm.providers.pricing.PRICES with its "
                "source and date before trusting any spend total that includes it.",
                model,
            )
        return 0.0
    return (tokens_in * price.usd_in + tokens_out * price.usd_out) / 1_000_000
