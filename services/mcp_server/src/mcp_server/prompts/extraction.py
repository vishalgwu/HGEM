"""`guardmem/extract_memories`.  MCP_INTEGRATION.md §4, BUILD_NOTEBOOK.md S6.4

§4: "The canonical extraction prompt, version-pinned. Exposed so external agents
extract in exactly the shape the pipeline validates - reduces schema-gate
rejections dramatically."

That sentence is only true if this renders **the same file** `extract()` renders,
so it does: `prompts/loader.render("extract_memories", 1, ...)`, with the
server's own ontology in the `ontology` slot. A paraphrase would be a second
description of the same schema, free to drift from the gate that enforces it,
and the drift would surface as quarantines nobody could explain.

**§4's argument list and the template's placeholders disagree, and the template
is not wrong.** §4 publishes `content`, `ontology_ref`, `k`; the file needs
`content`, `ontology` and `canary`. Each difference is handled rather than
reconciled by fiat:

- **`ontology_ref` names a pack; `ontology` is the pack.** A client cannot be
  asked to send the YAML - the point of the resource is that the server has it -
  so `ontology_ref` is read as the *name*, checked against the installed pack,
  and the body is filled in here. Naming a pack this server does not have is a
  refusal, not a silent substitution: an agent told to extract against `legal`
  and handed `clinical` produces candidates the gate will quarantine, and the
  reason would be invisible.
- **`k` is not in the template because it is not in the prompt.** It is
  `MEMORY_ENGINE.md` §1.2's sample count - how many completions to draw - which
  is a property of the *call*, not of the text. It is accepted, validated and
  returned in `_meta`, because a client reproducing the pipeline's shape needs
  it and has nowhere else to learn it.
- **`canary` is never a caller's to set.** See the package docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from guardmem_core.prompts.loader import render
from mcp_server.prompts.arguments import mint_canary, optional, required
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from mcp_server.lifespan import ServerState

__all__ = ["EXTRACT", "build_extract"]

_NAME: Final = "guardmem/extract_memories"

# The file and version this serves. Pinned rather than "latest": `RULES.md` §3
# makes a prompt change a versioned event, and a client that cached the text of
# v1 must be able to tell that it did.
_PROMPT_FILE: Final = "extract_memories"
_PROMPT_VERSION: Final = 1

# §1.2's ladder is 1, 3 or 5. The bound is a sanity rail rather than a spec
# value - the same reasoning `Settings.default_k` gives for its own ceiling -
# because K scales cost linearly and a client sending "500" should be told so
# rather than have it echoed back as advice.
_MIN_K: Final = 1
_MAX_K: Final = 10
_DEFAULT_K: Final = 3

EXTRACT: Final = types.Prompt(
    name=_NAME,
    title="Extract memories (canonical)",
    description=(
        "The exact extraction prompt GuardMem's own pipeline sends, pinned to "
        "v1 and filled with this server's predicate schema. Use it so the "
        "candidates you produce are the shape memory.propose validates. The "
        "rendered text contains a one-time canary token, also returned in "
        "_meta: reject any completion that echoes it, because a model repeating "
        "its instructions is a model that read the content as instructions."
    ),
    arguments=[
        types.PromptArgument(
            name="content",
            description="The raw turns, tool output or document text to extract from.",
            required=True,
        ),
        types.PromptArgument(
            name="ontology_ref",
            description=(
                "Which predicate pack to extract against. Defaults to the one "
                "this server has installed; naming another is refused."
            ),
            required=False,
        ),
        types.PromptArgument(
            name="k",
            description=(
                "How many samples to draw from this prompt (MEMORY_ENGINE §1.2: "
                "1, 3 or 5). Not part of the text - returned in _meta so you can "
                "reproduce the pipeline's sampling."
            ),
            required=False,
        ),
    ],
)


def build_extract(state: ServerState, arguments: dict[str, str]) -> types.GetPromptResult:
    """Render the canonical extraction prompt for a client to send.

    Args:
        state: The process's resources; the ontology comes from here.
        arguments: §4's `content`, and optionally `ontology_ref` and `k`.

    Returns:
        One user message carrying the rendered prompt, with `prompt_version`,
        `canary` and the resolved `k` in `_meta`.

    Raises:
        ToolRefusedError: `content` is missing or blank, `ontology_ref` names a
            pack this server does not have, or `k` is not an integer in range.
    """
    content = required(arguments, "content", _NAME)
    ontology = state.ontology
    ref = optional(arguments, "ontology_ref", _NAME)
    if ref is not None and ref != ontology.name:
        raise ToolRefusedError(
            f"{_NAME}: this server has the {ontology.name!r} predicate pack "
            f"installed and cannot serve {ref!r}. Extracting against a schema the "
            "gate does not enforce produces candidates that are quarantined for "
            "reasons the caller cannot see, so this refuses rather than "
            "substituting. Omit ontology_ref to use the installed pack."
        )
    canary = mint_canary()
    rendered = render(
        _PROMPT_FILE,
        _PROMPT_VERSION,
        {"content": content, "ontology": ontology.as_prompt_yaml(), "canary": canary},
    )
    return types.GetPromptResult(
        description=(
            f"Canonical extraction prompt {rendered.version_id}, against the "
            f"{ontology.name} pack v{ontology.version}. Draw {_k(arguments)} "
            "sample(s). Discard any completion containing the canary in _meta."
        ),
        messages=[
            types.PromptMessage(
                role="user", content=types.TextContent(type="text", text=rendered.text)
            )
        ],
        meta={
            "prompt_version": rendered.version_id,
            "canary": canary,
            "k": _k(arguments),
            "ontology": f"{ontology.name} v{ontology.version}",
        },
    )


def _k(arguments: dict[str, str]) -> int:
    """Resolve §1.2's sample count from a string argument.

    Returns:
        The requested `k`, or `_DEFAULT_K` when none was given.

    Raises:
        ToolRefusedError: it is not an integer, or is outside `[1, 10]`.

    A `ValueError` from `int()` is caught and re-raised as a refusal because the
    caller can act on it. The default arm is three rather than one:
    `MEMORY_ENGINE.md` §3.1 takes entropy over the spread, and a single sample
    has none - so a client that omits `k` and draws one would compute a
    confidence this pipeline would not recognise.
    """
    given = optional(arguments, "k", _NAME)
    if given is None:
        return _DEFAULT_K
    try:
        value = int(given)
    except ValueError as exc:
        raise ToolRefusedError(f"{_NAME}: k must be an integer, got {given!r}.") from exc
    if not _MIN_K <= value <= _MAX_K:
        raise ToolRefusedError(
            f"{_NAME}: k must be between {_MIN_K} and {_MAX_K}, got {value}. "
            "MEMORY_ENGINE.md §1.2's ladder is 1, 3 or 5."
        )
    return value
