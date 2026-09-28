"""Every core dependency is imported, or documented as coming.  S4.1 cleanup

Split from `test_dependency_consistency.py` on 2026-09-26, when that module passed
`RULES.md` 2.4's 400-line cap as S8.4 added a third workspace member. The seam was
already marked in the source: that file guards the agreement between **two lockfiles**,
and everything here guards one **pyproject comment block** against rot. They share a
repo root and nothing else.

Kept as a test rather than a convention because the failure is silent. Two of
`guardmem-core`'s dependencies are unused today and both are genuinely coming; the
next person to run a dependency audit finds unimported packages and cannot tell
"not needed yet" from "no longer needed", and removing the wrong one breaks a step
nobody has written.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]


def _normalise(name: str) -> str:
    """Return the PEP 503 comparison form of a distribution name.

    Args:
        name: A distribution name as written in a pyproject or a lock.

    Returns:
        The comparison form - `Foo_Bar` and `foo-bar` are one distribution to pip.

    Copied from `test_dependency_consistency` rather than imported, and the copy is
    three lines. Importing between test modules would make this file fail when that
    one is refactored, for a helper that is a single `re.sub` of a PEP 503 rule that
    does not change.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# The third invariant, added by the cleanup pass to S4.1: every dependency
# `guardmem-core` declares is either imported by it, or annotated with the step
# that will import it.
#
# Four were unused when this was written - httpx, structlog, anyio, numpy - and
# S9.1 and S5.1 have since imported httpx and numpy. Keeping the rest is right.
# Keeping them *silently* is not, because the next person to run a dependency
# audit finds unimported packages and cannot tell "not needed yet" from "no
# longer needed" - and removing the wrong one breaks a step that has not been
# written yet, which is the worst moment to discover it.
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
