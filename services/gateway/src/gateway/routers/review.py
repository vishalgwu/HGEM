"""The HITL queue's REST surface.  BUILD_NOTEBOOK.md S18.x

Claim a task, submit a decision, release a lease.

Empty at S8.1. The queue itself does not exist until S18.1, and leases are
what make a claim safe under two reviewers - shipping a claim endpoint over a
table with no lease column would hand the same task to both.

The router is declared now rather than at the step that fills it, for the
reason the Makefile declares its later targets: the router list is S8.1's
own instruction and the documented shape of this service, and a module that
appears with its first endpoint makes that shape discoverable only by
reading a diff.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter

__all__ = ["router"]

router: Final = APIRouter(prefix="/review", tags=["review"])
