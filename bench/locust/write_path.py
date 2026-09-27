"""The write path under load.  BUILD_NOTEBOOK.md S8.4

S8.4's DONE WHEN: "locust run at 50 rps shows p95 < 80 ms on the async propose
endpoint."

**What this measures, and what it deliberately does not.** `ADR-0003` is write-ahead
accept: the async path validates, checks idempotency, hashes, enqueues and returns 202.
The governing happens on the worker. So a p95 here covers authentication, the
rate-limit round trip to Redis, the enqueue round trip to Redis, and serialisation -
and covers no model call and no Postgres write at all. That is the point of the number:
it is the latency a caller sees for a promise, not for a decision.

Run it against a gateway that is already up, because starting one is a deployment
concern and `make dev` already owns the datastores:

    uv run uvicorn gateway.main:app --port 8000
    uv run locust -f bench/locust/write_path.py --headless \\
        -u 50 -r 10 -t 30s --host http://127.0.0.1:8000

`-u 50` with `constant_throughput(1)` is 50 rps by construction - see `ProposeAsync`
on why the pacing has to be explicit rather than left to saturation. The rate limiter
will refuse the run unless `GM_RATE_LIMIT_PER_MINUTE` is raised for it; a 429 is
cheaper than the work being measured, so `catch_response` fails those samples rather
than letting them flatter the p95.

**A credential is required and is not in this file.** `GM_GATEWAY_API_KEYS` configures
the gateway and `GM_BENCH_API_KEY` tells this script which of them to present. A key
committed to a bench script is a key in the repository, and `gitleaks` would be right
to fail it.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any, Final

from locust import HttpUser, constant_throughput, events, task

if TYPE_CHECKING:
    from locust.env import Environment

# Read once. A missing key is a configuration mistake and should stop the run before it
# reports the latency of 401s, which are fast.
_API_KEY: Final = os.environ.get("GM_BENCH_API_KEY", "")

# One namespace for the whole run. Distinct namespaces would be more realistic and
# would also mean the enqueue is the only thing being measured *and* that nothing
# contends - and contention on one namespace is the honest shape, because a real tenant
# writes to a handful of subjects rather than to thousands.
_NAMESPACE: Final = "bench:write-path"

_TURNS: Final = [
    {
        "turn_id": "t01",
        "role": "user",
        "text": "Ramipril 5mg each morning. Been on it about two years.",
        "captured_at": "2026-02-03T09:15:00+00:00",
    },
    {
        "turn_id": "t02",
        "role": "user",
        "text": "Penicillin allergy - hives as a child.",
        "captured_at": "2026-02-03T09:15:00+00:00",
    },
]


@events.test_start.add_listener
def _check_budget(environment: Environment, **_kwargs: Any) -> None:
    """Refuse to start without a credential.

    Args:
        environment: locust's run environment, used to stop the run.
        _kwargs: locust passes more than this needs.

    **Stops the run rather than letting it report.** Without a key every request is a
    401, and a 401 is cheaper than the work being measured - so the run would finish
    with an excellent p95 that describes the cost of rejecting a request. A benchmark
    that can produce a good number for the wrong reason is worse than one that refuses.
    """
    if not _API_KEY:
        environment.runner.quit() if environment.runner else None
        msg = (
            "GM_BENCH_API_KEY is empty. Set it to a key present in the gateway's "
            "GM_GATEWAY_API_KEYS, with the memory:write scope. Without it every "
            "request is a 401 and the p95 measures rejection."
        )
        raise RuntimeError(msg)


class ProposeAsync(HttpUser):
    """A caller submitting proposals on the write-ahead path.

    **`constant_throughput`, not `between`.** The target is "p95 < 80 ms **at 50 rps**",
    and those two numbers are not independent: with unpaced users locust offers as much
    load as the server will take, the server saturates, and the reported latency is
    mostly queue wait. Measured that way this endpoint reported a p95 of 580 ms at 124
    rps - which is a true statement about saturation and says nothing about the target.

    `constant_throughput(1)` paces each user at one request per second, so `-u 50` is
    50 rps by construction and the percentile describes service time at that offered
    load. `GM_BENCH_RPS_PER_USER` exists to find the saturation point deliberately
    rather than by accident.
    """

    wait_time = constant_throughput(float(os.environ.get("GM_BENCH_RPS_PER_USER", "1")))

    @task
    def propose(self) -> None:
        """One async accept.

        `name` is set so locust aggregates every call under one row. Without it the URL
        is the row and this endpoint has no path parameters, so it would be the same -
        but naming it means the report reads as the thing being measured rather than as
        a path.

        **`catch_response` and an explicit failure for anything but 202.** By default
        locust counts any 2xx-4xx as a success unless it raises, so a run where the
        rate limiter refused everything would report 100% success at excellent latency.
        A 429 is a failed sample here, which is what makes the number trustworthy.
        """
        with self.client.post(
            "/memory/propose",
            json={"namespace": _NAMESPACE, "turns": _TURNS, "mode": "async"},
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                # A fresh key per request. Reusing one would exercise the idempotency
                # *replay* path, which skips the enqueue and is therefore faster -
                # measuring the cache rather than the accept.
                "Idempotency-Key": uuid.uuid4().hex,
            },
            name="POST /memory/propose (async)",
            catch_response=True,
        ) as response:
            if response.status_code == 202:
                response.success()
            elif response.status_code == 429:
                response.failure("rate limited - raise GM_RATE_LIMIT_PER_MINUTE for the run")
            else:
                response.failure(f"expected 202, got {response.status_code}")
