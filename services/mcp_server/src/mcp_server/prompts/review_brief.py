"""`guardmem/review_brief`.  MCP_INTEGRATION.md §4, BUILD_NOTEBOOK.md S6.4

§4: "Renders a flagged candidate as a plain-language brief for a human reviewer -
the same copy the dashboard uses, so a reviewer working in Claude Desktop sees
an identical framing."

**It declines, and both halves of that sentence are why.** There is no flagged
candidate to render: a `HITL_REVIEW` decision is audited and produces no task,
because the queue that would hold one is **S18.1** - ADR-0010 says so in as many
words, and `MCP_INTEGRATION.md` §2.2 already marks `review_task_id` as arriving
with it. And there is no dashboard, so there is no copy to be "the same" as; the
reviewer UI is S15.x. Writing the brief now would fix the framing before the
thing it frames exists, which is the spec following the code rather than the
other way round.

**It is listed anyway.** A prompt that is published and refuses by name is
discoverable - a reviewer sees it in the menu, picks it, and is told which build
step it waits on. A prompt that is simply absent teaches nothing, and `§4` is
the published contract either way. That is the same reasoning `memory.propose`
uses for declining rather than disappearing, and the opposite of the
`resources.subscribe` case: a refusal here has a symptom, and a subscription
that never notifies does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from mcp_server.prompts.arguments import required
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from mcp_server.lifespan import ServerState

__all__ = ["REVIEW_BRIEF", "build_review_brief"]

_NAME: Final = "guardmem/review_brief"

REVIEW_BRIEF: Final = types.Prompt(
    name=_NAME,
    title="Review brief (not available in this build)",
    description=(
        "Renders a flagged candidate as a plain-language brief for a human "
        "reviewer. NOT AVAILABLE: a HITL_REVIEW decision is audited but produces "
        "no review task in this build - the queue is S18.1 - so there is no task "
        "to render. This prompt refuses rather than inventing one."
    ),
    arguments=[
        types.PromptArgument(
            name="task_id",
            description="The review task to brief. No task exists in this build.",
            required=True,
        )
    ],
)


def build_review_brief(_state: ServerState, arguments: dict[str, str]) -> types.GetPromptResult:
    """Decline, naming the task asked for and the step that will provide it.

    Args:
        _state: Unused - nothing here reads process state, and that is the
            point: there is no store to read a task from.
        arguments: §4's `task_id`.

    Returns:
        Never.

    Raises:
        ToolRefusedError: always.

    **The argument is validated before the refusal**, which looks like wasted
    work and is not. A caller who sent no `task_id` has a different problem from
    one who sent a real id to a server that cannot use it, and telling the first
    "the review queue is S18.1" sends them looking for a build step when they
    left out a field. Refusing in the order the request is wrong keeps the two
    messages apart.
    """
    task_id = required(arguments, "task_id", _NAME)
    raise ToolRefusedError(
        f"{_NAME} cannot brief task {task_id!r}: this build has no review queue. "
        "A HITL_REVIEW decision is recorded in the audit chain and produces no "
        "task - MCP_INTEGRATION.md §2.2 marks review_task_id as arriving with "
        "S18.1, and ADR-0010 records the same. Read the decision itself at "
        "guardmem://audit/{trace_id} in the meantime; it carries the confidence, "
        "the risk and the reason codes a brief would be written from."
    )
