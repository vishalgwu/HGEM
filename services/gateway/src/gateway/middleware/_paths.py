"""Which paths are exempt from which layer.  BUILD_NOTEBOOK.md S8.2

Two sets that hold the same four paths, kept separate on purpose.
`UNAUTHENTICATED` is about credentials and `UNBUDGETED` is about time. The day one
of these paths starts touching a datastore, whoever adds it should see two lists
and have to decide about each - rather than edit one name and silently change two
rules.

**Exact membership, never a prefix match.** A `startswith` would make
`/healthz-internal` unauthenticated too, and an auth bypass that comes from a prefix
test is the classic version of this bug.
"""

from __future__ import annotations

from typing import Final

__all__ = ["UNAUTHENTICATED", "UNBUDGETED"]

# The paths that carry no principal. Everything else is a 401 before it reaches a
# handler, which is why the rate limiter may assume a principal exists.
UNAUTHENTICATED: Final = frozenset({"/healthz", "/readyz", "/openapi.json", "/docs"})

# The paths that get no request budget. These four touch no datastore, so there is
# nothing for a budget to bound - and reading `Settings` off process state in order
# to bound nothing would make liveness depend on a populated environment.
# `/healthz` has to answer on a misconfigured process, which is when it matters
# most.
UNBUDGETED: Final = UNAUTHENTICATED
