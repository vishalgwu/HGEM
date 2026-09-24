"""Threshold and policy reads, and the audited writes that change them.  BUILD_NOTEBOOK.md S12.x

Empty at S8.1. `PRD.md` FR-3.3 makes a threshold change an audited event and
every `DecisionRecord` names the set that produced it, so a write here has to
emit an audit event and bump `thresholds_version` in one transaction. That is
S12.x's work, and a threshold endpoint without it would silently invalidate
every decision already recorded.

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

router: Final = APIRouter(prefix="/policy", tags=["policy"])
