"""The published schemas, enforced by the protocol itself.  BUILD_NOTEBOOK.md S7.3

`test_tool_schemas.py` validates handler output against `OUTPUT_SCHEMAS` with
`jsonschema` directly, which proves the schema and the handler agree. This
module proves something stronger and different: that the schema *reaches a
client* and is applied there.

**The assertion is that nothing raises, and that is not a weak test.**
`ClientSession.call_tool` fetches `tools/list`, compiles one validator per
declared `outputSchema`, and checks every successful result against it - so a
payload that does not conform raises `RuntimeError: Invalid structured content
returned by tool ...` inside the SDK, before a caller ever sees the data. Each
call below is therefore the SDK's own validator run against a real payload from
a real Postgres. A schema that were wrong, or a handler that drifted from one,
fails here as a protocol error rather than as an assertion.

**And a tool that declares a schema and returns nothing also fails.** The SDK
raises "has an output schema but did not return structured content", which is
why `memory.commit` must not declare one while it declines - a refusal carries
`isError` and is exempt, but a *successful* empty result is not.

**Moved here from `tests/contract/` on 2026-09-26, and the original placement was
reasoned rather than careless.** S7.3 put it there because what it checks is the
published contract rather than the behaviour behind it, and noted that it would
"skip without Docker like the integration suite".

That held while `tests/contract/` ran in no CI job at all. S8.1 added the
directory to `make test` - the `gates` job - on the stated grounds that it "needs
no container", which was false of this module and was verified on a machine where
Docker happened to be running. In `gates` the skip does not fire either, because
GitHub runners *do* have Docker: the fixture starts a Postgres, `alembic upgrade
head` builds `Settings`, and `gates` has no `.env`, so five tests error on eight
missing required fields.

The directory a test lives in is how this repository says what infrastructure it
needs - `make test` is the Docker-free suite and `make test-integration` is not -
so a module that starts a container belongs here whatever it is asserting about.
`test_tool_schemas.py` stays in `tests/contract/`: it validates schemas against
`jsonschema` with unit fixtures and needs nothing.
"""

from __future__ import annotations

import pytest

from fixtures.mcp_session import call, connected
from mcp_server.tools.outputs import OUTPUT_SCHEMAS


@pytest.mark.usefixtures("demo_server_env")
class TestTheClientValidatesEveryPayloadItIsSent:
    """One call per tool that publishes an `outputSchema`."""

    async def test_a_search_result_passes_the_client_s_validator(
        self, seeded_namespace: str
    ) -> None:
        """The seeded tenant has believed facts and retired ones, so this
        exercises both `assertions` and `excluded` in one payload."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": seeded_namespace},
            )

        assert not result.is_error
        assert result.structured_content is not None

    async def test_a_search_that_finds_nothing_passes_it_too(self, seeded_namespace: str) -> None:
        """The empty payload is the one a schema written from a populated
        example gets wrong, and it is also the first thing a new client sees."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {
                    "query": "nothing matches this",
                    "namespace": seeded_namespace,
                    "predicates": ["not_a_predicate_in_the_pack"],
                },
            )

        assert not result.is_error
        assert result.structured_content is not None

    async def test_an_entity_card_passes_the_client_s_validator(
        self, seeded_namespace: str
    ) -> None:
        """`memory.get_entity` returns three nullable-or-empty fields at once -
        `as_of`, `assertions` and `neighbors` - which is the combination most
        likely to disagree with a schema."""
        async with connected() as session:
            found = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": seeded_namespace},
            )
            subject = found.structured_content["assertions"][0]["subject"]

            result = await call(
                session,
                "memory.get_entity",
                {"entity_id": subject, "namespace": seeded_namespace},
            )

        assert not result.is_error
        assert result.structured_content is not None

    async def test_a_refusal_is_exempt_from_validation(self, seeded_namespace: str) -> None:
        """A tool that declares a schema and then refuses must not trip the
        validator, or every error path would become a protocol error.

        The SDK gates on `isError`, and this pins that this server's refusals
        actually carry it - a refusal returned as a *successful* empty result
        would raise "has an output schema but did not return structured
        content" inside the client, turning a clear message into a crash.
        """
        async with connected() as session:
            result = await call(session, "memory.search", {"namespace": seeded_namespace})

        assert result.is_error
        assert result.structured_content is None

    async def test_the_server_advertises_the_schemas_this_repo_publishes(self) -> None:
        """The link between `outputs.py` and what a client actually receives.

        Everything above rests on the schema arriving over `tools/list`; a
        handler wired without its schema attached would make every validation
        above pass vacuously, because the SDK skips a tool that declares none.
        """
        async with connected() as session:
            listed = await session.list_tools()

        advertised = {t.name: t.output_schema for t in listed.tools}
        for name, schema in OUTPUT_SCHEMAS.items():
            assert advertised[name] == schema, f"{name} advertises a different schema"
        assert advertised["memory.commit"] is None
