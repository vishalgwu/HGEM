"""Versioned prompt files and the loader that renders them.  S2.1

`RULES.md` §3 requires that prompts live in `prompts/<name>/v<N>.md` with
frontmatter and are never assembled inline. The `.md` files here are data; the
code is `loader.py`, which renders them, and `canary.py`, which mints the token
every rendered prompt carries and checks the reply for it.
"""

from guardmem_core.prompts.loader import PromptSpec, RenderedPrompt, render

__all__ = ["PromptSpec", "RenderedPrompt", "render"]
