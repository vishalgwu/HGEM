"""The dispatch around the four tools, and the tenancy it resolves first.  S6.2

Split from `test_mcp_write_tools.py` at `RULES.md` §2.4's cap, along a seam that
module had grown rather than chosen: what is here is about the tool *surface* -
`call_tool`'s routing, `_summarise`'s text, and `context_for`'s refusal to guess
a tenant - and none of it is about what `memory.propose` or `memory.commit`
decide.

The tenancy tests carry the most weight. `context_for` resolves the tenant from
configuration because no gateway authenticates a caller yet (S8.2), so a server
that guessed one would read another tenant's memory and answer confidently -
which is the isolation failure the whole product exists to prevent, arrived at
through a default rather than an attack.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from fixtures.assertions import NS, TENANT, WHEN, stored_assertion
from fixtures.mcp import context, settings, state, store_with
from mcp_server.tools import _HANDLERS, _summarise, call_tool
from mcp_server.tools.context import ToolRefusedError, context_for
from mcp_server.tools.search import run_search


class TestTenancyIsRefusedRatherThanGuessed:
    def test_an_unconfigured_tenant_is_refused(self) -> None:
        """No auth exists (S8.2), so the tenant is configuration. A default here
        would be a cross-tenant read that succeeds and returns the wrong rows."""
        with pytest.raises(ToolRefusedError, match="GM_MCP_TENANT_ID"):
            context_for(state(settings=settings()), {})

    def test_a_non_uuid_tenant_is_refused_at_the_edge(self) -> None:
        with pytest.raises(ToolRefusedError, match="not a UUID"):
            context_for(state(settings=settings(mcp_tenant_id="demo-clinic")), {})

    def test_a_call_may_name_its_own_namespace(self) -> None:
        resolved = context_for(state(), {"namespace": "patient:9001"})

        assert resolved.namespace == "patient:9001"

    def test_the_configured_namespace_is_the_default(self) -> None:
        """§2.1: "defaults to server-configured namespace"."""
        assert context_for(state(), {}).namespace == NS

    def test_no_namespace_and_no_default_is_refused(self) -> None:
        unset = settings(mcp_tenant_id=str(TENANT))

        with pytest.raises(ToolRefusedError, match="GM_MCP_DEFAULT_NAMESPACE"):
            context_for(state(settings=unset), {})


class TestDispatch:
    async def test_an_unknown_tool_is_a_result_not_an_exception(self) -> None:
        """A model that hallucinated a tool name can read the list of real ones
        and try again; a protocol error is for the client's developer."""
        result = await call_tool(state(), "memory.teleport", {})

        assert result.is_error
        assert "unknown tool" in result.content[0].text  # type: ignore[union-attr]

    async def test_a_refusal_carries_is_error_and_the_reason(self) -> None:
        result = await call_tool(state(), "memory.search", {})

        assert result.is_error
        assert "`query` is required" in result.content[0].text  # type: ignore[union-attr]

    async def test_an_unexpected_error_does_not_leak_its_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception string can carry a DSN, a row, or a span of untrusted
        source text. `RULES.md` §1.5 keeps all three out of anything a caller
        sees; the traceback goes to the log instead.

        It also must not surface as JSON-RPC "Invalid request parameters", which
        is what the SDK does with an uncaught handler exception - blaming the
        caller for a server bug. That is how the zero-vector `NaN` in
        `get_entity` hid for a debugging session.
        """
        secret = "postgresql://user:hunter2@host/db"  # pragma: allowlist secret

        async def explode(_context: object, _arguments: object) -> dict[str, Any]:
            raise RuntimeError(secret)

        monkeypatch.setitem(_HANDLERS, "memory.search", explode)

        result = await call_tool(state(), "memory.search", {"query": "x"})

        assert result.is_error
        text = result.content[0].text  # type: ignore[union-attr]
        assert "hunter2" not in text
        assert "internal error" in text


class TestTheSummaryAModelReads:
    async def test_it_reports_the_retired_count_because_that_changes_behaviour(self) -> None:
        superseded = stored_assertion(predicate="home_address", obj="old", visible=True)
        successor = stored_assertion(predicate="home_address", obj="new", visible=True)
        store = await store_with(superseded, successor)
        await store.supersede(superseded.assertion_id, successor.assertion_id, WHEN)

        payload = await run_search(context(store), {"query": "address"})

        assert "1 retired and excluded" in _summarise("memory.search", payload)

    async def test_a_point_in_time_query_recovers_a_retired_assertion(self) -> None:
        """`as_of` is the axis `ADR-0002` exists to make answerable, and the one
        that separates "superseded" from "deleted"."""
        before = datetime(2026, 1, 1, tzinfo=UTC)
        superseded = stored_assertion(
            predicate="home_address", obj="old", visible=True, valid_from=before
        )
        successor = stored_assertion(predicate="home_address", obj="new", visible=True)
        store = await store_with(superseded, successor)
        await store.supersede(superseded.assertion_id, successor.assertion_id, WHEN)

        result = await run_search(
            context(store), {"query": "address", "as_of": "2026-02-01T00:00:00+00:00"}
        )

        assert [a["object"] for a in result["assertions"]] == ["old"]
