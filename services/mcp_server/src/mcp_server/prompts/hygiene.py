"""`guardmem/memory_hygiene_report`.  MCP_INTEGRATION.md §4, BUILD_NOTEBOOK.md S6.4

§4: "Narrated summary of drift, stale facts, and contradiction pressure for a
namespace."

**It declines, and the reason is that two of those three words name
measurements this system does not take.**

- *Drift* is a comparison across time - what the distribution of decisions looked
  like last week against this one. Nothing samples it. `threshold_tuner.py`
  (S20.2) is the first thing that refits from outcomes, and drift detection is
  the instrument it needs.
- *Contradiction pressure* is a rate: how often candidates arrive that conflict
  with what is believed. `conflict.py` computes the signal per candidate and
  nothing aggregates it, because there has been nothing to aggregate over - the
  audit chain got its first governed row on 2026-09-16.
- *Stale facts* is the one that is nearly computable today: `valid_from` is on
  every assertion. "Nearly" is doing real work in that sentence, because staleness
  is per-predicate - a `medication` untouched for a year is a finding and a
  `date_of_birth` untouched for a year is not - and the ontology carries no decay
  field to distinguish them. `MEMORY_ENGINE.md` §4's decay tiers are what supply
  it.

**Two out of three is not a report, it is a number with a narrative attached.**
This prompt's whole value is that a reader trusts what it says, and a hygiene
report that silently omits drift because drift is unmeasured would be read as
"no drift". That is the failure this repository spends its comments on: an
absence that renders identically to a negative finding. So it refuses and says
which of the three it is missing, which is information a reader can act on.

Listed rather than hidden, for the reason `review_brief.py` gives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from mcp_server.prompts.arguments import optional, required
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from mcp_server.lifespan import ServerState

__all__ = ["HYGIENE", "build_hygiene"]

_NAME: Final = "guardmem/memory_hygiene_report"

HYGIENE: Final = types.Prompt(
    name=_NAME,
    title="Memory hygiene report (not available in this build)",
    description=(
        "Narrated summary of drift, stale facts and contradiction pressure for a "
        "namespace. NOT AVAILABLE: drift and contradiction pressure are not "
        "measured in this build, and staleness needs the decay tiers from "
        "MEMORY_ENGINE §4. A report missing two of its three findings reads as "
        "though it found nothing, so this refuses. Use "
        "guardmem://memory/{namespace} for the believed state."
    ),
    arguments=[
        types.PromptArgument(
            name="namespace",
            description="The namespace to report on.",
            required=True,
        ),
        types.PromptArgument(
            name="window",
            description="The period to report over, e.g. `30d`. Unused in this build.",
            required=False,
        ),
    ],
)


def build_hygiene(_state: ServerState, arguments: dict[str, str]) -> types.GetPromptResult:
    """Decline, naming which of the three findings are unmeasured.

    Args:
        _state: Unused; there is nothing to read.
        arguments: §4's `namespace`, optionally `window`.

    Returns:
        Never.

    Raises:
        ToolRefusedError: always.

    `window` is read and discarded rather than ignored: a caller who passed one
    should be told it was understood and that the build cannot honour it,
    because "my window was wrong" and "this feature does not exist" send a
    person to different places.
    """
    namespace = required(arguments, "namespace", _NAME)
    window = optional(arguments, "window", _NAME)
    over = f" over {window}" if window else ""
    raise ToolRefusedError(
        f"{_NAME} cannot report on {namespace!r}{over}: this build measures none "
        "of drift or contradiction pressure, and staleness needs the per-predicate "
        "decay tiers in MEMORY_ENGINE.md §4 to tell a year-old medication from a "
        "year-old date of birth. A report that quietly dropped the two it cannot "
        "compute would read as a clean bill of health. Attach "
        f"guardmem://memory/{namespace} for the believed state, including what "
        "has been retired."
    )
