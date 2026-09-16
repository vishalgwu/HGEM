"""The URI scheme every resource shares.  MCP_INTEGRATION.md §3, S6.4

One constant in its own module, for an import-cycle reason rather than a
taxonomic one: `resources/__init__.py` imports each reader so it can dispatch,
and every reader needs the scheme to build the `uri` it echoes back. Defining it
in the package `__init__` would make each reader import its own parent, which
Python resolves at import time as a partially-initialised module and reports as
an `ImportError` naming a symbol that plainly exists.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import quote

__all__ = ["SCHEME", "uri_for"]

# `MCP_INTEGRATION.md` §3's scheme, and the one clients match on to decide which
# server owns a URI. Not `guardmem-ai` or the distribution name: §3's table is
# the published contract and a client that constructed a URI from it must hit a
# handler here.
SCHEME: Final = "guardmem"


# What `quote` leaves alone when a namespace is put into a path segment. A colon
# is a legal path character and every documented namespace carries one
# (`patient:8812`, `org:acme`, `quarantine:<tenant>`), so encoding it would turn
# every URI in a client's picker into `patient%3A8812` for no gain. Everything
# else is encoded, which is what makes the round trip total.
_SAFE: Final = ":"


def uri_for(kind: str, argument: str) -> str:
    """Build a `guardmem://` URI, encoding the argument into one path segment.

    Args:
        kind: The authority - `memory`, `audit` or `ontology`.
        argument: A namespace or a trace id.

    Returns:
        The URI, with `argument` percent-encoded except for its colons.

    **`Namespace` is an unconstrained `NewType(str)`.** Nothing rejects a slash
    or a space in one, and both break the parse in `resources/__init__.py`: a
    slash splits into two segments and only the first is read, so
    `guardmem://memory/a/b` would silently answer about `a`. A space makes the
    URI invalid outright. Encoding here is what pairs with the `unquote` there,
    and the pair is what makes any namespace a client can hold a namespace it
    can attach.
    """
    return f"{SCHEME}://{kind}/{quote(argument, safe=_SAFE)}"
