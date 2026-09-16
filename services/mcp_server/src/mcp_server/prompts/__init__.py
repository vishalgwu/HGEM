"""The prompt surface: what a client may invoke, and what it must do with it.  S6.4

`MCP_INTEGRATION.md` §4 publishes four. Two are served and two decline, which is
the same split S6.2 made across the tools and for the same reason: the two that
decline have no implementation behind them, and a prompt that returned plausible
text for a review task that does not exist would be inventing the thing this
product exists to prevent people inventing.

| Prompt | S6.4 | Why |
|---|---|---|
| `guardmem/extract_memories` | served | `extract_memories/v1.md`, which the pipeline sends |
| `guardmem/adjudicate_conflict` | served | `adjudicate_conflict/v1.md`, likewise |
| `guardmem/review_brief` | declines | the review queue is **S18.1**; no task to render |
| `guardmem/memory_hygiene_report` | declines | drift is unmeasured; see `hygiene.py` |

**These are not `guardmem_core.prompts`, and the word doing the work is
"direction".** That package holds the versioned files this system sends *to* a
model. §4's prompts are what this server offers *to a client*, for a person to
invoke from a menu. The two overlap for exactly the two that are served, and
that overlap is the point of serving them: §4 says exposing the canonical
extraction prompt "reduces schema-gate rejections dramatically", which is only
true if it is the same file, byte for byte, that `extract()` renders.

**The canary is the sharp edge, and a served prompt blunts it.** Both real
templates carry a `{{canary}}` slot: the pipeline fills it with
`secrets.token_hex`, sends the prompt, and raises `InjectionDetected` if the
model's reply echoes it - content that talked the model into repeating its
instructions is content that has escaped being data. A client invoking the
prompt gets the text and runs its own completion, so **nobody checks the echo
unless the client does**. Every served prompt therefore mints a fresh canary,
states the obligation in its `description`, and returns the token in `_meta` so
the check is one string comparison rather than a parsing exercise. A fixed
canary would be worse than none: it would be guessable, and it would read as
protection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from mcp_server.prompts.adjudication import ADJUDICATE, build_adjudicate
from mcp_server.prompts.extraction import EXTRACT, build_extract
from mcp_server.prompts.hygiene import HYGIENE, build_hygiene
from mcp_server.prompts.review_brief import REVIEW_BRIEF, build_review_brief
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_server.lifespan import ServerState

__all__ = ["PROMPTS", "PROMPTS_BY_NAME", "get_prompt"]

# §4's four, in the order its table lists them.
PROMPTS: Final[tuple[types.Prompt, ...]] = (EXTRACT, ADJUDICATE, REVIEW_BRIEF, HYGIENE)

PROMPTS_BY_NAME: Final[dict[str, types.Prompt]] = {prompt.name: prompt for prompt in PROMPTS}

type _Builder = Callable[[ServerState, dict[str, str]], types.GetPromptResult]

# Keyed off `PROMPTS` by `test_every_published_prompt_has_a_builder`, so a
# prompt that lists in a client's menu and has no builder - which fails only
# when somebody picks it - is a test failure instead.
_BUILDERS: Final[dict[str, _Builder]] = {
    EXTRACT.name: build_extract,
    ADJUDICATE.name: build_adjudicate,
    REVIEW_BRIEF.name: build_review_brief,
    HYGIENE.name: build_hygiene,
}


def get_prompt(
    state: ServerState, name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    """Render one `prompts/get`.

    Args:
        state: The process's resources.
        name: The prompt the client asked for.
        arguments: Its arguments; `None` is treated as `{}`.

    Returns:
        The rendered messages, with the prompt's version and its canary in
        `_meta` where one applies.

    Raises:
        ToolRefusedError: the name is unknown, a required argument is missing,
            or the prompt is one of the two that decline. Raised rather than
            returned as text, and that is the opposite of the tool surface's
            choice - see the note below.

    **A refusal here is a protocol error, not a rendered message.** A tool's
    refusal is read by a model that can act on it, so `tools/__init__.py` returns
    `isError` text. A prompt is picked by a *person* from a menu and its result
    is inserted into their conversation - so a refusal rendered as a message
    would put "this prompt is not available" into the transcript **as if a model
    had said it**, and a later reader cannot tell that from content. Failing the
    request leaves the transcript clean and lets the client say so in its own
    UI.

    Synchronous, unlike `call_tool`. Nothing here touches a store: two prompts
    render files off disk through a cached loader and two decline. Declaring it
    `async` to match the tool surface would be symmetry bought with a false
    promise about what this does.
    """
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ToolRefusedError(
            f"unknown prompt {name!r}. This server publishes: " + ", ".join(sorted(_BUILDERS)) + "."
        )
    return builder(state, arguments or {})
