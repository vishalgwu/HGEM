"""`governance.build_deps`: the one composition of the Postgres-backed pipeline.

Four composition roots built the same thirteen-field `Deps` by hand until this
existed, so what these tests pin is the part that used to be copied: which
parts are built here, which are taken as given, and which come off settings.
The last matters most. The orchestrator once shipped with a hard-coded fan-out
of 8, so `GM_MAX_CONCURRENT_SCORES` did nothing while looking as though it had -
a composition that stopped reading the setting would repeat that silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fixtures.assertions import TENANT
from fixtures.fakes import FakeGraphStore, FakeLLM, FakeVectorStore
from fixtures.settings import settings
from guardmem_core.governance import build_deps
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from guardmem_core.schemas import load_ontology

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.pipeline.deps import Deps


def compose(**overrides: Any) -> Deps:
    """`build_deps` over fakes and a pool nothing here touches.

    Constructing a store or a resolver stores the pool and opens nothing, so a
    stand-in object is enough - and a test that did reach it would fail loudly
    on the attribute, which is the right failure.
    """
    arguments: dict[str, Any] = {
        "settings": settings(max_concurrent_scores=3, tau_hi=0.8),
        "pool": cast("asyncpg.Pool", object()),
        "tenant_id": TENANT,
        "llm": FakeLLM(),
        "graph": FakeGraphStore(),
        "embedder": HashEmbedder(),
        "ontology": load_ontology("clinical"),
        "policy_version": "test-v1",
    }
    return build_deps(**{**arguments, **overrides})


class TestWhatItBuilds:
    def test_the_store_is_a_postgres_store(self) -> None:
        """The default is the real backend; a caller wanting another passes it."""
        assert isinstance(compose().vector, PgVectorStore)

    def test_a_store_that_is_passed_in_is_used_as_is(self) -> None:
        """`mcp_server` hands over its `ToolContext` store, which is what lets its
        unit suite drive the handlers against a fake."""
        given = FakeVectorStore()

        assert compose(vector=given).vector is given

    def test_the_judge_and_the_resolver_are_the_shipped_ones(self) -> None:
        deps = compose()

        assert isinstance(deps.nli, LLMJudge)
        assert isinstance(deps.resolver, NamespaceEntityResolver)


class TestWhatComesOffSettings:
    def test_the_concurrency_bound_is_the_configured_one(self) -> None:
        assert compose().max_concurrent_scores == 3

    def test_the_thresholds_are_the_configured_set(self) -> None:
        assert compose().thresholds.tau_hi == 0.8


class TestWhatIsTakenAsGiven:
    def test_the_caller_owns_the_backends_and_the_policy_stamp(self) -> None:
        """What differs between the four roots is an argument, never a default."""
        graph = FakeGraphStore()
        llm = FakeLLM()

        deps = compose(graph=graph, llm=llm)

        assert deps.graph is graph
        assert deps.llm is llm
        assert deps.policy_version == "test-v1"

    def test_the_scoring_weights_are_the_v1_sets(self) -> None:
        deps = compose()

        assert deps.weights == V1_WEIGHTS
        assert deps.betas == V1_BETAS
