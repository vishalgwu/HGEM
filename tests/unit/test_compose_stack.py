"""Enforces the three rules the dev datastore stack depends on.

`docs/RULES.md` opens by saying a rule that is not checkable by a linter, a
test, or a review gate is only a suggestion. All three rules below are stated in
the header of `infra/docker/docker-compose.dev.yml`, and all three are the kind
that rot quietly the moment somebody adds a service in a hurry.

**Images are pinned to an exact version.** `RULES.md` §3 pins model ids so that
replay is honest. A datastore is no different: if `make dev` can silently pull a
new major between two runs, then "it worked yesterday" stops being evidence of
anything. `latest` is the obvious offender, but a bare major like `pg16`,
`7-alpine` or `5-community` is the same problem wearing a number - those tags
move. The rule here is that the tag must carry at least a `MAJOR.MINOR`.

**Every service declares a healthcheck.** S1.3's acceptance check is that
`docker compose ps` reports all services healthy, and `make dev` runs
`up -d --wait`, which returns as soon as every service is *either* healthy or
has no healthcheck to fail. A service without one is therefore not merely
unmonitored - it silently satisfies the gate, and `--wait` returns while it is
still starting. The next command in S1.3 is a `psql` call that would then race
the database's first boot.

**Every published port is bound to loopback.** Compose publishes on `0.0.0.0`
when an entry names no host address, and the credentials in this stack are the
throwaway kind a local stack is entitled to - which makes the binding, not the
password, the thing standing between a laptop on a shared network and an
unauthenticated Redis. See :func:`test_every_published_port_is_bound_to_loopback`.

These apply to every `docker-compose*.yml` under `infra/docker/`, so the
`test` and `observability` stacks inherit them as they arrive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from conftest import REPO_ROOT

COMPOSE_DIR = REPO_ROOT / "infra" / "docker"

# A tag is pinned if it carries at least MAJOR.MINOR somewhere in it. That
# accepts `0.8.6-pg16`, `7.4-alpine`, `5.26.30-community` and `20.9.0`, and
# rejects `latest`, `pg16`, `7-alpine` and `5-community`.
_VERSIONED_TAG = re.compile(r"\d+\.\d+")


def _compose_files() -> list[Path]:
    return sorted(COMPOSE_DIR.glob("docker-compose*.yml"))


def _services(path: Path) -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    services: dict[str, dict[str, Any]] = document.get("services") or {}
    return services


def test_at_least_one_compose_file_exists() -> None:
    """Guard against the suite silently passing because it found nothing.

    Every assertion below iterates over discovered files, so an empty glob would
    make all of them vacuously true.
    """
    assert _compose_files(), (
        f"no docker-compose*.yml found under {COMPOSE_DIR.relative_to(REPO_ROOT)}. "
        "If the stack moved, update COMPOSE_DIR - do not delete the check."
    )


def test_every_image_is_pinned_to_an_exact_version() -> None:
    """No `latest`, and no bare-major tag either. Both move underneath you."""
    offenders: list[str] = []
    for path in _compose_files():
        for name, spec in _services(path).items():
            image = spec.get("image")
            if image is None:
                continue  # built from a Dockerfile, not pulled
            _, separator, tag = image.partition(":")
            if not separator:
                offenders.append(f"{path.name}: {name} -> {image} (no tag at all)")
            elif not _VERSIONED_TAG.search(tag):
                offenders.append(f"{path.name}: {name} -> {image}")

    assert not offenders, (
        "these images are not pinned to an exact version:\n"
        + "\n".join(f"  {entry}" for entry in offenders)
        + "\nA tag without MAJOR.MINOR moves. Pin it, and bump it in a commit "
        "that says why."
    )


def test_every_service_declares_a_healthcheck() -> None:
    """`make dev` uses `up -d --wait`, which trusts a missing healthcheck.

    A service with no healthcheck is treated as satisfied the moment it starts,
    so it passes the gate without ever being checked.
    """
    missing: list[str] = []
    for path in _compose_files():
        for name, spec in _services(path).items():
            if not spec.get("healthcheck"):
                missing.append(f"{path.name}: {name}")

    assert not missing, (
        "these services declare no healthcheck:\n"
        + "\n".join(f"  {entry}" for entry in missing)
        + "\n`docker compose up --wait` cannot wait on a service that never "
        "reports health - it returns while the service is still starting."
    )


# `${VAR:-default}` and `${VAR}`. Resolved to the default before an entry is
# split, because the `:-` inside the wrapper is a colon like any other and a
# naive split runs straight through it.
_SHELL_DEFAULT = re.compile(r"\$\{([^:}]+)(?::-([^}]*))?\}")


def _published(entry: Any) -> tuple[str, str]:
    """Split one `ports` entry into the host address it binds and the host port.

    Returns:
        ``(address, port)``, with any `${VAR:-default}` wrapper already resolved
        to its default. `address` is `""` when the entry names none, which is
        the case :func:`test_every_published_port_is_bound_to_loopback` exists
        to catch - Compose reads an absent address as `0.0.0.0`. `port` is `""`
        for a bare container port, which Compose assigns an ephemeral host port
        for and which therefore collides with nothing.

    The short form has three spellings and they cannot be told apart by counting
    from the left: `CONTAINER`, `HOST:CONTAINER` and `ADDRESS:HOST:CONTAINER`.
    The container port is always last, so the host port is the segment before it
    and the address is everything in front of that - rejoined rather than
    indexed, so a bare IPv6 address keeps its own colons. Reading segment 0 as
    the host port, which is what this helper replaced, returns `127.0.0.1` for
    every loopback-bound service and reports five collisions that do not exist.
    """
    if not isinstance(entry, str):
        # The long form is a mapping, and it carries both fields by name.
        return str(entry.get("host_ip", "")), str(entry.get("published", ""))
    segments = _SHELL_DEFAULT.sub(lambda m: m.group(2) or "", entry).split(":")
    if len(segments) == 1:
        return "", ""
    if len(segments) == 2:
        return "", segments[0]
    return ":".join(segments[:-2]), segments[-2]


def test_published_ports_do_not_collide_within_a_stack() -> None:
    """Two services publishing the same host port is a start-time failure.

    Cheaper to catch here than as an "address already in use" halfway through
    `make dev`.
    """
    clashes: list[str] = []
    for path in _compose_files():
        seen: dict[str, str] = {}
        for name, spec in _services(path).items():
            for entry in spec.get("ports") or []:
                default = _published(entry)[1]
                if not default:
                    continue
                if default in seen:
                    clashes.append(f"{path.name}: {default} claimed by {seen[default]} and {name}")
                else:
                    seen[default] = name

    assert not clashes, "host port collisions:\n" + "\n".join(f"  {c}" for c in clashes)


def test_every_published_port_is_bound_to_loopback() -> None:
    """A dev datastore stack must not be reachable from the network.

    Compose publishes on `0.0.0.0` when an entry names no host address, so the
    short `HOST:CONTAINER` form offers the whole stack to every machine that can
    route to this one - a conference network, a shared office subnet. These
    services hold the credentials a throwaway local stack is entitled to
    (`guardmem`/`guardmem`, `neo4j`/`guardmem123`, and a Redis with no password
    at all), and an unauthenticated Redis on a routable address is a remote code
    execution primitive rather than merely an exposed cache: `CONFIG SET dir`
    plus `CONFIG SET dbfilename` writes an attacker-chosen file as the container
    user.

    The rule is stated in the header of `docker-compose.dev.yml`, and per the
    module docstring above, a rule that is only stated is only a suggestion. A
    stack that genuinely needs to be reachable from another machine should say
    so in the entry rather than inherit it from an omission.
    """
    exposed: list[str] = []
    for path in _compose_files():
        for name, spec in _services(path).items():
            for entry in spec.get("ports") or []:
                address, port = _published(entry)
                if not port:
                    continue
                if address not in {"127.0.0.1", "::1", "localhost"}:
                    exposed.append(
                        f"{path.name}: {name} publishes {port} on "
                        f"{address or '0.0.0.0 - no address given'}"
                    )

    assert not exposed, (
        "published ports reachable off this machine:\n"
        + "\n".join(f"  {e}" for e in exposed)
        + "\nPrefix the mapping with `127.0.0.1:`; the ${VAR:-default} override "
        "is the host port and keeps working."
    )
