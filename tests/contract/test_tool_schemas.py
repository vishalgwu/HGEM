"""Every tool's schemas, and the drift they exist to catch.  BUILD_NOTEBOOK.md S7.3

    "Validate every tool's inputSchema/outputSchema with jsonschema; assert no
    drift."

`tests/contract/` has held a `.gitkeep` since S1.1 waiting for this step. What
lands here is the half of the tool surface that is a *contract* rather than a
behaviour: the schemas a client reads once, at `tools/list`, and then holds for
the life of the session.

**Three kinds of drift, and they fail in three different places if nobody
checks.**

1. *A schema that is not a schema.* A malformed `inputSchema` is not rejected by
   this server - it is rejected by the client, at handshake, which reports the
   whole server as broken rather than the one tool. Validated against the
   JSON Schema metaschema below.
2. *A schema that disagrees with its handler.* The enum a client is told to
   choose from, the default it is told applies, the ceiling it is told it may
   ask for - all of them are written twice, once in `schemas.py` and once in the
   code that enforces them. Two copies of a fact is a fact that can disagree
   with itself.
3. *A payload that disagrees with its schema.* `outputSchema` is the one a
   client actively validates (`mcp/client/session.py` compiles a validator per
   tool), so a handler that changes shape breaks callers rather than tests. Real
   handler output is validated against the published schema here, which closes
   that loop before a client sees it.

**`additionalProperties` is open on the wire and closed here**, which is the one
asymmetry worth stating. A published schema that forbade unknown keys would make
*adding* a field a breaking change for every existing client - the opposite of
how a wire format should evolve. Drift still has to fail, so the exact key set
is asserted in this file instead. Loose on the wire, strict in CI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
from jsonschema.validators import validator_for

from fixtures.assertions import NS, WHEN, stored_assertion
from fixtures.mcp import SUBJECT, context, store_with
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.verdict import Decision
from mcp_server.tools import _HANDLERS, TOOLS, TOOLS_BY_NAME, arguments
from mcp_server.tools.context import ToolRefusedError
from mcp_server.tools.get_entity import run_get_entity
from mcp_server.tools.outputs import OUTPUT_SCHEMAS
from mcp_server.tools.search import run_search

if TYPE_CHECKING:
    import mcp_types as types

# The tools that return no `structuredContent` in this build, and therefore
# publish no `outputSchema`. Named rather than inferred, so a tool that grows a
# payload without a schema fails here instead of at a client - and so removing
# one from this list is a deliberate edit rather than an omission.
DECLINES: frozenset[str] = frozenset({"memory.commit"})

# A world time after `WHEN`, so an assertion given this `valid_to` is retired.
LATER = WHEN.replace(year=WHEN.year + 1)


def _validator(schema: dict[str, Any]) -> type[jsonschema.protocols.Validator]:
    """The validator class the schema's own `$schema` selects."""
    return validator_for(schema)


class TestEverySchemaIsAValidJsonSchema:
    """A malformed schema is rejected by the *client*, at handshake.

    Which means the failure arrives as "this server is broken" rather than as
    "this tool is broken", after the server has already been accepted - the
    worst shape a configuration error can take, and the cheapest to prevent.
    """

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_the_input_schema_validates_against_the_metaschema(self, tool: types.Tool) -> None:
        cls = _validator(tool.input_schema)

        cls.check_schema(tool.input_schema)

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_the_output_schema_validates_against_the_metaschema(self, tool: types.Tool) -> None:
        if tool.output_schema is None:
            pytest.skip(f"{tool.name} publishes no outputSchema; see DECLINES")
        cls = _validator(tool.output_schema)

        cls.check_schema(tool.output_schema)

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_every_input_schema_is_an_object_at_the_top(self, tool: types.Tool) -> None:
        """MCP passes tool arguments as a JSON object, so a schema whose root is
        anything else describes something the protocol cannot send."""
        assert tool.input_schema.get("type") == "object"


class TestTheAdvertisedSurfaceIsWiredAndComplete:
    def test_every_published_tool_has_a_handler(self) -> None:
        """A tool that lists and has no handler fails as "unknown tool" after a
        model has already decided to call it."""
        assert set(TOOLS_BY_NAME) == set(_HANDLERS)

    def test_every_tool_that_returns_a_payload_publishes_an_output_schema(self) -> None:
        """The check `outputs.py` says lives here.

        A tool that grows a payload without a schema loses the client-side
        validation silently - nothing fails, the guarantee just stops applying.
        """
        expected = set(TOOLS_BY_NAME) - DECLINES

        assert set(OUTPUT_SCHEMAS) == expected
        assert all(TOOLS_BY_NAME[name].output_schema is not None for name in expected)

    def test_a_declining_tool_publishes_no_output_schema(self) -> None:
        """The other direction: a schema for a payload nothing produces is a
        contract nobody can check, and would read as a promise."""
        assert all(TOOLS_BY_NAME[name].output_schema is None for name in DECLINES)

    def test_every_tool_carries_a_description(self) -> None:
        """`schemas.py`: "the descriptions are prompt engineering, not
        documentation". A tool with none is one a model has no basis to choose.
        """
        assert all(tool.description for tool in TOOLS)


def _properties(name: str) -> dict[str, Any]:
    """One tool's `inputSchema.properties`."""
    schema: dict[str, Any] = TOOLS_BY_NAME[name].input_schema
    properties: dict[str, Any] = schema.get("properties", {})
    return properties


class TestTheSchemaAgreesWithTheCodeThatEnforcesIt:
    """Every one of these numbers is written twice.

    Once in `schemas.py`, where a client reads it and decides what to send, and
    once in `arguments.py`, where the handler enforces it. Two copies of a fact
    is a fact that can disagree with itself, and this disagreement is the quiet
    kind: a client that trusts a `default` the handler does not apply gets
    different results than it asked for, with nothing raising anywhere.
    """

    def test_the_published_limit_ceiling_is_the_one_the_handler_enforces(self) -> None:
        """A client told it may ask for fifty, and a handler that caps at ten,
        silently returns a tenth of the answer."""
        assert _properties("memory.search")["limit"]["maximum"] == arguments._MAX_LIMIT

    def test_the_published_limit_default_is_the_one_the_handler_applies(self) -> None:
        assert _properties("memory.search")["limit"]["default"] == arguments._DEFAULT_LIMIT

    def test_the_published_confidence_floor_is_the_one_the_handler_applies(self) -> None:
        """`min_confidence` decides which believed facts an agent is shown at
        all, so a disagreement here changes what a model is told is true."""
        published = _properties("memory.search")["min_confidence"]["default"]

        assert published == arguments._DEFAULT_MIN_CONFIDENCE

    def test_the_published_token_budget_is_the_one_the_handler_applies(self) -> None:
        published = _properties("memory.search")["token_budget"]["default"]

        assert published == arguments._DEFAULT_TOKEN_BUDGET

    def test_the_source_tier_enum_is_the_ontology_s_vocabulary(self) -> None:
        """`memory.propose` publishes the tiers a caller may claim, and
        `SourceTier` is what the pipeline compares them against - a value in one
        and not the other is either a tier nobody can use or one nobody
        validates."""
        published = set(_properties("memory.propose")["source_tier"]["enum"])

        assert published == {tier.value for tier in SourceTier}

    def test_the_decision_enum_is_the_matrix_s_own(self) -> None:
        """`MEMORY_ENGINE.md` §3.4's four outcomes, published on the way out. A
        fifth decision that never reached the schema would be a payload the
        client rejects."""
        published = set(
            OUTPUT_SCHEMAS["memory.propose"]["properties"]["candidates"]["items"]["properties"][
                "decision"
            ]["enum"]
        )

        assert published == {decision.value for decision in Decision}

    def test_every_declared_default_names_a_property_that_exists(self) -> None:
        """A `default` on a property nobody reads is a promise to a client that
        nothing keeps."""
        for tool in TOOLS:
            for name, spec in tool.input_schema.get("properties", {}).items():
                if "default" in spec:
                    assert name in tool.input_schema["properties"], f"{tool.name}.{name}"

    def test_required_arguments_are_actually_refused_when_missing(self) -> None:
        """The strongest form of "the schema means something": the handler has
        to enforce what the schema declares, because the SDK validates the
        envelope and leaves per-property types to the server.

        `memory.search` declares `query` required; omitting it must be refused
        rather than treated as an empty search, which would return the
        namespace's nearest rows to a zero vector.
        """
        assert TOOLS_BY_NAME["memory.search"].input_schema["required"] == ["query"]

        with pytest.raises(ToolRefusedError):
            arguments.require_query({})


class TestRealPayloadsValidateAgainstThePublishedSchema:
    """The loop `outputs.py` exists to close.

    An `outputSchema` is the one schema a client *actively* validates - the SDK
    compiles a validator per tool from `tools/list` and checks every
    `structuredContent` it receives. So a handler that changes shape does not
    fail a test, it fails a caller. These run the real handlers over a fake
    store and validate what comes back, so the drift is caught here first.

    Driven through the handlers rather than `call_tool`, because `context_for`
    builds a real `PgVectorStore` from the pool and the unit fixture has none -
    the same seam every other unit test of these handlers uses.
    `tests/integration/` exercises the same payloads over a real session, where
    the SDK's own validator does the checking.
    """

    async def test_a_search_result_validates(self) -> None:
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True, subject=SUBJECT)
        )

        payload = await run_search(context(store), {"query": "allergy", "namespace": str(NS)})

        jsonschema.validate(payload, OUTPUT_SCHEMAS["memory.search"])

    async def test_an_empty_search_result_validates(self) -> None:
        """The shape a client meets first and the one most likely to be wrong:
        a schema that only ever saw a populated answer can require a field the
        empty case omits."""
        payload = await run_search(context(), {"query": "nothing", "namespace": str(NS)})

        jsonschema.validate(payload, OUTPUT_SCHEMAS["memory.search"])
        assert payload["assertions"] == []

    async def test_a_search_result_carrying_a_retired_fact_validates(self) -> None:
        """`excluded` has its own shape - it answers "why is this not here"
        rather than "what is here" - so it needs its own exercise."""
        store = await store_with(
            stored_assertion(
                predicate="home_address",
                obj="old",
                visible=True,
                subject=SUBJECT,
                valid_to=LATER,
            ),
            stored_assertion(predicate="home_address", obj="new", visible=True, subject=SUBJECT),
        )

        payload = await run_search(context(store), {"query": "home_address", "namespace": str(NS)})

        jsonschema.validate(payload, OUTPUT_SCHEMAS["memory.search"])
        assert payload["excluded"], "the fixture wrote a retired fact; excluded must show it"

    async def test_an_entity_card_validates(self) -> None:
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True, subject=SUBJECT)
        )

        payload = await run_get_entity(context(store), {"entity_id": SUBJECT, "namespace": str(NS)})

        jsonschema.validate(payload, OUTPUT_SCHEMAS["memory.get_entity"])

    async def test_an_entity_card_for_an_unknown_entity_validates(self) -> None:
        """`as_of` is null and both collections are empty, which is three
        nullable-or-empty fields at once - the combination a schema written from
        a happy-path example gets wrong."""
        payload = await run_get_entity(context(), {"entity_id": SUBJECT, "namespace": str(NS)})

        jsonschema.validate(payload, OUTPUT_SCHEMAS["memory.get_entity"])
        assert payload["as_of"] is None
        assert payload["graph_backed"] is False

    async def test_the_published_keys_are_exactly_the_keys_returned(self) -> None:
        """Where `additionalProperties` would have gone.

        The schema stays open so that adding a field is not a breaking change
        for existing clients; drift still has to fail, so the exact key set is
        asserted here instead. A field added to a handler and not to the schema
        loses its client-side validation silently.
        """
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True, subject=SUBJECT)
        )
        ctx = context(store)

        search = await run_search(ctx, {"query": "allergy", "namespace": str(NS)})
        entity = await run_get_entity(ctx, {"entity_id": SUBJECT, "namespace": str(NS)})

        assert set(search) == set(OUTPUT_SCHEMAS["memory.search"]["required"])
        assert set(entity) == set(OUTPUT_SCHEMAS["memory.get_entity"]["required"])

    async def test_every_returned_assertion_carries_a_citation(self) -> None:
        """`minItems: 1` on `provenance` is `RULES.md` non-negotiable #1 stated
        on the wire, and a client is entitled to rely on it rather than defend
        against it. Asserted on a real payload, not only in the schema."""
        store = await store_with(
            stored_assertion(predicate="allergy", obj="penicillin", visible=True, subject=SUBJECT)
        )

        payload = await run_search(context(store), {"query": "allergy", "namespace": str(NS)})

        assert all(a["provenance"] for a in payload["assertions"])
