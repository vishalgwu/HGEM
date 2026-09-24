"""The write and read path.  BUILD_NOTEBOOK.md S8.2, S8.4

Propose a candidate, search, fetch an entity.

Empty at S8.1 and deliberately so. The endpoints here are the ones that need
a tenant, and a tenant needs S8.2's auth and tenancy middleware - an endpoint
that accepted a `tenant_id` from a request body before RLS was wired would be
a cross-tenant read with a plausible signature, which is the one bug this
service exists to make impossible.

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

router: Final = APIRouter(prefix="/memory", tags=["memory"])
