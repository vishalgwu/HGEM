"""GuardMem AI's MCP server.  BUILD_NOTEBOOK.md S6.1

`ADR-0005` makes MCP the primary agent surface rather than a wrapper over REST,
and `MCP_INTEGRATION.md` is its spec of record: the tool schemas in §2, the
resources in §3 and the prompts in §4. This package is the server that serves
them.

S6.1 shipped the process with **zero** tools - it started, spoke MCP over stdio
and advertised only what it could honour - because `MCP_INTEGRATION.md`'s
descriptions are prompt engineering, and a tool advertised with the right name
and the wrong behaviour is worse for a model than no tool at all. S6.2 added
the four core tools and S6.4 the resources and prompts, and each still declines
what it cannot do rather than being stubbed.

`server.py` owns the protocol surface and `lifespan.py` owns the process's
resources. They are separate because they fail differently: a handler bug is a
bad response and a lifespan bug is a process that will not start, and the second
is the one an operator reads at three in the morning.
"""

from mcp_server.lifespan import ConfigurationError, ServerState, lifespan, preflight
from mcp_server.server import build_server, main

__all__ = [
    "ConfigurationError",
    "ServerState",
    "build_server",
    "lifespan",
    "main",
    "preflight",
]
