"""`guardmem/adjudicate_conflict`.  MCP_INTEGRATION.md §4, BUILD_NOTEBOOK.md S6.4

§4: "Structured conflict reasoning: entailment, contradiction, temporal
ordering, recommended resolution." The file behind it is
`prompts/adjudicate_conflict/v1.md`, which is what `MEMORY_ENGINE.md` §2.3's
adjudication sends, and this serves that file rather than a description of it -
the argument is `extraction.py`'s and applies identically.

**§4 says `incumbent` and the template says `incumbents`, and the plural is
right.** §2.2 retrieves the top ten incumbents before deciding, so adjudication
reasons over a set. §4's singular describes the common case rather than the
shape. This accepts `incumbent` as published - a client that read §4 must work -
and renders it into the plural slot, so one incumbent is a set of one. A second
argument name would have made the published contract wrong instead of
incomplete.

**`ontology_ref` is accepted and checked, not sent.** §4 lists it and the
template has no slot for it: adjudication compares two claims that have already
passed the schema gate, so the vocabulary is settled by the time this prompt is
reached. It is validated against the installed pack anyway, because a caller
asking to adjudicate under `legal` while this server enforces `clinical` has a
misunderstanding worth surfacing at the point of the request rather than in the
recommendation it produces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from guardmem_core.prompts.loader import render
from mcp_server.prompts.arguments import mint_canary, optional, required
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from mcp_server.lifespan import ServerState

__all__ = ["ADJUDICATE", "build_adjudicate"]

_NAME: Final = "guardmem/adjudicate_conflict"

_PROMPT_FILE: Final = "adjudicate_conflict"
_PROMPT_VERSION: Final = 1

ADJUDICATE: Final = types.Prompt(
    name=_NAME,
    title="Adjudicate a conflict",
    description=(
        "The exact conflict-adjudication prompt GuardMem's pipeline sends, "
        "pinned to v1: entailment, contradiction, temporal ordering and a "
        "recommended resolution over a candidate and what is already believed. "
        "The rendered text contains a one-time canary token, also returned in "
        "_meta: reject any completion that echoes it."
    ),
    arguments=[
        types.PromptArgument(
            name="candidate",
            description="The new claim, as text or JSON.",
            required=True,
        ),
        types.PromptArgument(
            name="incumbent",
            description=(
                "What is already believed. One claim, or several - the template "
                "reasons over a set, because §2.2 retrieves the top ten."
            ),
            required=True,
        ),
        types.PromptArgument(
            name="ontology_ref",
            description=(
                "Which predicate pack the two claims belong to. Defaults to the "
                "installed pack; naming another is refused."
            ),
            required=False,
        ),
    ],
)


def build_adjudicate(state: ServerState, arguments: dict[str, str]) -> types.GetPromptResult:
    """Render the adjudication prompt for a client to send.

    Args:
        state: The process's resources; the ontology name is checked against it.
        arguments: §4's `candidate` and `incumbent`, optionally `ontology_ref`.

    Returns:
        One user message carrying the rendered prompt, with `prompt_version` and
        `canary` in `_meta`.

    Raises:
        ToolRefusedError: `candidate` or `incumbent` is missing or blank, or
            `ontology_ref` names a pack this server does not have.
    """
    candidate = required(arguments, "candidate", _NAME)
    incumbent = required(arguments, "incumbent", _NAME)
    ontology = state.ontology
    ref = optional(arguments, "ontology_ref", _NAME)
    if ref is not None and ref != ontology.name:
        raise ToolRefusedError(
            f"{_NAME}: this server enforces the {ontology.name!r} predicate pack "
            f"and cannot adjudicate under {ref!r}. Omit ontology_ref to use the "
            "installed pack."
        )
    canary = mint_canary()
    rendered = render(
        _PROMPT_FILE,
        _PROMPT_VERSION,
        {"candidate": candidate, "incumbents": incumbent, "canary": canary},
    )
    return types.GetPromptResult(
        description=(
            f"Conflict adjudication {rendered.version_id}, under the "
            f"{ontology.name} pack v{ontology.version}. Discard any completion "
            "containing the canary in _meta."
        ),
        messages=[
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=rendered.text)
            )
        ],
        meta={
            "prompt_version": rendered.version_id,
            "canary": canary,
            "ontology": f"{ontology.name} v{ontology.version}",
        },
    )
