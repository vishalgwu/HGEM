"""Reads over the hash-chained audit log, including chain verification.  BUILD_NOTEBOOK.md S11.x

Empty. The chain is already written - `observability/audit.py` owns it and
`replay_trace.py` reads it - and since S8.2 a request carries the authenticated
tenant that would scope a read, so what is missing is only the endpoints, which
are the step this module is titled with.

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

router: Final = APIRouter(prefix="/audit", tags=["audit"])
