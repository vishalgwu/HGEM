# mypy: disable-error-code="untyped-decorator"
#
# `locust` ships no annotations, so `@events.*.add_listener` is untyped and
# `strict` calls anything it wraps untyped too. Disabled for this file rather than
# configured for a `bench.*` module path, because `bench/` has no `__init__.py` and
# mypy therefore names the module `write_path` - an override keyed on a filename is
# an override that stops applying the day the file moves.
#
# Narrow on purpose: every other strict check still runs here. The listener bodies
# decide whether CI's load gate passes, so they are exactly the code to keep checked.
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
import pathlib
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

# How far below the target rps a run may land and still count. Not 1.0: `constant_throughput`
# paces per user, so the achieved rate always lands a little under the nominal one, and a
# gate at exactly the target would fail on arithmetic. 0.9 is loose enough for that and
# tight enough that a server which has lost half its throughput fails.
_RPS_TOLERANCE: Final = 0.9

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


@events.quitting.add_listener
def _enforce_budget(environment: Environment, **_kwargs: Any) -> None:
    """Fail the run when the p95 is over budget, or any request failed.

    Args:
        environment: locust's run environment. `process_exit_code` is what turns a
            report into a non-zero exit, which is the only thing CI reads.
        _kwargs: locust passes more than this needs.

    **Two conditions, and the failure one matters as much as the latency.** A run where
    the rate limiter refused everything would post an excellent p95 - a 429 is cheaper
    than the work - so a single failed sample fails the run. `catch_response` in the
    task is what marks them.

    **The budget is `p95_ci_ms`, not `p95_ms`.** The second is a production SLA on real
    hardware; this runs on two shared vCPUs with Redis in a container beside it. See
    `bench/profiles/p95_targets.yaml` on why a gate at the SLA would be switched off
    within a week, and what this one is actually for.

    **Two budgets, and the median is the one that discriminates.** The p95's run-to-run
    spread on one machine was 260-340 ms against a broken-state 440, so a p95 gate tight
    enough to catch the regression would sit inside its own noise. The median moved
    130 -> 330 ms for the same regression with far less spread. The p95 stays as an outer
    rail; the p50 is the detector. The profile carries the numbers and the argument.

    `GM_BENCH_P95_MS` and `GM_BENCH_P50_MS` override them, for measuring on a machine
    worth measuring on.
    """
    stats = environment.stats.total
    budget_ms = float(os.environ.get("GM_BENCH_P95_MS", _ci_budget_ms()))
    p95 = stats.get_response_time_percentile(0.95) or 0.0
    p50 = stats.get_response_time_percentile(0.50) or 0.0
    print(
        f"\n[bench] {stats.num_requests} requests, {stats.num_failures} failed, "
        f"{stats.total_rps:.1f} rps | p50={p50:.0f}ms p95={p95:.0f}ms "
        f"| budgets p50<{_profile()['p50_ci_ms']:.0f}ms p95<{budget_ms:.0f}ms"
    )
    # **A run that measured nothing must not pass, and the first version of this did.**
    # With no credential the `test_start` guard stops the run before any request is
    # made; `num_requests` was then 0, `p95` was 0, and 0 is under every budget - so it
    # exited 0 and reported success. That is the exact shape this file warns about
    # elsewhere: a benchmark that can produce a good number for the wrong reason.
    if not stats.num_requests:
        print("[bench] FAIL: no requests were made, so nothing was measured")
        environment.process_exit_code = 1
        return
    # And a run that could not sustain the offered load has not measured the target
    # either. `constant_throughput` paces the users, so a shortfall means the server
    # could not keep up - which is a latency regression showing up as throughput.
    floor = _target_rps() * _RPS_TOLERANCE
    if stats.total_rps < floor:
        print(
            f"[bench] FAIL: sustained {stats.total_rps:.1f} rps against a "
            f"{_target_rps():.0f} rps target (floor {floor:.1f})"
        )
        environment.process_exit_code = 1
        return
    if stats.num_failures:
        print(f"[bench] FAIL: {stats.num_failures} requests failed")
        environment.process_exit_code = 1
        return
    # The median first, because it is the one that discriminates - see the profile on
    # why a p95 gate tight enough to catch the regression would sit inside the p95's own
    # run-to-run spread.
    p50_budget_ms = float(os.environ.get("GM_BENCH_P50_MS", _profile()["p50_ci_ms"]))
    if p50 > p50_budget_ms:
        print(f"[bench] FAIL: p50 {p50:.0f}ms exceeds the {p50_budget_ms:.0f}ms budget")
        environment.process_exit_code = 1
        return
    if p95 > budget_ms:
        print(f"[bench] FAIL: p95 {p95:.0f}ms exceeds the {budget_ms:.0f}ms budget")
        environment.process_exit_code = 1
        return
    environment.process_exit_code = 0


def _target_rps() -> float:
    """The offered load this run is supposed to sustain.

    Returns:
        `targets.propose_async.rps` from the profile.
    """
    return float(_profile()["rps"])


def _ci_budget_ms() -> float:
    """The p95 ceiling this run is gated on.

    Returns:
        `targets.propose_async.p95_ci_ms` from the profile.

    Read from the profile rather than duplicated here, because `PRD.md` 6.1 owns the
    real target and this file owning a copy of *any* of these numbers is how the two
    drift. A missing profile is an error rather than a default: a run that silently
    invented its own budget would report a pass nobody set.
    """
    return float(_profile()["p95_ci_ms"])


def _profile() -> dict[str, float]:
    """The `propose_async` targets, from the profile on disk.

    Returns:
        That mapping.

    Raises:
        KeyError: the profile lacks the section. An error rather than a default,
            because a run that silently invented its own budget would report a pass
            nobody set.
    """
    import yaml

    path = pathlib.Path(__file__).resolve().parents[1] / "profiles" / "p95_targets.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    section: dict[str, float] = loaded["targets"]["propose_async"]
    return section
