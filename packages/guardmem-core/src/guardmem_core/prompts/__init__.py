"""Versioned prompt files and the loader that renders them.  S2.1

`RULES.md` §3 requires that prompts live in `prompts/<name>/v<N>.md` with
frontmatter and are never assembled inline. The `.md` files here are data; the
only code is `loader.py`.
"""

from guardmem_core.prompts.loader import PromptSpec, RenderedPrompt, render

__all__ = ["PromptSpec", "RenderedPrompt", "render"]
