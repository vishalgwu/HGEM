"""Shared scaffolding for the MCP server's tool tests.  S6.2

`tests/unit/test_mcp_tools.py` and `test_mcp_write_tools.py` need the same three
things - a `ServerState` over fakes, a `ToolContext` over a fake store, and a
store with rows in it - and they are here rather than duplicated because the
second module was split out of the first when it passed `RULES.md` §2.4's
400-line cap. Two copies of `state()` is exactly how one of them quietly stops
matching the real `ServerState`.

A plain helper module, not a pytest plugin: nothing here is a fixture, and
`fixtures/assertions.py` and `fixtures/fakes.py` set the same precedent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fixtures.assertions import NS, TENANT
from fixtures.fakes import FakeGraphStore, FakeLLM, FakeVectorStore
from fixtures.settings import settings
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.schemas import load_ontology
from mcp_server.lifespan import ServerState
from mcp_server.tools.context import ToolContext

if TYPE_CHECKING:
    import asyncpg

__all__ = ["SUBJECT", "context", "state", "store_with"]

SUBJECT = "3c723e01-4c51-5cb4-a99e-258c318ef7c6"


def state(**overrides: object) -> ServerState:
    """A `ServerState` with fakes and no pool.

    `pool` is `None` behind a cast. It is a frozen *dataclass* rather than a
    pydantic model, so nothing validates the field at construction, and no test
    in this module reaches it: the handlers take their store from `ToolContext`,
    which is the seam that makes them testable at all. A test that did touch the
    pool would fail with an `AttributeError` naming it, which is the right
    failure - it means the handler stopped going through the protocol.
    """
    base: dict[str, object] = {
        "settings": settings(mcp_tenant_id=str(TENANT), mcp_default_namespace=str(NS)),
        "pool": cast("asyncpg.Pool", None),
        "graph": FakeGraphStore(),
        "embedder": HashEmbedder(),
        "ontology": load_ontology("clinical"),
        # S6.2's write half put a model client on the state. Scripted and
        # empty by default: the two read tools never call a model, and a
        # test that needs `memory.propose` to reach one passes its own.
        "llm": FakeLLM(),
    }
    return ServerState(**(base | overrides))  # type: ignore[arg-type]


def context(store: FakeVectorStore | None = None, **state_overrides: object) -> ToolContext:
    """A `ToolContext` over a fake store, built without `context_for`.

    Bypassing `context_for` is deliberate: that function's own behaviour is
    tested separately below, and routing every handler test through it would
    make each of them also a test of tenant resolution.
    """
    return ToolContext(
        state=state(**state_overrides),
        tenant_id=TENANT,
        namespace=NS,
        store=store or FakeVectorStore(),
    )


async def store_with(*assertions: object) -> FakeVectorStore:
    """A fake store holding `assertions`, already visible.

    Visible because the relay is what sets that flag and no relay runs here;
    a store full of invisible rows would make every search return nothing and
    every test pass for the wrong reason.
    """
    store = FakeVectorStore()
    await store.upsert([cast("Any", a) for a in assertions])
    return store
