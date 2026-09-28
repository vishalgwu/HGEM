"""The gateway's strict path and the worker write what they decide.  S8.4

Until 2026-09-27 neither did. Both called `pipeline.run()`, which decides and by
design applies nothing, and returned counts - so every decision a REST proposal
reached was computed and dropped: no assertion row and no `DECISION` event on the
audit chain. The worker's docstring said "the facts are in the store behind RLS",
and S8.3's DONE WHEN - "replaying the same request twice produces one assertion" -
was checked only as "one job was enqueued". Only `memory.propose` applied.

Everything here is real except the model: a migrated Postgres, the least-privilege
role, row-level security, the resolver writing its entity, the applier writing its
rows and audit events, and for the strict path the whole ASGI middleware chain. The
model is a script, and the script is short on purpose - the canonical draw proposes
two facts, every further draw agrees, and a fresh tenant has no incumbents, so the
judge is never asked and every entailment call is the one grounding pair.

What is asserted is the join rather than the verdicts: one `DECISION` per scored
candidate, one row per decision that wrote, and no failures. Which decisions they
are belongs to `test_decision.py`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

import httpx
from test_applier import chain

from fixtures.extraction import ALLERGY, CONTENT, PHARMACY, response
from fixtures.fakes import FakeLLM
from fixtures.gateway import WRITE_KEY, fake_state
from fixtures.pgvector import TIMEOUT_S, WHEN
from fixtures.settings import settings
from gateway.auth import InvalidCredentialError, Principal
from gateway.main import build_app
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pool import tenant_transaction
from guardmem_core.schemas import load_ontology
from guardmem_core.types import TenantId
from worker.tasks.evaluate import evaluate

if TYPE_CHECKING:
    import asyncpg

NAMESPACE: Final = "patient:8812"
FACTS: Final = json.dumps({"facts": [ALLERGY, PHARMACY]})
# One score, because identical draws leave only the grounding pair to ask about.
# `FakeLLM` repeats its last reply, so this answers every candidate's lookup.
ENTAILED: Final = json.dumps({"scores": [1.0]})
TURN: Final = {"turn_id": "t1", "role": "user", "text": CONTENT, "captured_at": WHEN.isoformat()}


async def rows_for(pool: asyncpg.Pool, tenant: TenantId, trace: str) -> int:
    """How many assertions one proposal wrote."""
    async with tenant_transaction(pool, tenant, timeout_s=TIMEOUT_S) as conn:
        count: int = await conn.fetchval(
            "SELECT count(*) FROM assertion WHERE trace_id = $1", trace
        )
    return count


class _WriterFor:
    """Resolves `WRITE_KEY` to a writer in this test's tenant.

    `fixtures.gateway.FakeAuth` answers a fixed tenant, and the applier writes an
    entity whose foreign key needs the tenant row `tenancy` created.
    """

    def __init__(self, tenant: TenantId) -> None:
        self.tenant = tenant

    def principal_for(self, credential: str) -> Principal:
        if credential != WRITE_KEY:
            raise InvalidCredentialError("unknown key")
        return Principal(tenant_id=self.tenant, key_id="writer", scopes=frozenset({"memory:write"}))


class TestTheWorkerWritesWhatItDecides:
    async def test_a_queued_proposal_is_decided_written_and_audited(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        tenant = TenantId(tenancy["tenant"])
        ctx: dict[str, Any] = {
            "settings": settings(),
            "pool": pool,
            "llm": FakeLLM(responses=[response(FACTS), response(FACTS, FACTS), response(ENTAILED)]),
        }
        payload = {
            "tenant_id": str(tenant),
            "trace_id": "tr_worker_s84",
            "namespace": NAMESPACE,
            "turns": [TURN],
            "source_tier": "verified_user",
            "k": 3,
            "tier": "balanced",
            "body_hash": None,
        }

        summary = await evaluate(ctx, payload)

        assert summary["scored"] == 2
        assert summary["failed"] == 0
        assert (await chain(pool, tenant)).count("DECISION") == 2
        assert await rows_for(pool, tenant, "tr_worker_s84") == summary["written"]


class TestTheStrictPathWritesWhatItDecides:
    async def test_a_strict_proposal_is_decided_written_and_audited(
        self, pool: asyncpg.Pool, tenancy: dict[str, str]
    ) -> None:
        """Through the real app and middleware, on the test's own loop.

        `httpx.ASGITransport` rather than `TestClient`, for the reason
        `tests/security/test_tenant_isolation.py` records: an asyncpg pool belongs
        to the loop that created it, and `TestClient` runs the app on its own.
        """
        tenant = TenantId(tenancy["tenant"])
        app = build_app()
        app.state.gateway = fake_state(
            auth=_WriterFor(tenant),
            pool=pool,
            llm=FakeLLM(responses=[response(FACTS), response(ENTAILED)]),
            graph=NetworkXGraphStore(),
            ontology=load_ontology("clinical"),
            embedder=HashEmbedder(),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            reply = await client.post(
                "/memory/propose",
                json={
                    "namespace": NAMESPACE,
                    "turns": [TURN],
                    "mode": "strict",
                    "source_tier": "verified_user",
                },
                headers={"Authorization": f"Bearer {WRITE_KEY}"},
            )

        assert reply.status_code == 200, reply.text
        decided = reply.json()
        assert decided["scored"] == 2
        assert decided["failed"] == 0
        assert (await chain(pool, tenant)).count("DECISION") == 2
        assert await rows_for(pool, tenant, decided["trace_id"]) == decided["written"]
