"""The four core tools, without a database.  BUILD_NOTEBOOK.md S6.2

The two tools that read governed memory, and the schemas all four publish.
`test_mcp_write_tools.py` holds `memory.propose`, `memory.commit`, tenancy and
dispatch - split when this module passed `RULES.md` §2.4's cap, along the line
the tools themselves fall on: two of them work today and two decline. The round
trip against real Postgres lives in `tests/integration/test_mcp_memory_tools.py`.

1. **The published schemas match `MCP_INTEGRATION.md`.** S6.2 says to copy §2.1
   to §2.4 "exactly, including the descriptions", and adds why: "the
   descriptions are prompt engineering, not documentation". A tool description
   is the only thing a model reads before deciding to call a tool, so a
   reworded one is a behavioural change to every agent using the server. These
   assert the fields a reader would have to diff two documents to check.
2. **Argument handling**, which is the bulk of the code and all of it reachable
   with a fake store.
3. **The refusals**, each of which stands for something that is not built -
   the pipeline, entity resolution, the worker queue. They are asserted so that
   wiring one up later fails a test rather than silently changing what an agent
   is told.
"""

from __future__ import annotations

from typing import Any

import pytest

from fixtures.assertions import WHEN, stored_assertion
from fixtures.mcp import SUBJECT, context, store_with
from mcp_server.tools import _HANDLERS, TOOLS, TOOLS_BY_NAME
from mcp_server.tools.context import ToolRefusedError
from mcp_server.tools.get_entity import run_get_entity
from mcp_server.tools.search import run_search


class TestTheSchemasAreTheDocument:
    """S6.2: copy §2.1-§2.4 exactly, including the descriptions."""

    def test_all_four_are_published(self) -> None:
        assert [tool.name for tool in TOOLS] == [
            "memory.search",
            "memory.propose",
            "memory.commit",
            "memory.get_entity",
        ], "the names and S6.2's implementation order"

    def test_every_published_tool_has_a_handler(self) -> None:
        """A tool in the schema module and not in the dispatch table lists in a
        client and then fails as "unknown tool" - a support ticket rather than a
        build failure. This is the check that makes it a build failure."""
        assert set(TOOLS_BY_NAME) == set(_HANDLERS)

    def test_search_description_is_the_one_a_model_reads(self) -> None:
        """§2.1's description verbatim. The last sentence is the behavioural
        half - it tells an agent *when* to call, which is the difference between
        a tool that is available and a tool that is used."""
        assert TOOLS_BY_NAME["memory.search"].description == (
            "Search governed long-term memory. Returns only currently-believed, "
            "non-tombstoned assertions with provenance. Use this before answering "
            "anything that depends on facts about this subject."
        )

    def test_propose_description_says_facts_are_not_immediately_stored(self) -> None:
        """The single most important sentence in §2.2 for an agent's behaviour:
        a fact that came back `hitl_review` must not be treated as established."""
        description = TOOLS_BY_NAME["memory.propose"].description or ""
        assert "NOT immediately stored" in description
        assert "source spans for provenance" in description

    def test_commit_description_says_it_is_not_a_bypass(self) -> None:
        description = TOOLS_BY_NAME["memory.commit"].description or ""
        assert "not a bypass" in description
        assert "no unsourced write path" in description

    @pytest.mark.parametrize(
        ("name", "required"),
        [
            ("memory.search", ["query"]),
            ("memory.propose", ["content"]),
            ("memory.commit", ["assertions"]),
            ("memory.get_entity", ["entity_id"]),
        ],
    )
    def test_required_properties_match_the_document(self, name: str, required: list[str]) -> None:
        assert TOOLS_BY_NAME[name].input_schema["required"] == required

    def test_search_publishes_the_documented_bounds(self) -> None:
        """§2.1's numbers, which callers read and this server has to honour."""
        properties = TOOLS_BY_NAME["memory.search"].input_schema["properties"]

        assert properties["min_confidence"]["default"] == 0.6
        assert properties["limit"]["default"] == 10
        assert properties["limit"]["maximum"] == 50
        assert properties["token_budget"]["default"] == 1500

    def test_propose_publishes_the_five_source_tiers(self) -> None:
        properties = TOOLS_BY_NAME["memory.propose"].input_schema["properties"]

        assert properties["source_tier"]["enum"] == [
            "trusted_system",
            "verified_user",
            "unverified_user",
            "tool_output",
            "retrieved_web",
        ]
        assert properties["source_tier"]["default"] == "unverified_user"

    def test_only_the_tool_that_declines_publishes_no_output_schema(self) -> None:
        """S6.2 pinned "no tool publishes one" and said why: §2.1-§2.4 publish
        example results rather than schemas, so writing one would invent a
        contract the spec of record does not state - and that **S7.3** owned the
        question.

        S7.3 answered it, so this is updated rather than deleted. Three tools
        publish a schema now, each read off the handler that produces it, which
        is what makes it a transcription rather than an invention.
        `memory.commit` still publishes none, for S6.2's reason unchanged: it
        declines, and a schema for a payload nothing produces is a contract
        nobody can check.

        The detail lives in `tests/contract/`, which owns the schemas
        themselves; what is pinned here is only which tools have one, because
        that is a fact about this module's `TOOLS`.
        """
        by_name = {tool.name: tool.output_schema for tool in TOOLS}

        assert by_name["memory.commit"] is None
        assert all(
            by_name[name] is not None
            for name in ("memory.search", "memory.propose", "memory.get_entity")
        )


class TestSearchReadsGovernedMemory:
    async def test_it_returns_believed_assertions_with_provenance(self) -> None:
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True)
        )

        result = await run_search(context(store), {"query": "allergy"})

        assert len(result["assertions"]) == 1
        citation = result["assertions"][0]["provenance"][0]
        assert citation["verbatim"], "S6.2's DONE WHEN: found with its provenance"
        assert citation["span"] == [13, 35]

    async def test_min_confidence_filters(self) -> None:
        store = await store_with(
            stored_assertion(obj="sure", confidence=0.95, visible=True),
            stored_assertion(obj="unsure", confidence=0.30, visible=True),
        )

        result = await run_search(context(store), {"query": "x", "min_confidence": 0.6})

        assert [a["object"] for a in result["assertions"]] == ["sure"]

    async def test_a_retired_assertion_is_excluded_rather_than_returned(self) -> None:
        """§2.1's whole reason for publishing `excluded`: "we have no record" and
        "we retired that record" must be distinguishable."""
        superseded = stored_assertion(
            predicate="home_address", obj="14 Ashfield Road", visible=True
        )
        successor = stored_assertion(predicate="home_address", obj="3 Calder Way", visible=True)
        store = await store_with(superseded, successor)
        await store.supersede(superseded.assertion_id, successor.assertion_id, WHEN)

        result = await run_search(context(store), {"query": "address"})

        assert [a["object"] for a in result["assertions"]] == ["3 Calder Way"]
        assert len(result["excluded"]) == 1
        assert result["excluded"][0]["reason"] == f"superseded_by {successor.assertion_id}"

    async def test_excluded_is_scoped_to_the_predicates_asked_about(self) -> None:
        """Found by driving the seeded demo tenant: an unscoped `excluded` told a
        search for `allergy` that two facts had been retired, and both were a
        `home_address` and a `preferred_pharmacy`. A footnote that is usually
        wrong is worse than no footnote."""
        retired_address = stored_assertion(predicate="home_address", obj="old", visible=True)
        successor = stored_assertion(predicate="home_address", obj="new", visible=True)
        allergy = stored_assertion(predicate="allergy", obj="latex", visible=True)
        store = await store_with(retired_address, successor, allergy)
        await store.supersede(retired_address.assertion_id, successor.assertion_id, WHEN)

        result = await run_search(context(store), {"query": "x", "predicates": ["allergy"]})

        assert [a["object"] for a in result["assertions"]] == ["latex"]
        assert result["excluded"] == [], "no allergy was retired"

    async def test_the_token_budget_trims_from_the_tail(self) -> None:
        store = await store_with(
            stored_assertion(obj="first", visible=True),
            stored_assertion(obj="second", visible=True),
        )

        generous = await run_search(context(store), {"query": "x"})
        stingy = await run_search(context(store), {"query": "x", "token_budget": 1})

        assert len(generous["assertions"]) == 2
        assert stingy["assertions"] == []
        assert stingy["tokens_used"] == 0

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "`query` is required"),
            ({"query": ""}, "`query` is required"),
            ({"query": "x", "limit": "10"}, "`limit` must be an integer"),
            ({"query": "x", "limit": 0}, "`limit` must be at least 1"),
            ({"query": "x", "min_confidence": 2}, r"`min_confidence` must be in"),
            ({"query": "x", "token_budget": 0}, "`token_budget` must be at least 1"),
            ({"query": "x", "as_of": "not-a-date"}, "not a valid ISO-8601"),
            ({"query": "x", "as_of": "2026-03-14T09:30:00"}, "must carry a timezone"),
            ({"query": "x", "predicates": "allergy"}, "must be an array of strings"),
        ],
    )
    async def test_bad_arguments_are_refused(self, arguments: dict[str, Any], message: str) -> None:
        """Every one of these reaches the handler: the SDK validates the
        envelope, not the property types, so a client sending `{"limit": "10"}`
        gets here. Coercing would hide the client's bug."""
        with pytest.raises(ToolRefusedError, match=message):
            await run_search(context(), arguments)

    async def test_a_limit_above_the_published_maximum_is_clamped(self) -> None:
        """§2.1 publishes `maximum: 50`, so a client asking for more has read the
        schema and means "as many as you will give me". Failing teaches nothing."""
        store = await store_with(stored_assertion(visible=True))

        result = await run_search(context(store), {"query": "x", "limit": 5000})

        assert len(result["assertions"]) == 1

    async def test_a_subject_that_is_a_name_is_refused_with_the_reason(self) -> None:
        """`subject` is an entity filter over a `uuid` column. Entity resolution
        is specified in no document, so a name cannot be resolved - and saying so
        beats an empty result or an asyncpg cast error."""
        with pytest.raises(ToolRefusedError, match="resolved entity id"):
            await run_search(context(), {"query": "x", "subject": "Joan Ellery"})


class TestGetEntityReturnsTheGraphView:
    async def test_assertions_are_grouped_by_predicate(self) -> None:
        store = await store_with(
            stored_assertion(subject=SUBJECT, predicate="allergy", obj="latex", visible=True),
            stored_assertion(subject=SUBJECT, predicate="allergy", obj="penicillin", visible=True),
            stored_assertion(subject=SUBJECT, predicate="blood_type", obj="O-", visible=True),
        )

        result = await run_get_entity(context(store), {"entity_id": SUBJECT})

        assert set(result["assertions"]) == {"allergy", "blood_type"}
        assert len(result["assertions"]["allergy"]) == 2

    async def test_it_says_when_the_neighbour_list_is_a_limitation(self) -> None:
        """`neighbors: []` from an in-process graph means "this process has no
        graph", not "this entity is isolated". S7.1's durable backend is what
        makes the field trustworthy, and until then `graph_backed` says so."""
        result = await run_get_entity(context(), {"entity_id": SUBJECT})

        assert result["neighbors"] == []
        assert result["graph_backed"] is False, (
            "set by the composition root, which is the only thing that knows which backend it bound"
        )

    async def test_a_durable_backend_is_reported_as_one(self) -> None:
        """The control. Without it the test above would pass against a field
        hard-coded to False, which is exactly what it would become if S7.1 wired
        Neo4j and forgot this flag."""
        result = await run_get_entity(context(None, graph_durable=True), {"entity_id": SUBJECT})

        assert result["graph_backed"] is True

    async def test_a_name_is_refused(self) -> None:
        with pytest.raises(ToolRefusedError, match="resolved entity id"):
            await run_get_entity(context(), {"entity_id": "Joan Ellery"})
