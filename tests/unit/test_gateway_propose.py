"""The write path, both modes.  BUILD_NOTEBOOK.md S8.4

S8.4: "`mode=async` -> validate, hash payload to blob store, enqueue, return 202 +
`trace_id` in under 80 ms. `mode=strict` runs the pipeline inline with K=1 FAST."

**The first test in this file is the one that matters most and looks the least like
a test.** The gateway and the worker must name the same Redis queue and the same job,
and the import-linter contract forbidding them from importing each other means the
two strings are duplicated. A drift does not fail anything: the gateway enqueues into
a queue nothing reads, every proposal is accepted, and none is ever evaluated. That is
the worst failure this system has - silent, total, and indistinguishable from working
until somebody asks where a fact went.

No Redis and no Postgres here. The queue is a fake that records what it was asked to
enqueue, which is the whole contract the async path has: `mode=strict` runs the
pipeline and belongs in the integration suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from fixtures.gateway import READ_KEY, TENANT, WRITE_KEY, app_with, fake_state
from gateway.propose import JOB, QUEUE
from worker.main import QUEUE as WORKER_QUEUE

TURNS = [
    {
        "turn_id": "t01",
        "role": "user",
        "text": "Penicillin. Hives as a child.",
        "captured_at": "2026-02-03T09:15:00+00:00",
    }
]


class TestTheTwoServicesAgreeOnTheQueue:
    """A drift here is silent and total. See the module docstring."""

    def test_the_gateway_and_the_worker_name_the_same_queue(self) -> None:
        """`gateway.propose.QUEUE` must equal `worker.main.QUEUE`.

        Duplicated rather than shared because the services may not import each other,
        and a constant in `guardmem-core` would put a queue name in a library that
        neither the eval harness nor the MCP server has any use for. So the agreement
        is a test rather than a type.
        """
        assert QUEUE == WORKER_QUEUE

    def test_the_job_name_is_one_the_worker_registers(self) -> None:
        """arq dispatches by the string the gateway sends.

        Asserted against `WorkerSettings.functions` rather than a second literal -
        the point is that the worker actually registers a coroutine under this name,
        not that two strings match.
        """
        from worker.main import WorkerSettings

        registered = {fn.__name__ for fn in WorkerSettings.functions}

        assert JOB in registered


def _post(client: TestClient, body: dict[str, Any], **headers: str) -> Any:
    """POST a proposal as the writer.

    Args:
        client: The test client.
        body: The proposal.
        headers: Extra headers, e.g. `Idempotency-Key`.

    Returns:
        The response.

    Local rather than in `fixtures.gateway`: that module is about process *state*, and
    this is about one route's shape. A shared poster would have to know this URL, and
    the next route would want its own anyway.
    """
    return client.post(
        "/memory/propose", json=body, headers={"Authorization": f"Bearer {WRITE_KEY}", **headers}
    )


class TestTheAsyncAcceptIsWriteAhead:
    """202 and a trace id, with the work deferred. ADR-0003."""

    def test_it_returns_202_with_a_trace_id(self) -> None:
        """The trace id is the one handle that reaches the audit events, the decision
        and the job result, so a 202 without one would be an accept the caller cannot
        follow up."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": "patient:1", "turns": TURNS})

        assert response.status_code == 202
        assert response.json()["trace_id"].startswith("tr_")
        assert response.json()["status"] == "accepted"

    def test_async_is_the_default_mode(self) -> None:
        """The slow path should be the one you ask for, and the fast path is the one
        with the SLA a caller is most likely relying on."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            _post(client, {"namespace": "patient:1", "turns": TURNS})

        assert len(state.queue.jobs) == 1

    def test_the_job_carries_the_tenant_from_the_credential(self) -> None:
        """**The one place a tenant crosses a process boundary.**

        The worker has no request and no credential, so it takes the tenant from the
        job body. That makes this payload the thing to read when auditing isolation -
        and it must come from the principal, never from the request, which has no
        tenant field at all.
        """
        state = fake_state()

        with TestClient(app_with(state)) as client:
            _post(client, {"namespace": "patient:1", "turns": TURNS})

        _name, payload, _job_id, _queue = state.queue.jobs[0]
        assert payload["tenant_id"] == str(TENANT)

    def test_the_job_is_keyed_on_the_trace_so_a_retry_cannot_double_evaluate(self) -> None:
        """arq deduplicates on `_job_id`.

        The gateway returns 202 before the job runs, so a client that times out on the
        *response* may retry a request already queued. HTTP idempotency covers the
        caller's replay; this covers ours.
        """
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": "patient:1", "turns": TURNS})

        _name, _payload, job_id, queue = state.queue.jobs[0]
        assert job_id == response.json()["trace_id"]
        assert queue == QUEUE

    def test_the_queue_path_draws_the_full_sample(self) -> None:
        """K comes from `default_k`, not the 1 that `mode=strict` uses.

        The worker is not user-blocking, so it draws the sample `MEMORY_ENGINE.md` §1.2
        wants rather than the one-shot the inline path is limited to. A queue that
        evaluated at K=1 would be paying the latency of a queue for the measurement of
        an inline call.
        """
        state = fake_state()

        with TestClient(app_with(state)) as client:
            _post(client, {"namespace": "patient:1", "turns": TURNS})

        _name, payload, _job_id, _queue = state.queue.jobs[0]
        assert payload["k"] == state.settings.default_k


class TestTheTenantCannotBeNamedByTheCaller:
    """The property the whole isolation story rests on."""

    def test_a_tenant_field_in_the_body_is_rejected_or_ignored(self) -> None:
        """`ProposeRequest` has no tenant field, so pydantic either ignores it or
        refuses - and either way the job carries the credential's tenant.

        Asserted on the outcome rather than on the model, because "there is no such
        field" is a property a later commit could remove while every other test here
        still passed.
        """
        state = fake_state()
        attacker = "22222222-2222-4222-8222-222222222222"

        with TestClient(app_with(state)) as client:
            response = _post(
                client,
                {"namespace": "patient:1", "turns": TURNS, "tenant_id": attacker},
            )

        assert response.status_code in {202, 422}
        if state.queue.jobs:
            assert state.queue.jobs[0][1]["tenant_id"] == str(TENANT)


class TestWritesNeedTheWriteScope:
    """`memory:read` is not `memory:write`."""

    def test_a_read_only_credential_is_refused(self) -> None:
        """403, because the credential was valid - a 401 would make a client retry an
        authentication that already succeeded."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = client.post(
                "/memory/propose",
                json={"namespace": "patient:1", "turns": TURNS},
                headers={"Authorization": f"Bearer {READ_KEY}"},
            )

        assert response.status_code == 403
        assert not state.queue.jobs, "a refused write must not enqueue"


class TestStrictRefusesWithoutAModel:
    """`GatewayState.llm` is None when no provider is configured."""

    def test_strict_answers_503_rather_than_crashing(self) -> None:
        """A 500 would report a configuration problem as a fault, and the gateway
        deliberately starts without a model so that reads keep working."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": "patient:1", "turns": TURNS, "mode": "strict"})

        assert response.status_code == 503
        assert not state.queue.jobs, "strict must not fall back to the queue"


class TestIdempotency:
    """S8.3's store, with its first caller. ADR-0003 and S8.3."""

    def test_a_replay_returns_the_first_response_without_enqueueing_again(self) -> None:
        """S8.3's DONE WHEN, at last testable: "replaying the same request twice
        produces one assertion and two identical responses"."""
        state = fake_state()
        body = {"namespace": "patient:1", "turns": TURNS}

        with TestClient(app_with(state)) as client:
            first = _post(client, body, **{"Idempotency-Key": "k1"})
            second = _post(client, body, **{"Idempotency-Key": "k1"})

        assert first.json() == second.json()
        assert len(state.queue.jobs) == 1, "the replay must not enqueue a second job"

    def test_a_write_without_a_key_is_not_deduplicated(self) -> None:
        """Idempotency is opt-in. A caller that sent no key asked for no promise, and
        inventing one from the body hash would silently collapse two legitimate
        identical writes - which for an append-only memory is a lost fact."""
        state = fake_state()
        body = {"namespace": "patient:1", "turns": TURNS}

        with TestClient(app_with(state)) as client:
            _post(client, body)
            _post(client, body)

        assert len(state.queue.jobs) == 2

    def test_the_same_key_with_a_different_body_is_not_replayed(self) -> None:
        """Reusing one key for a different body is a client mistake, and replaying the
        first response would hide it."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            first = _post(
                client, {"namespace": "patient:1", "turns": TURNS}, **{"Idempotency-Key": "k1"}
            )
            second = _post(
                client, {"namespace": "patient:2", "turns": TURNS}, **{"Idempotency-Key": "k1"}
            )

        assert first.json()["trace_id"] != second.json()["trace_id"]
        assert len(state.queue.jobs) == 2


class TestTheRequestIsBounded:
    """`RULES.md` §2.2's position on input, not just on time."""

    def test_an_empty_turn_list_is_refused(self) -> None:
        """Nothing to extract from is a client error, not an empty accept."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": "patient:1", "turns": []})

        assert response.status_code == 422

    def test_too_many_turns_is_refused(self) -> None:
        """A proposal is extracted by a model, so an unbounded turn list is an
        unbounded prompt and an unbounded bill."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": "patient:1", "turns": TURNS * 201})

        assert response.status_code == 422

    @pytest.mark.parametrize("namespace", ["", "x" * 201])
    def test_a_missing_or_oversized_namespace_is_refused(self, namespace: str) -> None:
        """Required rather than defaulted: a default namespace is a write into
        somebody's primary memory that nobody asked for."""
        state = fake_state()

        with TestClient(app_with(state)) as client:
            response = _post(client, {"namespace": namespace, "turns": TURNS})

        assert response.status_code == 422
