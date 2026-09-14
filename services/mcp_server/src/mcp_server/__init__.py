"""GuardMem AI's MCP server.  BUILD_NOTEBOOK.md S6.1

`ADR-0005` makes MCP the primary agent surface rather than a wrapper over REST,
and `MCP_INTEGRATION.md` is its spec of record: the tool schemas in §2, the
resources in §3 and the prompts in §4. This package is the server that serves
them.

What ships at S6.1 is the skeleton: the process starts, speaks MCP over stdio,
advertises the capabilities it can honour, and lists **zero** tools. The four
core tools are S6.2, the resources and prompts are S6.4, and both are
deliberately absent rather than stubbed - `MCP_INTEGRATION.md`'s descriptions
are prompt engineering, and a tool advertised with the right name and the wrong
behaviour is worse for a model than no tool at all.

`server.py` owns the protocol surface and `lifespan.py` owns the process's
resources. They are separate because they fail differently: a handler bug is a
bad response and a lifespan bug is a process that will not start, and the second
is the one an operator reads at three in the morning.
"""

from mcp_server.lifespan import ServerState, lifespan
from mcp_server.server import build_server, main

__all__ = ["ServerState", "build_server", "lifespan", "main"]
