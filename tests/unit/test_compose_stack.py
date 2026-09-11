"""Enforces the two rules the dev datastore stack depends on.

`docs/RULES.md` opens by saying a rule that is not checkable by a linter, a
test, or a review gate is only a suggestion. Both rules below are stated in the
header of `infra/docker/docker-compose.dev.yml`, and both are the kind that rot
quietly the moment somebody adds a service in a hurry.

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

These apply to every `docker-compose*.yml` under `infra/docker/`, so the
`test` and `observability` stacks inherit them as they arrive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
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
                # Only the short "HOST:CONTAINER" string form is used here; the
                # long form is a mapping and carries `published` instead.
                published = (
                    str(entry).split(":")[0]
                    if isinstance(entry, str)
                    else str(entry.get("published", ""))
                )
                # Strip the ${VAR:-default} wrapper down to the default.
                default = re.sub(r"^\$\{[^:]+:-([^}]+)\}$", r"\1", published)
                if default in seen:
                    clashes.append(f"{path.name}: {default} claimed by {seen[default]} and {name}")
                else:
                    seen[default] = name

    assert not clashes, "host port collisions:\n" + "\n".join(f"  {c}" for c in clashes)
