"""`guardmem://audit/{trace_id}`.  MCP_INTEGRATION.md §3, BUILD_NOTEBOOK.md S6.4

§3: "full decision record". `README.md` puts it more strongly - "the audit log is
the product. If a decision isn't reconstructable, that's a bug of the same
severity as a wrong decision" - and this resource is the first surface where
that claim is something a person can check rather than take on trust.

**Every event for the trace, not only the decision.** ADR-0010's applier writes
`DECISION` for all four outcomes and adds `WRITE`, `SUPERSEDE` or `REVIEW` where
state changed, in one transaction. A resource that returned only the `DECISION`
would answer "what did it decide" and not "what did it do", and the second
question is the one an audit is for. The digests come too: `prev_digest` and
`digest` are what make the chain checkable, and omitting them would turn a
tamper-evident record into a list of claims.

**It reads the tenant's whole chain and filters in Python, which is honest for
now and will not be for a busy tenant.** `audit_store.read_chain` says the same
thing about itself. The reason is that the chain is verified as a chain - each
link's digest covers its predecessor - so a `WHERE trace_id = $1` returns rows
whose `prev_digest` points at links that were filtered out, and nothing in the
result can be checked against anything. Paging that honestly needs a verified
checkpoint to resume from, which needs somewhere to record it. Until then this
is bounded by how much one tenant has written, and `_MAX_EVENTS` bounds the
*answer* so a client is not handed a document it cannot render.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import mcp_types as types

from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.observability.audit_store import read_chain
from mcp_server.resources.uris import SCHEME, uri_for
from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from guardmem_core.schemas.receipt import AuditEvent
    from mcp_server.tools.context import ToolContext

__all__ = ["AUDIT_TEMPLATE", "read_audit"]

AUDIT_TEMPLATE: Final = types.ResourceTemplate(
    uri_template=f"{SCHEME}://audit/{{trace_id}}",
    name="Decision record",
    description=(
        "Every audit event for one proposal - what was decided, why, under "
        "which thresholds, and what state changed - with the hash-chain digests "
        "that make it tamper-evident. The trace_id comes from a memory.propose "
        "or memory.search result."
    ),
    mime_type="application/json",
)

# Ceiling on the events one resource returns. A proposal yields one DECISION per
# candidate plus its effects, so a normal trace is single digits; this exists
# for the pathological one rather than the ordinary one.
_MAX_EVENTS: Final = 200


async def read_audit(context: ToolContext, trace_id: str) -> types.TextResourceContents:
    """Return every audit event belonging to one trace.

    Args:
        context: Tenant, namespace and a bound store. The tenant is what scopes
            the chain; the namespace is unused, because a trace is not
            namespaced - one proposal may touch several.
        trace_id: From the URI, and from whatever result gave it to the caller.

    Returns:
        `application/json`: the trace id, the events oldest first, and whether
        the answer was truncated.

    Raises:
        ToolRefusedError: no event in this tenant's chain carries that trace.
            A refusal rather than an empty list, because the two questions a
            reader has - "was this decision recorded?" and "did I paste the
            right id?" - have opposite answers and an empty array answers
            neither. The message says which tenant was searched.
        StoreUnavailable: Postgres is unreachable.

    **Not scoped by namespace, and that is deliberate.** `context_for` resolves a
    namespace for every call because tools need one; a trace is minted per
    proposal and its events are keyed by tenant and trace alone. Filtering by
    namespace here would silently drop the events of a proposal that wrote
    across two, which is precisely the proposal whose audit someone is reading.
    """
    async with tenant_transaction(
        context.state.pool, context.tenant_id, timeout_s=context.state.settings.store_timeout_s
    ) as connection:
        chain = await read_chain(
            connection, context.tenant_id, timeout_s=context.state.settings.store_timeout_s
        )
    matched = [event for event in chain if event.trace_id == trace_id]
    if not matched:
        raise ToolRefusedError(
            f"no audit events for trace {trace_id!r} under tenant "
            f"{context.tenant_id}. Either that proposal was never governed by "
            "this server, or the id belongs to another tenant - a trace_id is "
            "only meaningful beside the tenant that produced it."
        )
    body = {
        "trace_id": trace_id,
        "tenant_id": context.tenant_id,
        "event_count": len(matched),
        "truncated": len(matched) > _MAX_EVENTS,
        "events": [_event_view(event) for event in matched[:_MAX_EVENTS]],
    }
    return types.TextResourceContents(
        uri=uri_for("audit", trace_id),
        mime_type="application/json",
        text=json.dumps(body, indent=2, default=str),
    )


def _event_view(event: AuditEvent) -> dict[str, Any]:
    """One link, rendered for a reader rather than for a verifier.

    Returns:
        The event with its payload inline.

    `seq`, `prev_digest` and `digest` are carried because they are the chain.
    A reader checking one decision does not need them; a reader asking whether
    the record has been altered needs nothing else, and `verify_chain` is the
    thing that answers that from exactly these three fields.

    The payload is emitted as the object it is rather than as a JSON string.
    `AuditEvent.payload` round-trips through `JSONB`, so re-encoding it into a
    string would hand a client something it has to parse twice and would make
    the resource's own `application/json` a lie about its interior.
    """
    return {
        "seq": event.seq,
        "kind": event.kind,
        "created_at": event.created_at.isoformat(),
        "prev_digest": event.prev_digest,
        "digest": event.digest,
        "payload": event.payload,
    }
