"""S6.2's DONE WHEN, against real Postgres and the seeded tenant.  S6.2

    "from the Inspector you can propose a fact, get a decision back, and then
    find it via `memory.search` with its provenance."

**The middle clause is satisfied as of S6.2's write half, up to the model.**
`memory.propose` builds a real `Deps` and runs the pipeline; a CI runner has no
provider, so the call fails at the first completion and that is what is asserted
- the last step failing proves every earlier one was wired. So the step's
acceptance splits in two:

- the half that **is** satisfied - "find it via `memory.search` with its
  provenance" - is asserted here end to end, over the MCP protocol, against the
  twenty-eight assertions `make seed` writes through the real path. Those spans
  were located in a real transcript by `link_span`, which is what makes this a
  provenance test rather than a fixture test.
- the half that is not - seeing the row afterwards - waits on the applier.
  `run()` reaches a decision and writes nothing, so there is no row to find, and
  `memory.propose` says so with `applied: false` rather than letting a caller
  infer it.

Driven over the SDK's in-memory transport for the reason
`test_mcp_stdio.py` gives: the pipe is the SDK's code, and spawning a
subprocess would add a Windows/POSIX difference to a test about tool
semantics.
"""

from __future__ import annotations

import pytest

from fixtures.mcp_session import call, connected
from fixtures.seed import PATIENT_NAME


@pytest.mark.usefixtures("demo_server_env")
class TestTheHalfOfTheDoneWhenThatWorks:
    """ "...find it via `memory.search` with its provenance"."""

    async def test_a_seeded_fact_comes_back_with_a_real_source_span(
        self, seeded_namespace: str
    ) -> None:
        """The provenance is the point. These spans were located in a real
        forty-turn transcript by `link_span`, so `verbatim` is text that is
        actually in the source rather than a fixture string."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": seeded_namespace, "predicates": ["allergy"]},
            )

        assert not result.is_error, result.content[0].text
        assertions = result.structured_content["assertions"]
        assert assertions, "the seed writes four allergies"
        citation = assertions[0]["provenance"][0]
        assert citation["verbatim"]
        assert citation["source_hash"].startswith("sha256:")
        assert citation["span"][1] > citation["span"][0], "a half-open span with width"
        assert citation["tier"] == "verified_user"

    async def test_a_superseded_fact_is_excluded_rather_than_returned(
        self, seeded_namespace: str
    ) -> None:
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
                    "namespace": seeded_namespace,
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

    async def test_excluded_is_scoped_to_what_was_asked_about(self, seeded_namespace: str) -> None:
        """The seed retires a `home_address` *and* a `preferred_pharmacy`. An
        allergy query must not be told that two of its facts were retired -
        which is what an unscoped `excluded` did, found by driving this against
        the seeded tenant by hand."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": seeded_namespace, "predicates": ["allergy"]},
            )

        assert result.structured_content["excluded"] == []

    async def test_get_entity_groups_the_patients_facts_by_predicate(
        self, seeded_namespace: str
    ) -> None:
        """§2.4, and the tool that produces an entity id `search` can filter on -
        which matters because entity resolution does not exist, so an id has to
        come from somewhere."""
        async with connected() as session:
            found = await call(
                session,
                "memory.search",
                {"query": "allergy", "namespace": seeded_namespace, "predicates": ["allergy"]},
            )
            subject = found.structured_content["assertions"][0]["subject"]

            result = await call(
                session,
                "memory.get_entity",
                {"entity_id": subject, "namespace": seeded_namespace},
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

    async def test_a_point_in_time_query_recovers_the_retired_address(
        self, seeded_namespace: str
    ) -> None:
        """`ADR-0002` exists to make this answerable, and it is the difference
        between supersession and deletion."""
        async with connected() as session:
            result = await call(
                session,
                "memory.search",
                {
                    "query": "home address",
                    "namespace": seeded_namespace,
                    "predicates": ["home_address"],
                    "as_of": "2026-05-01T00:00:00+00:00",
                },
            )

        objects = [a["object"] for a in result.structured_content["assertions"]]
        assert objects == ["14 Ashfield Road, Leeds"], (
            "what was believed in May, before the follow-up call moved it"
        )

    async def test_the_seeded_patient_is_reachable_by_name_nowhere(
        self, seeded_namespace: str
    ) -> None:
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
                {"query": "allergies", "namespace": seeded_namespace, "subject": PATIENT_NAME},
            )

        assert result.is_error
        assert "resolved entity id" in result.content[0].text


@pytest.mark.usefixtures("demo_server_env")
class TestTheWriteHalf:
    """ "propose a fact, get a decision back" - which now happens.

    What this cannot assert is the *decision*, because a runner has no model.
    What it does assert is that the whole chain is wired: a real MCP client, a
    real server built from the real `lifespan`, a real `Deps`, a real Postgres -
    and that the only thing left missing is the provider, reported as
    `GM_PROVIDER` rather than as a refusal about unwired parts.

    That distinction is the test. The class this replaced asserted the tool
    declined because dependencies were missing, and went on passing after all of
    them landed, because it walked a tuple nobody had emptied.
    """

    async def test_propose_reaches_the_pipeline_and_only_the_model_is_missing(
        self, seeded_namespace: str
    ) -> None:
        """`GM_PROVIDER` is the signal. A runner has no Ollama, so the first
        model call fails - which is the *last* thing that fails, and proves
        every step before it was wired."""
        async with connected() as session:
            result = await call(
                session,
                "memory.propose",
                {
                    "content": "The patient's preferred pharmacy is CVS #4021.",
                    "namespace": seeded_namespace,
                    "mode": "strict",
                },
            )

        assert result.is_error
        text = result.content[0].text
        assert "GM_PROVIDER" in text, (
            f"propose should now fail at the model rather than refusing as unwired. Got: {text}"
        )

    async def test_propose_still_refuses_async_because_nothing_would_decide(
        self, seeded_namespace: str
    ) -> None:
        """§2.2's *default* mode, and the one part of the write half that is
        still genuinely unbuilt: S8.4's queue."""
        async with connected() as session:
            result = await call(
                session,
                "memory.propose",
                {"content": "x", "namespace": seeded_namespace, "mode": "async"},
            )

        assert result.is_error
        assert "S8.4" in result.content[0].text

    async def test_commit_refuses_for_the_scoring_question_not_a_missing_part(
        self, seeded_namespace: str
    ) -> None:
        """§2.3 skips extraction, so §3.1's entropy has no samples. That is
        `w_H = 0.35` of `C`, and inventing it is a decision for an ADR."""
        async with connected() as session:
            result = await call(
                session,
                "memory.commit",
                {
                    "assertions": [
                        {
                            "subject": "patient:7781",
                            "predicate": "allergy",
                            "object": "penicillin",
                            "provenance": [{"verbatim": "allergic to penicillin"}],
                        }
                    ],
                    "namespace": seeded_namespace,
                },
            )

        assert result.is_error
        assert "no K samples" in result.content[0].text

    async def test_commit_refuses_an_unsourced_write_before_anything_else(
        self, seeded_namespace: str
    ) -> None:
        """`RULES.md` non-negotiable #1 needs no pipeline to enforce, so it is
        enforced with no pipeline: the caller is told the real objection."""
        async with connected() as session:
            result = await call(
                session,
                "memory.commit",
                {
                    "namespace": seeded_namespace,
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
