"""S6.2's DONE WHEN, against real Postgres and the seeded tenant.  S6.2

    "from the Inspector you can propose a fact, get a decision back, and then
    find it via `memory.search` with its provenance."

**The middle clause cannot be satisfied and this module is where that is
recorded as a test rather than as a paragraph.** `memory.propose` needs
`pipeline.run`, and `Deps` cannot be built: an `EntityResolver` and a
`CandidateClassifier` have no implementation, and the entailer S5.1 now has is
not wired into the orchestrator. So the step's acceptance splits in two:

- the half that **is** satisfied - "find it via `memory.search` with its
  provenance" - is asserted here end to end, over the MCP protocol, against the
  twenty-eight assertions `make seed` writes through the real path. Those spans
  were located in a real transcript by `link_span`, which is what makes this a
  provenance test rather than a fixture test.
- the half that is not is asserted as a **refusal that names what is missing**,
  so that the day the pipeline is wired this test fails and has to be rewritten
  into the round trip the step actually asks for. A skip would go green forever.

Driven over the SDK's in-memory transport for the reason
`test_mcp_stdio.py` gives: the pipe is the SDK's code, and spawning a
subprocess would add a Windows/POSIX difference to a test about tool
semantics.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, Final

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from fixtures.seed import PATIENT_NAME
from guardmem_core.settings import get_settings
from mcp_server.server import build_server
from mcp_server.tools.pipeline import MISSING_DEPENDENCIES

_TIMEOUT_S: Final = 30.0

# The namespace `scripts/demo_tenant_data.py` writes into. Read from the seeded
# rows rather than hard-coded twice - see `namespace`.
_SEEDED_PREDICATE: Final = "allergy"


@pytest.fixture
def _server_env(
    app_role_dsn: str, demo: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    """Configure the server for the seeded demo tenant, and yield its namespace.

    The tenant id comes from the database rather than from a constant: the seed
    derives it as a `uuid5` of its own slug, and a test that recomputed that
    derivation would be a second implementation of it - free to drift, and
    silently, since a wrong tenant reads as an empty namespace rather than an
    error.
    """
    _connection, tenant = demo
    monkeypatch.setenv("GM_DATABASE_URL", app_role_dsn)
    monkeypatch.setenv("GM_MCP_TENANT_ID", tenant)
    get_settings.cache_clear()
    yield tenant
    get_settings.cache_clear()


@pytest.fixture
async def namespace(demo: tuple[Any, str]) -> str:
    """The namespace the seed wrote into, read back from it."""
    connection, _tenant = demo
    found = await connection.fetchval(
        "SELECT namespace FROM assertion WHERE predicate = $1 LIMIT 1", _SEEDED_PREDICATE
    )
    assert found is not None, "the seed wrote no allergy; the fixture is out of step with it"
    return str(found)


@asynccontextmanager
async def connected() -> AsyncIterator[ClientSession]:
    """An initialized client over a fully started server.

    A helper entered inside each test body rather than a fixture, for the reason
    `test_mcp_stdio.py` spells out: `ClientSession` opens an anyio task group and
    anyio refuses to close a cancel scope from a different task, which is what an
    async-generator fixture can produce on teardown.
    """
    server = build_server()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server_read, server_write = server_streams
        task = asyncio.create_task(
            server.run(
                server_read,
                server_write,
                server.create_initialization_options(),
                raise_exceptions=True,
            )
        )
        client_read, client_write = client_streams
        try:
            async with ClientSession(client_read, client_write) as client:
                await asyncio.wait_for(client.initialize(), timeout=_TIMEOUT_S)
                yield client
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    """One tool call, bounded."""
    return await asyncio.wait_for(session.call_tool(name, arguments), timeout=_TIMEOUT_S)


@pytest.mark.usefixtures("_server_env")
class TestTheHalfOfTheDoneWhenThatWorks:
    """ "...find it via `memory.search` with its provenance"."""

    async def test_a_seeded_fact_comes_back_with_a_real_source_span(self, namespace: str) -> None:
        """The provenance is the point. These spans were located in a real
        forty-turn transcript by `link_span`, so `verbatim` is text that is
        actually in the source rather than a fixture string."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": namespace, "predicates": ["allergy"]},
            )

        assert not result.is_error, result.content[0].text
        assertions = result.structured_content["assertions"]
        assert assertions, "the seed writes four allergies"
        citation = assertions[0]["provenance"][0]
        assert citation["verbatim"]
        assert citation["source_hash"].startswith("sha256:")
        assert citation["span"][1] > citation["span"][0], "a half-open span with width"
        assert citation["tier"] == "verified_user"

    async def test_a_superseded_fact_is_excluded_rather_than_returned(self, namespace: str) -> None:
        """The seed supersedes two facts from a three-turn follow-up call, and
        `home_address` is one. §2.1's `excluded` is what makes "we retired that
        record" distinguishable from "we have no record" - invariant I6 keeps
        the retired row out of the results, and this keeps it visible anyway."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {
                    "query": "home address",
                    "namespace": namespace,
                    "predicates": ["home_address"],
                },
            )

        payload = result.structured_content
        assert [a["object"] for a in payload["assertions"]] == ["3 Calder Way, Leeds"]
        assert len(payload["excluded"]) == 1
        retired = payload["excluded"][0]
        assert retired["object"] == "14 Ashfield Road, Leeds"
        assert retired["reason"].startswith("superseded_by ")
        assert retired["at"], "when it stopped being believed"

    async def test_excluded_is_scoped_to_what_was_asked_about(self, namespace: str) -> None:
        """The seed retires a `home_address` *and* a `preferred_pharmacy`. An
        allergy query must not be told that two of its facts were retired -
        which is what an unscoped `excluded` did, found by driving this against
        the seeded tenant by hand."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": namespace, "predicates": ["allergy"]},
            )

        assert result.structured_content["excluded"] == []

    async def test_get_entity_groups_the_patients_facts_by_predicate(self, namespace: str) -> None:
        """§2.4, and the tool that produces an entity id `search` can filter on -
        which matters because entity resolution does not exist, so an id has to
        come from somewhere."""
        async with connected() as session:
            found = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": namespace, "predicates": ["allergy"]},
            )
            subject = found.structured_content["assertions"][0]["subject"]

            result = await call(
                session,
                "memory.get_entity",
                {"entity_id": subject, "namespace": namespace},
            )

        payload = result.structured_content
        assert not result.is_error
        assert "allergy" in payload["assertions"]
        assert len(payload["assertions"]["allergy"]) == 4, "the seed writes four"
        assert payload["graph_backed"] is False, (
            "NetworkX holds the graph in-process, so a fresh server has none - "
            "S7.1's Neo4j backend is the first durable one, and until then an "
            "empty neighbour list is a limitation rather than a finding"
        )

    async def test_a_point_in_time_query_recovers_the_retired_address(self, namespace: str) -> None:
        """`ADR-0002` exists to make this answerable, and it is the difference
        between supersession and deletion."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {
                    "query": "home address",
                    "namespace": namespace,
                    "predicates": ["home_address"],
                    "as_of": "2026-05-01T00:00:00+00:00",
                },
            )

        objects = [a["object"] for a in result.structured_content["assertions"]]
        assert objects == ["14 Ashfield Road, Leeds"], (
            "what was believed in May, before the follow-up call moved it"
        )

    async def test_the_seeded_patient_is_reachable_by_name_nowhere(self, namespace: str) -> None:
        """The gap this step could not close, asserted so it is not forgotten.

        `PATIENT_NAME` is a real canonical name in the seeded database and there
        is no way to search by it: `subject` is a `uuid` filter, and turning a
        name into an id is entity resolution - specified in no document,
        implemented nowhere. An agent handed a patient's name cannot use this
        server, which is the single most user-visible consequence of that gap.
        """
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergies", "namespace": namespace, "subject": PATIENT_NAME},
            )

        assert result.is_error
        assert "resolved entity id" in result.content[0].text


@pytest.mark.usefixtures("_server_env")
class TestTheHalfThatIsBlocked:
    """ "propose a fact, get a decision back" - which cannot happen yet.

    Asserted as a refusal rather than skipped. A skip is green forever; this
    fails the day the pipeline is wired, and the person who wires it is exactly
    the person who should be made to write the round trip S6.2 actually asks for.
    """

    async def test_propose_declines_and_names_what_is_missing(self, namespace: str) -> None:
        async with connected() as session:
            result = await call(
                session,
                "memory.propose",
                {
                    "content": "The patient's preferred pharmacy is CVS #4021.",
                    "namespace": namespace,
                    "mode": "strict",
                },
            )

        assert result.is_error
        text = result.content[0].text
        # Walks the tuple rather than naming entries: the list shrinks as the
        # gaps close - S9.1 removed `LLMClient`, S5.1's entailer removed
        # `EntailFn` - and a spelled-out assertion fails on those commits for
        # the wrong reason. What must hold is that the refusal reaches a real
        # MCP client with every remaining gap named.
        assert MISSING_DEPENDENCIES, "a refusal that names nothing is a shrug"
        for missing in MISSING_DEPENDENCIES:
            assert missing in text

    async def test_commit_refuses_an_unsourced_write_before_anything_else(
        self, namespace: str
    ) -> None:
        """`RULES.md` non-negotiable #1 needs no pipeline to enforce, so it is
        enforced with no pipeline: the caller is told the real objection."""
        async with connected() as session:
            result = await call(
                session,
                "memory.commit",
                {
                    "namespace": namespace,
                    "assertions": [
                        {
                            "subject": "s",
                            "predicate": "allergy",
                            "object": "latex",
                            "provenance": [],
                        }
                    ],
                },
            )

        assert result.is_error
        assert "no unsourced write path" in result.content[0].text
