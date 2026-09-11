"""Tests for the domain identifier types.  BUILD_NOTEBOOK.md S1.5

`RULES.md` §2.1 makes a specific claim: "Passing a raw `str` where an
`AssertionId` is expected **must be a type error**."

That claim cannot be tested at runtime. `NewType` erases completely - at
runtime `AssertionId("x")` *is* `"x"`, and every assertion about isinstance,
equality or behaviour passes whether or not the annotations are doing anything
at all. A test suite that only exercised runtime behaviour would report these
types as working while providing no protection whatsoever.

So the check runs `mypy` over generated snippets in a subprocess and asserts on
its verdict, following the same reasoning as
`tests/unit/test_package_contract.py`: the property under test is a static one,
so a static tool has to be the thing that reports it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from guardmem_core.types import (
    AssertionId,
    CandidateId,
    EntityId,
    Namespace,
    TenantId,
    TraceId,
)

ALL_IDS = (AssertionId, CandidateId, EntityId, Namespace, TenantId, TraceId)


def run_mypy(source: str, tmp_path: Path) -> tuple[int, str]:
    """Type-check a snippet in isolation and return mypy's exit code and output.

    The file is written outside the package so the project's own per-module
    settings do not apply, and `--strict` is passed explicitly to match what
    `make typecheck` runs.
    """
    module = tmp_path / "snippet.py"
    module.write_text(source, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--no-incremental",  # a shared cache between snippets would be wrong
            "--no-error-summary",
            str(module),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=REPO_ROOT,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_ids_are_plain_strings_at_runtime() -> None:
    """NewType has no runtime cost and no runtime effect - by design.

    Pinned so nobody later mistakes these for validating wrappers and starts
    relying on them to reject a malformed id. They do not.
    """
    assertion_id = AssertionId("a_7f21")

    assert assertion_id == "a_7f21"
    assert isinstance(assertion_id, str)


def test_every_id_is_constructible_and_distinct_by_name() -> None:
    """All six exist and carry their own name, which is what mypy reports on."""
    assert {t.__name__ for t in ALL_IDS} == {
        "AssertionId",
        "CandidateId",
        "EntityId",
        "Namespace",
        "TenantId",
        "TraceId",
    }


def test_mypy_rejects_a_raw_str_where_an_id_is_expected(tmp_path: Path) -> None:
    """The claim RULES.md 2.1 actually makes, checked by the tool that enforces it."""
    code, output = run_mypy(
        "from guardmem_core.types import AssertionId\n"
        "def supersede(old: AssertionId) -> None: ...\n"
        'supersede("a_7f21")\n',
        tmp_path,
    )

    assert code != 0, f"mypy accepted a raw str as an AssertionId:\n{output}"
    assert "arg-type" in output, f"expected an argument-type error, got:\n{output}"


def test_mypy_rejects_mixing_two_id_types(tmp_path: Path) -> None:
    """The failure these types exist to prevent.

    Both are `str`, both are opaque, and `supersede(new, old)` with the
    arguments transposed corrupts data silently. Distinct `NewType`s are what
    make that a compile-time error instead of an incident.
    """
    code, output = run_mypy(
        "from guardmem_core.types import AssertionId, CandidateId\n"
        "def store(assertion: AssertionId) -> None: ...\n"
        'store(CandidateId("c_1"))\n',
        tmp_path,
    )

    assert code != 0, f"mypy allowed a CandidateId where an AssertionId belongs:\n{output}"
    assert "arg-type" in output, f"expected an argument-type error, got:\n{output}"


def test_mypy_accepts_correct_usage(tmp_path: Path) -> None:
    """The negative tests above only mean something if the positive one passes.

    Otherwise they would pass on any broken snippet - an unresolved import, a
    syntax error - and prove nothing about the types.
    """
    code, output = run_mypy(
        "from guardmem_core.types import AssertionId\n"
        "def supersede(old: AssertionId) -> None: ...\n"
        'supersede(AssertionId("a_7f21"))\n',
        tmp_path,
    )

    assert code == 0, f"mypy rejected correct usage:\n{output}"
