"""The two tools that decline, and the dispatch around all four.  S6.2

Split from `test_mcp_tools.py` at `RULES.md` §2.4's 400-line cap, along the seam
the tool surface already has: `memory.search` and `memory.get_entity` read
governed memory and work; `memory.propose` and `memory.commit` validate
everything they can and then refuse, because the decision pipeline has no
entity resolver, no candidate classifier, and no entailment wiring in the
orchestrator.

**These are the most important tests in the S6.2 suite.** A `memory.propose`
that returned `auto_write` with a plausible confidence would be the single most
harmful thing this repository could ship - the product's claim is that a fact
was governed before it was believed - so what is asserted here is that it
declines, and that the refusal names what is missing rather than being a shrug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from fixtures.assertions import NS, TENANT, WHEN, stored_assertion
from fixtures.mcp import context, settings, state, store_with
from mcp_server.tools import _HANDLERS, _summarise, call_tool
from mcp_server.tools.context import ToolRefusedError, context_for
from mcp_server.tools.pipeline import MISSING_DEPENDENCIES, run_commit, run_propose
from mcp_server.tools.search import run_search


class TestProposeAndCommitRefuseRatherThanInvent:
    """The most important tests in this module.

    A `memory.propose` that returned `auto_write` with a plausible confidence
    would be the single most harmful thing this repository could ship: the
    product's claim is that a fact was governed before it was believed. These
    assert that it declines, and that the message names what is missing.
    """

    async def test_propose_names_every_missing_dependency(self) -> None:
        """Walks `MISSING_DEPENDENCIES` rather than listing its entries.

        The list changes as the gaps close - S9.1 removed `LLMClient` from it -
        and a test that spelled the entries out failed on that commit for the
        wrong reason, asserting the shape of a sentence rather than the property
        that every gap is named. `tests/unit/test_errors.py` walks the exception
        hierarchy for the same reason.
        """
        with pytest.raises(ToolRefusedError) as caught:
            await run_propose(context(), {"content": "The patient prefers CVS.", "mode": "strict"})

        message = str(caught.value)
        assert MISSING_DEPENDENCIES, "a refusal that names nothing is a shrug"
        for missing in MISSING_DEPENDENCIES:
            assert missing in message

    async def test_propose_validates_arguments_before_refusing(self) -> None:
        """A caller whose call was *also* malformed should learn that too, and
        when the pipeline is wired these checks are already the right ones."""
        with pytest.raises(ToolRefusedError, match="`source_tier` must be one of"):
            await run_propose(context(), {"content": "x", "source_tier": "gossip"})

    async def test_async_mode_is_refused_because_nothing_would_decide(self) -> None:
        """§2.2's *default* mode, refused: it means "return now, decide later",
        and the worker that decides later is S8.4."""
        with pytest.raises(ToolRefusedError, match=r"S8\.4"):
            await run_propose(context(), {"content": "x", "mode": "async"})

    async def test_commit_refuses_an_unsourced_assertion_before_anything_else(self) -> None:
        """§2.3: "there is no unsourced write path in this API". That rule needs
        no pipeline, so it is enforced with no pipeline - the caller is told the
        real objection rather than a missing dependency."""
        with pytest.raises(ToolRefusedError, match="no unsourced write path"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": [],
                        }
                    ]
                },
            )

    async def test_commit_refuses_provenance_that_cannot_be_quoted(self) -> None:
        with pytest.raises(ToolRefusedError, match="verbatim"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": [{"source_hash": "sha256:abc"}],
                        }
                    ]
                },
            )

    async def test_commit_requires_the_four_documented_keys(self) -> None:
        with pytest.raises(ToolRefusedError, match="missing"):
            await run_commit(context(), {"assertions": [{"subject": "s"}]})


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


class TestEveryPublishedConstraintIsEnforced:
    """One test per property §2.2 and §2.3 publish.

    Each of these is a promise the `inputSchema` makes to a caller, and the SDK
    keeps none of them: it validates that `arguments` is an object and leaves
    the property types to the server. So a published `enum` that nothing checks
    is a lie, and these are what make it true.
    """

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "`content` is required"),
            ({"content": "   "}, "`content` is required"),
            ({"content": 42}, "`content` is required"),
            ({"content": "x", "risk_hint": "extreme"}, "`risk_hint` must be one of"),
            ({"content": "x", "mode": "eventually"}, "`mode` must be one of"),
            ({"content": "x", "mode": "strict", "hints": []}, "`hints` must be an object"),
            (
                {"content": "x", "mode": "strict", "hints": {"subject": 7}},
                "`hints.subject` must be a string",
            ),
            (
                {"content": "x", "mode": "strict", "hints": {"predicates_of_interest": "allergy"}},
                "predicates_of_interest",
            ),
            (
                {"content": "x", "mode": "strict", "idempotency_key": 1},
                "`idempotency_key` must be a string",
            ),
        ],
    )
    async def test_propose_refuses_bad_arguments(
        self, arguments: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ToolRefusedError, match=message):
            await run_propose(context(), arguments)

    async def test_propose_accepts_every_published_source_tier(self) -> None:
        """All five reach the pipeline gate rather than being refused on the
        way - a tier the document publishes and the server rejects would be a
        schema that lies."""
        for tier in (
            "trusted_system",
            "verified_user",
            "unverified_user",
            "tool_output",
            "retrieved_web",
        ):
            with pytest.raises(ToolRefusedError, match="pipeline is not wired"):
                await run_propose(
                    context(), {"content": "x", "mode": "strict", "source_tier": tier}
                )

    async def test_propose_accepts_every_published_risk_hint(self) -> None:
        for hint in ("low", "default", "high"):
            with pytest.raises(ToolRefusedError, match="pipeline is not wired"):
                await run_propose(context(), {"content": "x", "mode": "strict", "risk_hint": hint})

    async def test_well_formed_hints_are_accepted(self) -> None:
        """`hints.subject` is the one route by which a proposal could be governed
        before an `EntityResolver` is specified, so it has to survive."""
        with pytest.raises(ToolRefusedError, match="pipeline is not wired"):
            await run_propose(
                context(),
                {
                    "content": "x",
                    "mode": "strict",
                    "hints": {"subject": "e-1", "predicates_of_interest": ["allergy"]},
                    "idempotency_key": "idem-1",
                },
            )

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "`assertions` is required"),
            ({"assertions": []}, "`assertions` is required"),
            ({"assertions": "not-an-array"}, "`assertions` is required"),
            ({"assertions": ["not-an-object"]}, "must be an object"),
        ],
    )
    async def test_commit_refuses_bad_assertions(
        self, arguments: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ToolRefusedError, match=message):
            await run_commit(context(), arguments)

    async def test_commit_accepts_a_single_provenance_object(self) -> None:
        """§2.3's item schema names `provenance` and does not say it is a list.
        A caller sending one object rather than a list of one is not making a
        mistake, and refusing it would be this server inventing a constraint the
        document does not state."""
        with pytest.raises(ToolRefusedError, match="pipeline is not wired"):
            await run_commit(
                context(),
                {
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": {"verbatim": "allergic to latex"},
                        }
                    ]
                },
            )
