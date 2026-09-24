"""Metric tiles, the extraction funnel, decision mix and latency panels.  BUILD_NOTEBOOK.md S13.x

Empty at S8.1. `PHASES_AND_ROADMAP.md` §3 requires funnel counts to reconcile
exactly with audit-log counts, so these endpoints are derived from the audit
log rather than from counters kept alongside it - two sources that can
disagree is the reconciliation failure that exit gate names.

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

router: Final = APIRouter(prefix="/telemetry", tags=["telemetry"])
