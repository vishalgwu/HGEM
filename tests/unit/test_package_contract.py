"""Structural contract tests for the `guardmem_core` package.

These assert the architectural invariant from `docs/PROJECT_TREE.md`
("Ownership & Dependency Rules") at *runtime*, complementing the static
`import-linter` contract in `pyproject.toml`. The two catch different things:

- `lint-imports` reads the source tree and fails on a written `import fastapi`,
  including in a module nothing imports yet.
- These tests observe what is actually loaded into `sys.modules`, so they also
  catch a web framework pulled in indirectly by a third-party dependency.

`ARCHITECTURE.md` §2.2 is the reason this matters: `guardmem_core.pipeline` has
to run unchanged in the Gateway, the Worker and the eval harness, which is only
true while the package carries no server framework.
"""

from __future__ import annotations

import subprocess
import sys

# Frameworks the core engine must never depend on. Keep in sync with the
# `forbidden_modules` list of the import-linter contract in pyproject.toml.
FORBIDDEN_FRAMEWORKS: tuple[str, ...] = (
    "fastapi",
    "starlette",
    "uvicorn",
    "mcp",
    "arq",
)


def test_package_imports() -> None:
    """The package is installed and importable.

    This is the acceptance check for BUILD_NOTEBOOK.md S1.1. It fails on a
    fresh clone where `packages/guardmem-core` was never installed, which is
    exactly the onboarding break it exists to catch.
    """
    import guardmem_core

    assert guardmem_core.__doc__, "the package should carry a module docstring"


def test_importing_core_does_not_load_a_web_framework() -> None:
    """Importing `guardmem_core` must not drag in a server framework.

    Run in a subprocess with an isolated interpreter so the result reflects
    only what `import guardmem_core` loads. Asserting against the ambient
    `sys.modules` would be meaningless here: the test session itself has
    already imported half the toolchain.
    """
    probe = (
        "import sys; import guardmem_core; "
        f"loaded=[m for m in {FORBIDDEN_FRAMEWORKS!r} if m in sys.modules]; "
        "print(','.join(loaded))"
    )
    # Fixed argv, no shell, no user-supplied input - the flake8-bandit `S` rules
    # are already disabled for tests/ by per-file-ignores, so no noqa is needed
    # here (RUF100 flags one that is).
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    leaked = [name for name in result.stdout.strip().split(",") if name]
    assert not leaked, (
        f"guardmem_core pulled in {leaked}. The core engine must stay free of "
        "web frameworks - see PROJECT_TREE.md 'Ownership & Dependency Rules'."
    )
