"""Argument reading and canary minting, shared by the served prompts.  S6.4

Separate from `prompts/__init__.py` for the import-cycle reason `resources/uris.py`
gives: the package `__init__` imports each builder so it can dispatch, so a
builder cannot import the `__init__` back.

**Every argument arrives as a string, and that is the protocol rather than a
simplification.** MCP's `prompts/get` carries `arguments` as `{str: str}`, so
`k` comes across as `"3"` and there is no type for a client to get wrong - which
moves the entire validation burden here. `tools/arguments.py` makes the same
point about the tool surface for the opposite reason: there the `inputSchema`
*looks* like it validates and does not.
"""

from __future__ import annotations

import secrets
from typing import Any, Final

from mcp_server.tools.context import ToolRefusedError

__all__ = ["CANARY_BYTES", "mint_canary", "optional", "required"]

# Matches `pipeline/l1_extract/extractor.py`'s `_CANARY_BYTES`, deliberately.
# The token a served prompt carries has to be the same shape as the one the
# pipeline uses, or a client that learns to strip it from one will not recognise
# it in the other. Sixteen hex characters is long enough that a model cannot
# emit it by chance and short enough not to eat the context it is protecting.
CANARY_BYTES: Final = 8


def mint_canary() -> str:
    """A fresh injection canary for one rendered prompt.

    Returns:
        A hex token, new on every call.

    Never cached, never derived from the arguments, never a constant. The canary
    works because content cannot predict it: a value reused across calls is one
    an attacker can learn from a single transcript and then instruct the model
    to avoid, which leaves the check passing while the injection succeeds.
    `secrets`, not `random`, for the same reason `extractor.py` uses it.
    """
    return secrets.token_hex(CANARY_BYTES)


def required(arguments: dict[str, str], name: str, prompt: str) -> str:
    """Read one required argument, or refuse by name.

    Args:
        arguments: What the client sent.
        name: The argument to read.
        prompt: The prompt asking, for the message.

    Returns:
        The value, guaranteed a non-blank string.

    Raises:
        ToolRefusedError: it is absent, not a string, or blank.

    The blank check is not pedantry. Arguments are strings by protocol, so a
    client with an empty form field sends `""` rather than omitting the key -
    and an empty `content` renders an extraction prompt asking a model to find
    facts in nothing, which it will obligingly do.
    """
    value: Any = arguments.get(name)
    if value is None:
        raise ToolRefusedError(
            f"{prompt} requires the {name!r} argument. See MCP_INTEGRATION.md §4."
        )
    if not isinstance(value, str) or not value.strip():
        raise ToolRefusedError(
            f"{prompt}'s {name!r} argument must be a non-empty string; got {value!r}."
        )
    return value


def optional(arguments: dict[str, str], name: str, prompt: str) -> str | None:
    """Read one optional argument, refusing a present-but-wrong value.

    Args:
        arguments: What the client sent.
        name: The argument to read.
        prompt: The prompt asking, for the message.

    Returns:
        The value, or `None` when it was omitted or sent blank.

    Raises:
        ToolRefusedError: it is present and not a string.

    Blank is treated as absent rather than as an error, which is the opposite of
    `required`'s reading of the same value. The asymmetry is about what the
    caller meant: an empty *optional* field is a client rendering a form nobody
    filled in, and refusing it would make an untouched field an error.
    """
    value: Any = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolRefusedError(
            f"{prompt}'s {name!r} argument must be a string; got {type(value).__name__}."
        )
    return value.strip() or None
