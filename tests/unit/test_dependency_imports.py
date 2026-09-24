"""Every third-party module this repo imports must be installable in CI.

`test_dependency_consistency.py` guards the agreement between the two
dependency artifacts. This module guards something the two locks agreeing
cannot tell you: that the packages the **source actually imports** are among
the ones CI installs. It is a separate module rather than a fourth section over
there because that file sits at the `RULES.md` §2.4 cap.

**The gap this closes, and the failure that found it.** The existing guard's
second invariant is that `uv.lock` is a *subset* of `requirements.lock.txt`. A
subset check is asymmetric by construction, and the direction it permits is the
harmful one: a package the developer has and CI does not. That direction was
closed for the dev toolchain by `test_dev_group_mirrors_requirements_dev`, and
left open everywhere else.

S7.1 walked straight into it. `neo4j` was added to `requirements/stores.txt`
and compiled into `requirements.lock.txt`, so every local gate passed; it was
never added to `packages/guardmem-core/pyproject.toml`, so it never reached
`uv.lock`, which is the only thing CI installs from. All three CI jobs failed
on `Cannot find implementation or library stub for module named "neo4j"` while
`make lint typecheck test` was green locally. The manifest comment even named
the step - "Neo4j arrives at S7.1 [...] and is NOT listed here" - and S7.1 did
not move the line.

That is the third failure of this shape in this repository, which is the
argument for checking the property directly rather than adding a third mirror
between hand-maintained lists. An import is not a declaration one can forget to
mirror: it is what the code does.

**Why this asks `uv.lock` rather than the manifests.** The question worth
answering is "can CI import this?", and the only artifact that answers it is
the lock CI installs from - including transitive edges. A package can be
importable because something else depends on it, and that is a legitimate way
to satisfy an import even though it is a fragile one to rely on deliberately.
"""

from __future__ import annotations

import ast
import re
import sys
from importlib.metadata import packages_distributions
from typing import Final

from test_dependency_consistency import _normalise, _uv_lock_versions

from conftest import REPO_ROOT

# The five trees `make typecheck` covers, which is the definition of "source CI
# has to be able to import". Keeping this list identical to the Makefile's is the
# point: a tree mypy checks and this does not is a tree where an undeclared
# import is invisible again. `services/gateway/src` joined at S8.1, and its
# absence here made `gateway` read as an undeclared third-party import - which is
# this guard working, one tree late.
SOURCE_TREES: Final = (
    REPO_ROOT / "packages" / "guardmem-core" / "src",
    REPO_ROOT / "services" / "mcp_server" / "src",
    REPO_ROOT / "services" / "gateway" / "src",
    REPO_ROOT / "tests",
    REPO_ROOT / "scripts",
)


def _local_module_names() -> frozenset[str]:
    """Every module name that resolves inside this repository.

    Derived rather than listed, because a hand-written list is the thing that
    goes stale: it would have to grow a line for every new test module, and a
    missing line reports first-party code as an undeclared dependency.

    **This follows `sys.path` semantics rather than taking every file's stem,
    and the difference is load-bearing.** A file is importable under its own
    name only when its directory is not itself a package; inside a package the
    top-level name is the outermost package instead. `tests/fixtures/neo4j.py`
    is the case that proves it - `tests/fixtures/__init__.py` exists, so that
    file is `fixtures.neo4j` and contributes `fixtures`, **not** `neo4j`. The
    first version of this function used bare stems and therefore classified
    `neo4j` as first-party, which silently swallowed the exact import this
    module was written to catch.
    """
    names: set[str] = set()
    for tree in SOURCE_TREES:
        for path in tree.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            outermost = path
            while (outermost.parent / "__init__.py").exists():
                outermost = outermost.parent
            names.add(outermost.stem if outermost.is_file() else outermost.name)
    return frozenset(names)


# `mcp_types` is the one first-party name no file spells as its own module:
# `services/mcp_server` re-exports it, so it is imported but never defined as a
# module of that name inside the scanned trees.
EXTRA_FIRST_PARTY: Final = frozenset({"mcp_types"})

# Module name -> distribution, where `packages_distributions()` cannot help
# because the distribution supplies only type stubs and installs no importable
# package of that name. `mypy` still needs them, which is exactly why they are
# pinned, so they must not be reported as undeclared.
STUB_ONLY: Final = {
    "yaml": "types-pyyaml",
    "networkx": "types-networkx",
    "jsonschema": "types-jsonschema",
    "asyncpg": "asyncpg-stubs",
}


def _top_level_imports(tree: ast.Module) -> set[str]:
    """Every top-level module name imported by one parsed file.

    Relative imports (`from .base import ...`) carry `level > 0` and name
    nothing installable, so they are skipped rather than resolved.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _imported_modules() -> dict[str, set[str]]:
    """Third-party module name -> the repo-relative files importing it."""
    first_party = _local_module_names() | EXTRA_FIRST_PARTY
    importers: dict[str, set[str]] = {}
    for tree in SOURCE_TREES:
        for path in tree.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for module in _top_level_imports(parsed):
                if module in sys.stdlib_module_names or module in first_party:
                    continue
                importers.setdefault(module, set()).add(path.relative_to(REPO_ROOT).as_posix())
    return importers


def _candidate_distributions(module: str) -> set[str]:
    """The distribution names that could satisfy `import <module>`.

    A set rather than one name because a namespace package can be supplied by
    several distributions, and satisfying the import with any of them is
    enough. `packages_distributions()` reads the *running* environment, which
    is the developer's - the one that has the package CI is missing - so the
    mapping is available exactly when it is needed. The module name itself is
    the fallback, which is what makes a package absent from both environments
    (the `neo4j` case) still resolve to something checkable rather than
    silently skipped.
    """
    if module in STUB_ONLY:
        return {_normalise(STUB_ONLY[module])}
    installed = packages_distributions().get(module)
    if installed:
        return {_normalise(name) for name in installed}
    return {_normalise(module)}


def test_every_imported_third_party_module_is_in_uv_lock() -> None:
    """The property CI actually needs, checked directly.

    `mypy --strict` reports this as `Cannot find implementation or library stub
    for module named "x"` and `pytest` as a collection error - both of them in
    CI only, and neither reproducible locally, because a developer installs a
    different and larger package set. Failing here moves that discovery from a
    red badge to a local test run.
    """
    available = set(_uv_lock_versions())
    importers = _imported_modules()

    missing = {
        module: sorted(files)
        for module, files in importers.items()
        if not (_candidate_distributions(module) & available)
    }

    assert not missing, (
        "these modules are imported by this repo and cannot be installed from "
        "uv.lock, so CI cannot import them:\n"
        + "\n".join(
            f"  {module} - imported by {', '.join(files[:3])}"
            + (f" (+{len(files) - 3} more)" if len(files) > 3 else "")
            for module, files in sorted(missing.items())
        )
        + "\nDeclare each in the pyproject.toml of the package that imports it "
        "(or in [dependency-groups] dev for a tool), mirror it into the right "
        "requirements/ layer, then run `uv lock`."
    )


def test_the_scan_reaches_every_tree_make_typecheck_covers() -> None:
    """Pin the scan's own scope.

    A guard that silently stopped reading a directory would pass forever. This
    reads the Makefile's `typecheck` recipe and asserts the trees agree, so
    widening one and not the other fails rather than shrinking the check.
    """
    recipe = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    line = next(raw for raw in recipe.splitlines() if "mypy" in raw and "$(CORE_SRC)" in raw)
    variables = {
        name: value.strip()
        for name, value in re.findall(r"^([A-Z_]+)\s*[:?]?=\s*(.+)$", recipe, re.M)
    }
    for variable, value in variables.items():
        line = line.replace(f"$({variable})", value)

    checked = {
        (REPO_ROOT / token).resolve()
        for token in line.split()
        if (not token.startswith("$") and "/" in token) or token in {"tests", "scripts"}
    }

    assert {tree.resolve() for tree in SOURCE_TREES} <= checked, (
        "SOURCE_TREES has drifted from the Makefile's typecheck target; a tree "
        "mypy checks and this guard does not is one where an undeclared import "
        "is invisible."
    )


def test_the_scan_finds_the_imports_it_is_supposed_to_find() -> None:
    """The control.

    Every assertion above passes trivially if `_imported_modules` returns
    nothing - a broken walk, an exclusion that swallowed everything, a rename.
    These are three imports from three different trees that must be found.
    """
    importers = _imported_modules()

    assert "pydantic" in importers, "guardmem-core imports pydantic"
    assert "neo4j" in importers, "S7.1's driver, the import this module exists for"
    assert "pytest" in importers, "the test tree imports pytest"
    assert "os" not in importers, "stdlib must be excluded"
    assert "guardmem_core" not in importers, "first-party packages must be excluded"
    assert "test_dependency_consistency" not in importers, (
        "a sibling test module is first-party; this module imports one, and the "
        "first version of this guard reported it as an undeclared dependency"
    )
    assert "fixtures" not in importers, "tests/fixtures is a package, so it is first-party"
