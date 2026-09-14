"""Guards the agreement between this repo's two dependency artifacts.

`docs/RULES.md` opens by saying that a rule which is not checkable by a linter,
a test, or a review gate is only a suggestion. This module is the test for a
rule that was previously only a comment.

The repo deliberately keeps two dependency descriptions, because they do
different jobs:

- ``pyproject.toml`` + ``uv.lock`` - authoritative for the workspace packages,
  and what ``uv sync`` installs. **This is what CI installs from.**
- ``requirements/*.txt`` + ``requirements.lock.txt`` - the human inventory,
  where every entry cites the build step or spec clause that requires it, and
  the plain pip-installable lock the README's cold start uses. **This is what a
  developer's machine installs from.**

Keeping both is a real choice with a real failure mode: they drift, and nothing
errors when they do - the two environments simply stop being the same one, and
the difference only shows up as a CI failure nobody can reproduce locally.

It has happened twice.

``testcontainers`` was pinned to 4.13.3 in ``requirements/dev.txt`` while
``uv.lock`` resolved 4.15.0 from a ``>=4.8`` floor, so the installed version
depended on which command ran last.

Then ``types-pyyaml`` was pinned in ``requirements/dev.txt`` and **not** in
``pyproject.toml``'s dev group, so it reached a developer's machine and never
reached ``uv.lock``. It went unnoticed until S1.7 widened ``make typecheck`` to
cover ``tests/``, at which point ``mypy --strict`` began failing in CI on
``tests/unit/test_compose_stack.py``'s ``import yaml`` - and passing locally,
where the stubs were installed. Six commits were pushed red.

The guard that should have caught it only checked one direction: dev-group
entries missing from ``requirements/dev.txt``. That is the harmless direction.
The harmful one is a package the developer has and CI does not, and it is now
checked too - see :func:`test_dev_group_mirrors_requirements_dev`.

Two invariants, then:

    1. the dev group and requirements/dev.txt pin the same set, exactly
    2. uv.lock is a version-consistent subset of requirements.lock.txt,
       *for the interpreter this project pins*

The qualification on (2) is not a loophole. ``uv.lock`` is a **universal** lock:
it carries entries for every interpreter its resolution markers cover, so
``libcst`` lists ``pyyaml-ft`` under ``python_full_version == '3.13.*'`` even
though this project pins 3.12 and can never install it. ``requirements.lock.txt``
is resolved for one interpreter and rightly omits it. Comparing the two without
evaluating markers reports that as drift, which is how a guard earns a reputation
for crying wolf and then gets relaxed.

When one of these fails, fix the pin - do not relax the test. The commands are
``uv lock`` after editing ``pyproject.toml``, and
``uv pip compile requirements-dev.txt -o requirements.lock.txt`` after editing
``requirements/``.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Final

from packaging.markers import Marker

from conftest import REPO_ROOT

UV_LOCK = REPO_ROOT / "uv.lock"
PIP_LOCK = REPO_ROOT / "requirements.lock.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEV_REQUIREMENTS = REPO_ROOT / "requirements" / "dev.txt"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"

# Workspace members are built from source in this repo, so they never appear in
# a lock of third-party packages. Comparing them would always fail.
# It is also the walk's starting frontier, so a member missing from here is one
# whose dependencies are never visited - the two locks then stop being compared
# over that subtree and the guard reports agreement it did not check.
# `guardmem-mcp` joined at S6.1 and brought `mcp` with it.
LOCAL_PACKAGES: Final = frozenset({"guardmem-core", "guardmem-mcp", "guardmem-workspace"})

# `Foo_Bar` and `foo-bar` are the same distribution to pip; normalise before
# comparing so a naming style difference is never reported as a drift.
_PIN = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([^\s;#]+)")


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


def _marker_environment() -> dict[str, str]:
    """A PEP 508 environment for the interpreter `.python-version` pins.

    Read from the file rather than from `sys.version_info`, so the guard
    describes the project's target rather than whatever happens to be running
    it. A developer on 3.13 must still get the same verdict as CI.
    """
    pinned = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()
    major_minor = ".".join(pinned.split(".")[:2])
    return {
        "python_version": major_minor,
        # `.0` is a placeholder patch: every marker in a lock file discriminates
        # on minor version at most, and `python_full_version` needs three parts
        # to compare correctly against a `== '3.13.*'` style specifier.
        "python_full_version": f"{major_minor}.0",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "extra": "",
    }


def _applies(marker: str | None, environment: dict[str, str]) -> bool:
    """Does a dependency edge apply to the pinned interpreter?"""
    if marker is None:
        return True
    return bool(Marker(marker).evaluate(environment))


def _uv_lock_versions() -> dict[str, str]:
    """Every uv.lock package installable on the pinned interpreter.

    Parsed as TOML, because `uv.lock` is TOML. The previous version of this
    function matched `name = "..."` immediately followed by `version = "..."`
    with a regex, which is true of the file today and is not a property the
    format guarantees.

    Reachability is walked from the root package's own dependencies, following
    only edges whose marker the pinned interpreter satisfies. That is what
    excludes `pyyaml-ft`, which `libcst` requires solely on 3.13.
    """
    data: dict[str, Any] = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    packages = {_normalise(entry["name"]): entry for entry in data["package"]}
    environment = _marker_environment()

    def edges(entry: dict[str, Any]) -> list[str]:
        found: list[str] = []
        groups: list[dict[str, Any]] = list(entry.get("dependencies", []))
        for extra in entry.get("optional-dependencies", {}).values():
            groups.extend(extra)
        for group in entry.get("dev-dependencies", {}).values():
            groups.extend(group)
        for dependency in groups:
            if _applies(dependency.get("marker"), environment):
                found.append(_normalise(dependency["name"]))
        return found

    reachable: set[str] = set()
    frontier = [name for name in LOCAL_PACKAGES if name in packages]
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in packages:
            continue
        reachable.add(name)
        frontier.extend(edges(packages[name]))

    return {
        name: packages[name]["version"]
        for name in reachable - LOCAL_PACKAGES
        if "version" in packages[name]
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
    """uv.lock must stay a subset of requirements.lock.txt on the pinned Python.

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


def test_dev_group_mirrors_requirements_dev() -> None:
    """The dev group and requirements/dev.txt must pin the same set, both ways.

    Two hand-maintained lists of one toolchain, and the direction that matters
    is the one this test used to omit. A package in `requirements/dev.txt` and
    not in the dev group is a package the developer has and **CI does not** -
    which is how `types-pyyaml` left six commits red while every local run was
    green. The reverse is merely undocumented.

    `pyproject.toml` states the intent in as many words: the dev group "carries
    EXACT pins mirroring requirements/dev.txt". Mirroring is symmetric.
    """
    declared = _dev_group_pins()
    documented = _parse_pinned(DEV_REQUIREMENTS.read_text(encoding="utf-8"))

    conflicts = {
        name: (version, documented[name])
        for name, version in declared.items()
        if name in documented and documented[name] != version
    }
    missing_from_lock = sorted(set(documented) - set(declared))
    missing_from_requirements = sorted(set(declared) - set(documented))

    assert not conflicts, (
        "pyproject.toml dev group and requirements/dev.txt disagree on:\n"
        + "\n".join(
            f"  {name}: pyproject={pin}, requirements/dev.txt={req}"
            for name, (pin, req) in sorted(conflicts.items())
        )
    )
    assert not missing_from_lock, (
        "requirements/dev.txt pins packages the dev group does not:\n"
        + "\n".join(f"  {name}=={documented[name]}" for name in missing_from_lock)
        + "\nThese reach a developer's machine and never reach uv.lock, so CI "
        "does not have them. Add them to [dependency-groups] dev and re-run "
        "`uv lock`."
    )
    assert not missing_from_requirements, (
        "dev group entries missing from requirements/dev.txt:\n"
        + "\n".join(f"  {name}" for name in missing_from_requirements)
        + "\nrequirements/dev.txt is where each package cites the step that "
        "requires it; a tool with no citation there is a tool nobody can justify."
    )


def test_the_marker_environment_matches_the_pinned_interpreter() -> None:
    """Pin the filter that keeps the subset check honest.

    If `_marker_environment` ever stopped reflecting `.python-version`, the
    subset test would quietly start comparing the wrong resolution - passing
    for packages that cannot install and failing for ones that can.
    """
    environment = _marker_environment()
    pinned = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()

    assert environment["python_version"] == ".".join(pinned.split(".")[:2])
    assert _applies("python_full_version != '3.13.*'", environment) is True
    assert _applies("python_full_version == '3.13.*'", environment) is False
    assert _applies(None, environment) is True


# ---------------------------------------------------------------------------
# The third invariant, added by the cleanup pass to S4.1: every dependency
# `guardmem-core` declares is either imported by it, or annotated with the step
# that will import it.
#
# Four of them are unused today - httpx, structlog, anyio, numpy - and all four
# are genuinely coming: RULES.md 2.2 and 6 name three of them and S5.1 needs the
# fourth. Keeping them is right. Keeping them *silently* is not, because the
# next person to run a dependency audit finds four unimported packages and
# cannot tell "not needed yet" from "no longer needed" - and removing the wrong
# one breaks a step that has not been written yet, which is the worst moment to
# discover it.
#
# So the comment block in `packages/guardmem-core/pyproject.toml` is the record,
# and this is what stops it rotting. It checks both directions: an undocumented
# unused dependency fails, and so does a documented one that has since started
# being imported, which is the half that goes stale on its own.
# ---------------------------------------------------------------------------

CORE_PYPROJECT = REPO_ROOT / "packages" / "guardmem-core" / "pyproject.toml"
CORE_SOURCE = REPO_ROOT / "packages" / "guardmem-core" / "src" / "guardmem_core"

# Distribution name -> the module it is imported as, where the two differ.
_IMPORT_NAME: Final = {"pydantic-settings": "pydantic_settings", "pyyaml": "yaml"}

# The heading that opens the annotated block in the core manifest.
_DEFERRED_HEADING: Final = "DECLARED AND NOT YET IMPORTED"


def _core_dependencies() -> list[str]:
    """Every distribution `guardmem-core` declares, normalised."""
    declared = tomllib.loads(CORE_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    return [_normalise(re.split(r"[><=!\[;]", spec)[0].strip()) for spec in declared]


def _imported_by_core() -> set[str]:
    """The subset of those distributions the package actually imports."""
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in CORE_SOURCE.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    found: set[str] = set()
    for name in _core_dependencies():
        module = _IMPORT_NAME.get(name, name.replace("-", "_"))
        if re.search(rf"^\s*(?:import {module}\b|from {module}[\s.])", source, re.M):
            found.add(name)
    return found


def _documented_as_deferred() -> set[str]:
    """Distributions named in the manifest's annotated block, with a step id.

    Read from the comment rather than from a second list, because a second list
    is one more thing to keep in step - and the comment is what a person
    actually reads when they wonder why `numpy` is there.
    """
    text = CORE_PYPROJECT.read_text(encoding="utf-8")
    block = text[text.index(_DEFERRED_HEADING) :].split('"httpx')[0]
    return {
        _normalise(match.group(1))
        for match in re.finditer(r"^\s*#\s+`([a-zA-Z0-9._-]+)`\s+S\d+\.\d+\s", block, re.M)
    }


def test_every_unused_core_dependency_is_documented_with_its_step() -> None:
    """An unimported dependency must say which step will import it."""
    undocumented = sorted(
        set(_core_dependencies()) - _imported_by_core() - _documented_as_deferred()
    )

    assert not undocumented, (
        f"guardmem-core declares {undocumented} and imports none of them. Either "
        "drop them, or add each to the 'DECLARED AND NOT YET IMPORTED' block in "
        "packages/guardmem-core/pyproject.toml with the step that will use it."
    )


def test_no_dependency_is_documented_as_deferred_once_it_is_used() -> None:
    """The half that goes stale on its own.

    A package listed as "arriving at S9.1" that S9.1 has since imported is a
    comment describing the past. Left alone it teaches the next reader that the
    block is unreliable, which is how the whole record stops being read.
    """
    stale = sorted(_documented_as_deferred() & _imported_by_core())

    assert not stale, (
        f"{stale} are imported now but still listed as deferred. Move them out "
        "of the 'DECLARED AND NOT YET IMPORTED' block in "
        "packages/guardmem-core/pyproject.toml."
    )
