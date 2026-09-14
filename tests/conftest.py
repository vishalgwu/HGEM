"""Shared test configuration and paths.

Five test modules had grown their own copy of::

    REPO_ROOT = Path(__file__).resolve().parents[2]

which is duplication, but the reason to remove it is the `parents[2]`. That
index silently encodes "this file lives exactly two directories below the
root". A module moved from `tests/unit/` to `tests/` keeps working *and starts
pointing at the parent of the repository*, where a `.secrets.baseline` or a
`pyproject.toml` may well exist and be the wrong one. Nothing fails; the test
just quietly checks somebody else's file.

Resolving by marker instead makes the answer independent of where the caller
sits, and asserting the marker exists turns a wrong answer into a loud one.

Importing this from a test module works because pytest puts the directory
containing `conftest.py` on `sys.path`. That is the one piece of pytest
machinery being relied on here, and it is what makes `tests/conftest.py` the
conventional home for shared test code - `PROJECT_TREE.md` lists it for exactly
that.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import guardmem_core
from guardmem_core.schemas import GMModel

# The file that marks the workspace root. Chosen because it is the thing that
# defines the root - uv, ruff, mypy, pytest and coverage all resolve against it.
_ROOT_MARKER = "pyproject.toml"

# Postgres fixtures, registered as a plugin rather than as a second `conftest.py`
# under `tests/integration/`. Two `conftest` modules in a tree without
# `__init__.py` collide under `mypy`, which would take the whole test suite out
# of `make typecheck`. Nothing here starts a container until a test asks for the
# fixture, so the unit suite is unaffected. S3.2.
pytest_plugins = (
    "fixtures.postgres",
    "fixtures.pgvector",
    "fixtures.outbox",
    "fixtures.seed",
)


def _find_repo_root() -> Path:
    """Walk upwards until the workspace root is found.

    Returns:
        The directory containing the root `pyproject.toml`.

    Raises:
        RuntimeError: if no ancestor contains it, which means the test tree has
            been moved somewhere the rest of the tooling would not work either.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    raise RuntimeError(
        f"no {_ROOT_MARKER} found above {__file__}; the test tree is not inside the workspace"
    )


REPO_ROOT = _find_repo_root()


def _import_every_guardmem_module() -> None:
    """Import all of `guardmem_core`, so the subclass walk below can be complete.

    `__subclasses__()` only sees classes that have actually been imported, which
    made the original version of `all_schema_models()` quietly dependent on what
    each test module happened to pull in. S1.7 proved it: `LLMResponse` is a
    `GMModel` living in `guardmem_core.llm.base`, no test imported that module,
    and every "every model is covered" guard passed while not covering it.

    Importing the package tree first makes the answer independent of import
    order. It is safe to do at collection time only because S1.4 made settings
    construction lazy - a module-scope `Settings()` would make this line fail on
    any machine without a populated `.env`, CI included.
    """
    for module in pkgutil.walk_packages(
        guardmem_core.__path__, prefix=f"{guardmem_core.__name__}."
    ):
        importlib.import_module(module.name)


def all_schema_models() -> set[type[GMModel]]:
    """Every concrete schema in the package, wherever it is defined.

    Shared by the property suite, which checks that each one has a generative
    strategy, and by the unit suite, which checks that each one defined under
    `guardmem_core.schemas` is exported from it. Both are drift guards, and
    walking the same function means a schema cannot be invisible to one and not
    the other.

    The walk is recursive rather than a single `__subclasses__()` call: a model
    that subclasses another model - a refinement of `StoredAssertion`, say -
    would not be a direct child of `GMModel`, and a test that looked exhaustive
    would silently stop covering it.

    Returns:
        Every `GMModel` subclass, excluding `GMModel` itself, which declares no
        fields and is configuration rather than a schema.
    """
    _import_every_guardmem_module()

    def walk(cls: type[GMModel]) -> list[type[GMModel]]:
        return [cls, *(found for sub in cls.__subclasses__() for found in walk(sub))]

    return set(walk(GMModel)) - {GMModel}
