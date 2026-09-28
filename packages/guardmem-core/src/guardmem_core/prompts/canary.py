"""The canary a rendered prompt carries, and the check on the reply.  RULES.md §3

§3: content inside the delimiters is data, never instructions, and canary tokens
are inserted and checked on every model call. A prompt carries a fresh random
token it tells the model never to repeat; a reply containing it came from a
model that read the content as instructions - a confirmed injection rather than
a heuristic, which is why `InjectionDetected` quarantines instead of retrying.

**One module, because the five copies drifted.** The extractor, the noise
classifier, the conflict judge, the entailer and the MCP server's served prompts
each minted and checked their own. Three used 8 bytes and two used 16, while the
comments on the two said "same length as the extractor's".

**8 bytes - sixteen hex characters.** Long enough that a model cannot emit them
by chance, short enough not to eat the context they protect. The MCP server's
served prompts must carry the same shape as the pipeline's, or a client that
learns to strip one will not recognise the other; one constant makes that
structural rather than a comment's promise.

**`secrets`, and fresh on every call.** Never cached, never derived from the
content, never a constant. A token content could predict, or one reused across
calls, is one an attacker can learn from a single transcript and instruct the
model to avoid - which leaves the check passing while the injection succeeds.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final

from guardmem_core.errors import InjectionDetected

if TYPE_CHECKING:
    from collections.abc import Iterable

    from guardmem_core.types import TraceId

__all__ = ["CANARY_BYTES", "mint_canary", "reject_echo"]

CANARY_BYTES: Final = 8


def mint_canary() -> str:
    """A fresh canary for one rendered prompt.

    Returns:
        `CANARY_BYTES` random bytes as hex, new on every call.
    """
    return secrets.token_hex(CANARY_BYTES)


def reject_echo(canary: str, samples: Iterable[str], *, stage: str, trace_id: TraceId) -> None:
    """Refuse a reply in which any sample repeats the canary.

    Args:
        canary: The token the prompt carried.
        samples: Every sample the model returned - all of them, since one
            echoing sample is the injection whichever sample would be used.
        stage: What was asking, for the message: "extraction", "noise
            classifier" and so on.
        trace_id: For the error.

    Raises:
        InjectionDetected: `"<stage> echoed the canary token"`.
    """
    if any(canary in sample for sample in samples):
        raise InjectionDetected(f"{stage} echoed the canary token", trace_id=trace_id)
