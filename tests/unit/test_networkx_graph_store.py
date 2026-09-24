"""`NetworkXGraphStore`, and the fake it has to agree with.  BUILD_NOTEBOOK.md S3.4

S3.4's DONE WHEN has two clauses. "`degree()` and `neighbors()` return correct
values in a unit test" is most of this module; "the pipeline never imports the
concrete class" is an `import-linter` contract in `pyproject.toml`, because
`RULES.md` §0 wants rules checked by a linter where one can do it - and
`TestTheDoneWhenIsEnforced` below pins the contract itself against quiet
deletion.

**Every shared behaviour runs against both implementations.** The `store`
fixture is parametrised over `FakeGraphStore` and `NetworkXGraphStore`, so each
test below runs twice. That is not thoroughness for its own sake: `fakes.py`
says in as many words that a fake permitting what a real store forbids makes the
whole week-1 unit suite a measurement of the wrong system, and until today there
was no real `GraphStore` to check that claim against. Now there is, and the two
answer the same questions the same way or this file fails.

What is **not** shared has its own class. The single-tenant guard is specific to
this backend, and so is the question of what a non-string object is stored as -
the fake has no node keys to collide.

`degree()` is the input to `MEMORY_ENGINE.md` §3.3's `graph_fanout` feature,
`min(1, log(1+deg)/log(1+50))`. The formula is not tested here because
`l3_score/impact.py` does not exist yet; what is tested is that the number it
will be handed counts live edges, in both directions, exactly once.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest

from conftest import REPO_ROOT
from fixtures.assertions import TENANT
from fixtures.fakes import FakeGraphStore
from fixtures.graph_contract import OTHER, GraphStoreContract, fact
from guardmem_core.errors import ValidationRejected
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.types import EntityId

if TYPE_CHECKING:
    from guardmem_core.memory.graph.base import GraphStore


class TestTheContractOnTheInProcessBackends(GraphStoreContract):
    """Every shared behaviour, run once per in-process backend.

    The assertions live in `fixtures/graph_contract.py` since S7.1, because
    Neo4j has to run the same ones and cannot join this parametrisation - it
    needs a container, so it lives in the integration suite. Two copies of
    sixteen assertions is how two backends come to agree until somebody edits
    one; a base class is how they cannot.

    `fakes.py` says a fake permitting what a real store forbids makes the whole
    unit suite a measurement of the wrong system. Running the fake through the
    same contract as a real backend is what checks that claim.
    """

    @pytest.fixture(params=["fake", "networkx"])
    def store(self, request: pytest.FixtureRequest) -> GraphStore:
        """Both in-process implementations, so every inherited test runs twice."""
        if request.param == "fake":
            return FakeGraphStore()
        return NetworkXGraphStore()


class TestTheSingleTenantGuard:
    """Specific to this backend; `FakeGraphStore` has no such guard."""

    async def test_a_subject_held_for_another_tenant_is_refused(self) -> None:
        """The protocol gives `degree()` no tenant to filter on, so a graph
        holding two tenants would price one's blast radius with the other's
        edges. Entity ids are database-wide UUIDs so the exposure is nil, and
        `RULES.md` §4 still wants the check rather than the coincidence."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected, match="single-tenant"):
            await store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

    async def test_the_owning_tenant_may_keep_writing(self) -> None:
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", predicate="allergy"))

        await store.upsert_assertion(fact(subject="e-1", predicate="employer", obj="Northwind"))

        assert await store.degree(EntityId("e-1")) == 2

    async def test_two_tenants_may_share_a_literal(self) -> None:
        """Guarded on the subject only. Two tenants recording an allergy to
        penicillin legitimately share the node `"penicillin"`, and refusing that
        would refuse a correct write."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", obj="penicillin", tenant=TENANT))

        await store.upsert_assertion(fact(subject="e-2", obj="penicillin", tenant=OTHER))

        assert await store.degree(EntityId("e-2")) == 1

    async def test_the_refusal_names_the_proposal_it_came_from(self) -> None:
        """`RULES.md` §2.3: every raise inside the pipeline carries `trace_id`
        and `candidate_id`."""
        store = NetworkXGraphStore()
        await store.upsert_assertion(fact(subject="e-1", tenant=TENANT))

        with pytest.raises(ValidationRejected) as raised:
            await store.upsert_assertion(fact(subject="e-1", tenant=OTHER))

        assert raised.value.trace_id == "tr_s34"
        assert raised.value.candidate_id is not None


class TestTheDoneWhenIsEnforced:
    def test_an_import_linter_contract_forbids_the_pipeline_a_concrete_store(
        self,
    ) -> None:
        """S3.4's second clause: "the pipeline never imports the concrete class".

        Pinned here because the enforcement lives in configuration, and a
        contract deleted from `pyproject.toml` fails nothing - `lint-imports`
        would simply report one fewer contract kept, in green.
        """
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        contracts = config["tool"]["importlinter"]["contracts"]
        # `.get`, not `[...]`: not every contract type has `source_modules`.
        # S8.1 added an `independence` contract over the two services, which
        # spells its operands `modules`, and this comprehension raised
        # `KeyError` on it - a test for one contract broken by an unrelated
        # contract being added beside it.
        forbidding = [c for c in contracts if c.get("source_modules") == ["guardmem_core.pipeline"]]

        assert forbidding, "no contract stops the pipeline importing a concrete store"
        forbidden = set(forbidding[0]["forbidden_modules"])
        assert "guardmem_core.memory.graph.networkx_store" in forbidden
        assert "guardmem_core.memory.vector.pgvector_store" in forbidden
