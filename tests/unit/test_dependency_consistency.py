"""Guards the agreement between this repo's two dependency artifacts.

`docs/RULES.md` opens by saying that a rule which is not checkable by a linter,
a test, or a review gate is only a suggestion. This module is the test for a
rule that was previously only a comment.

The repo deliberately keeps two dependency descriptions, because they do
different jobs:

- ``pyproject.toml`` + ``uv.lock`` - authoritative for the workspace packages,
  and what ``uv run`` / ``uv sync`` install from.
- ``requirements/*.txt`` + ``requirements.lock.txt`` - the human inventory,
  where every entry cites the build step or spec clause that requires it, and
  the plain pip-installable lock the README's cold start uses.

Keeping both is a real choice with a real failure mode: they drift, and nothing
errors when they do. It happened. ``testcontainers`` was pinned to 4.13.3 in
``requirements/dev.txt`` while ``uv.lock`` resolved 4.15.0 from a ``>=4.8``
floor, so the installed version depended on whether ``uv run`` or
``uv pip install -r requirements.lock.txt`` ran last. The same mechanism nearly
swapped ``redis`` 5.3.1 for 8.1.0 and silently undid the ``arq`` pin behind it.

So the invariant these tests enforce is:

    uv.lock is a version-consistent SUBSET of requirements.lock.txt

Subset, not equality - ``requirements.lock.txt`` intentionally carries the full
runtime set (fastapi, presidio, phoenix and the rest) that the workspace itself
does not declare as a dependency. What is forbidden is disagreement on a shared
package, or a package appearing in ``uv.lock`` that the pip lock has never
heard of.

When one of these fails, fix the pin - do not relax the test. The commands are
``uv lock`` after editing ``pyproject.toml``, and
``uv pip compile requirements-dev.txt -o requirements.lock.txt`` after editing
``requirements/``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UV_LOCK = REPO_ROOT / "uv.lock"
PIP_LOCK = REPO_ROOT / "requirements.lock.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEV_REQUIREMENTS = REPO_ROOT / "requirements" / "dev.txt"

# Workspace members are built from source in this repo, so they never appear in
# a lock of third-party packages. Comparing them would always fail.
LOCAL_PACKAGES = frozenset({"guardmem-core", "guardmem-workspace"})

# `Foo_Bar` and `foo-bar` are the same distribution to pip; normalise before
# comparing so a naming style difference is never reported as a drift.
_NAME = re.compile(r"^([A-Za-z0-9._-]+)")
_PIN = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s;#]+)")
_UV_ENTRY = re.compile(r'^name = "([^"]+)"\nversion = "([^"]+)"', re.MULTILINE)


def _normalise(name: str) -> str:
    """Return the PEP 503 comparison form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_pinned(text: str) -> dict[str, str]:
    """Parse ``name==version`` lines, ignoring comments, extras and markers."""
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        if (match := _PIN.match(line)) is not None:
            pins[_normalise(match.group(1))] = match.group(2)
    return pins


def _uv_lock_versions() -> dict[str, str]:
    """Return ``{normalised name: version}`` for every package in uv.lock."""
    text = UV_LOCK.read_text(encoding="utf-8")
    return {
        _normalise(name): version
        for name, version in _UV_ENTRY.findall(text)
        if _normalise(name) not in LOCAL_PACKAGES
    }


def _dev_group_pins() -> dict[str, str]:
    """Return the exact pins declared in ``[dependency-groups] dev``."""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    entries: list[str] = data["dependency-groups"]["dev"]
    return _parse_pinned("\n".join(entries))


def test_uv_lock_agrees_with_requirements_lock() -> None:
    """No package may resolve to different versions in the two locks.

    A disagreement here means the installed version depends on which command
    ran last, with no error either way - the exact failure this file exists to
    prevent.
    """
    uv_versions = _uv_lock_versions()
    pip_versions = _parse_pinned(PIP_LOCK.read_text(encoding="utf-8"))

    conflicts = {
        name: (version, pip_versions[name])
        for name, version in uv_versions.items()
        if name in pip_versions and pip_versions[name] != version
    }

    assert not conflicts, "uv.lock and requirements.lock.txt disagree on:\n" + "\n".join(
        f"  {name}: uv.lock={uv_ver}, requirements.lock.txt={pip_ver}"
        for name, (uv_ver, pip_ver) in sorted(conflicts.items())
    )


def test_uv_lock_introduces_no_package_the_pip_lock_lacks() -> None:
    """uv.lock must stay a subset of requirements.lock.txt.

    A package here but not there is one that `uv sync` would install and the
    README's install path would not, which is how the two environments diverge
    without anything failing. `testcontainers[redis]` pulling redis 8.1.0 past
    the deliberate 5.3.1 pin was caught exactly this way.
    """
    uv_versions = _uv_lock_versions()
    pip_versions = _parse_pinned(PIP_LOCK.read_text(encoding="utf-8"))

    orphans = sorted(set(uv_versions) - set(pip_versions))

    assert not orphans, (
        "uv.lock contains packages absent from requirements.lock.txt:\n"
        + "\n".join(f"  {name}=={uv_versions[name]}" for name in orphans)
        + "\nEither add them to the right requirements/ layer and recompile the "
        "pip lock, or stop declaring them in pyproject.toml."
    )


def test_dev_group_is_pinned_exactly() -> None:
    """Every `[dependency-groups] dev` entry must be an exact `==` pin.

    Floors are what let uv.lock drift away from requirements/dev.txt in the
    first place: `testcontainers>=4.8` resolved 4.15.0 against a 4.13.3 pin.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    entries: list[str] = data["dependency-groups"]["dev"]

    unpinned = [entry for entry in entries if "==" not in entry]

    assert not unpinned, "dev dependency group entries must use `==`, found:\n" + "\n".join(
        f"  {entry}" for entry in unpinned
    )


def test_dev_group_matches_requirements_dev() -> None:
    """The dev group and requirements/dev.txt must pin the same versions.

    These are two hand-maintained lists of the same toolchain. Bumping one and
    not the other is the drift; this is the check that makes it loud.
    """
    declared = _dev_group_pins()
    documented = _parse_pinned(DEV_REQUIREMENTS.read_text(encoding="utf-8"))

    conflicts = {
        name: (version, documented[name])
        for name, version in declared.items()
        if name in documented and documented[name] != version
    }
    undocumented = sorted(set(declared) - set(documented))

    assert not conflicts, (
        "pyproject.toml dev group and requirements/dev.txt disagree on:\n"
        + "\n".join(
            f"  {name}: pyproject={pin}, requirements/dev.txt={req}"
            for name, (pin, req) in sorted(conflicts.items())
        )
    )
    assert not undocumented, (
        "dev group entries missing from requirements/dev.txt:\n"
        + "\n".join(f"  {name}" for name in undocumented)
        + "\nrequirements/dev.txt is where each package cites the step that "
        "requires it; a tool with no citation there is a tool nobody can justify."
    )
