"""What the server process owns, and how long it owns it.  BUILD_NOTEBOOK.md S6.1

S6.1's instruction is "wire lifespan: settings, Postgres pool, LLM client,
pipeline deps". Three of those four are wired here. The fourth cannot be, and
the reason is the same one that has CHECKPOINT B recorded as BLOCKED:
**there is no `LLMClient` implementation in this repository.** `llm/base.py`
declares the Protocol, `tests/fixtures/fakes.py` implements it for the suite,
and S9.1 builds the provider adapters. Binding `FakeLLM` here to make the
sentence come true would give the first user-facing surface in the project a
scripted model behind it, which is the one thing a governed-memory server must
not have.

`pipeline.Deps` follows it for the same reason plus two more: `EntityResolver`
and `CandidateClassifier` are Protocols that nothing implements either
(`pipeline/deps.py` explains at length why neither gets a default), so `Deps`
cannot be constructed at all today. That is what S6.2 is actually blocked on -
not on writing tool handlers - and it is better known now than discovered
halfway through the step.

**Why a pool opens for a server that serves no tools.** Because the alternative
makes S6.1's DONE WHEN check nothing. "The inspector connects and lists zero
tools" is a statement about the whole startup path - that `Settings` validated,
that `GM_DATABASE_URL` points at a reachable database, that the ontology on disk
parses - and a lifespan that allocated nothing would pass it on a machine where
none of that is true. `RULES.md` §2.2 also puts pool creation in lifespan
explicitly: one pool per process, never one per request.

**Shutdown closes what startup opened, in reverse, and `finally` is why.** An
MCP client disconnecting is a normal end to a stdio session, and a pool that is
dropped rather than closed leaves its connections to be reaped by Postgres on a
timeout. One orphan per restart is invisible; a supervisor restarting a crash
loop reaches `max_connections` and takes the rest of the deployment with it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.schemas import load_ontology
from guardmem_core.settings import Settings, get_settings

if TYPE_CHECKING:
    import asyncpg

    from guardmem_core.schemas.ontology import Ontology

__all__ = ["DEFAULT_ONTOLOGY", "ServerState", "lifespan"]

_LOGGER: Final = logging.getLogger(__name__)

# The only pack that ships (`ontology/clinical.yaml`); `PROJECT_TREE.md` names
# legal and fintech packs that arrive at the step that needs them. Not a
# `Settings` field yet, deliberately: a setting with one legal value is a
# setting nobody can get wrong, and the step that adds the second pack is the
# step that knows whether the choice is per-process or per-namespace.
DEFAULT_ONTOLOGY: Final = "clinical"


@dataclass(frozen=True, slots=True)
class ServerState:
    """Process-scoped resources, built once at startup.

    Attributes:
        settings: The validated configuration. Held rather than re-read, so
            every handler sees the same values a restart would be needed to
            change - `RULES.md` §2.4's "no global mutable state", with the
            single instance passed rather than imported.
        pool: The Postgres pool. Request-scoped stores are built *from* it -
            `PgVectorStore` binds a tenant, and the tenant comes from an
            authenticated request - so the pool is what lives here and the store
            is not.
        graph: The entity graph. In-process and therefore lost on restart, which
            `NetworkXGraphStore` says of itself; the outbox is what rebuilds it,
            and S7.1's Neo4j backend is the first durable one.
        embedder: Write-side embedding. **`HashEmbedder` models no semantics** -
            it hashes text, so identical text retrieves identically and nothing
            else does. It is here because it is the only `Embedder` in the
            package until S9.1, and naming it in the state is better than a
            handler reaching for one on its own. Nothing measured against it is
            a retrieval quality number.
        ontology: The validated predicate pack, loaded once. `load_ontology` is
            itself cached, so this field is about making the dependency visible
            rather than about avoiding a second parse.

    Frozen, because none of it may be swapped while a session is open: a handler
    that saw a different pool halfway through a request would be reading a
    different database than the one it authenticated against.
    """

    settings: Settings
    pool: asyncpg.Pool
    graph: NetworkXGraphStore
    embedder: HashEmbedder
    ontology: Ontology


@asynccontextmanager
async def lifespan(_server: object = None) -> AsyncIterator[ServerState]:
    """Open the process's resources, hand them over, and close them again.

    Args:
        _server: The `Server` the SDK passes to a lifespan. Unused and ignored:
            nothing here needs the protocol object, and taking it positionally
            with a default is what lets the same function be called directly by
            a test as `async with lifespan():`. The SDK's signature is the
            constraint; the default is the affordance.

    Yields:
        The `ServerState` every handler reads from.

    Raises:
        pydantic.ValidationError: the environment does not satisfy `Settings` -
            a missing `GM_DATABASE_URL`, a threshold set that is out of order, a
            stray key in `.env`. Raised before anything is allocated, so a
            misconfigured process fails at startup rather than on first use.
        StoreUnavailable: Postgres is unreachable at the configured DSN.
        FileNotFoundError: the ontology pack is not installed, which on an
            editable checkout means a typo and on a wheel means the `.yaml`
            files were not packaged.

    The DSN is converted with `libpq_dsn` because `GM_DATABASE_URL` carries
    SQLAlchemy's `postgresql+asyncpg://` marker for Alembic's benefit and
    asyncpg rejects it outright - `pool.py` owns that conversion and this is one
    of its call sites rather than a fourth inline `str.replace`.
    """
    settings = get_settings()
    pool = await create_pool(libpq_dsn(str(settings.database_url)))
    try:
        _LOGGER.info(
            "guardmem-mcp started",
            extra={"env": settings.env, "ontology": DEFAULT_ONTOLOGY},
        )
        yield ServerState(
            settings=settings,
            pool=pool,
            graph=NetworkXGraphStore(),
            embedder=HashEmbedder(),
            ontology=load_ontology(DEFAULT_ONTOLOGY),
        )
    finally:
        await pool.close()
        _LOGGER.info("guardmem-mcp stopped")
