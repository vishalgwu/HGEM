"""`RULES.md` §2.4's size limits, enforced rather than remembered.  S2.2

§0 of that document sets the bar for every rule in it: *"If a rule below isn't
checkable by a linter, a test, or a CODEOWNERS review gate, it's a suggestion
and should be deleted from this file."* The module and function length caps were
the one rule being checked by hand, once per step, in a script pasted into a
terminal - which is how a limit gets noticed three steps after it was breached.
This is that check, run by the suite.

**The function cap counts the body, not the docstring**, and S2.2 is where that
stopped being a matter of taste. §8 requires every public function to state what
it returns *and what it raises*; a function with seven parameters and five raise
conditions cannot do that inside 50 lines measured from `def`. The proof that
the body reading is the intended one is already in the tree - `complete` in
`llm/base.py` sits at exactly 50 lines measured from `def` while its body is the
single token `...`. Under the strict reading, the repository's own reference
Protocol is at the limit while containing no logic at all, which makes the rule
a docstring-length cap rather than the complexity signal it sits beside `C901`
to be.

Module length is measured plainly, in lines. A long file is a navigation cost
whatever is in it, and nothing about that argument turns on docstrings.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import REPO_ROOT

# RULES.md §2.4.
_MAX_MODULE_LINES = 400
_MAX_FUNCTION_BODY_LINES = 50

# Everything the repo writes and lints. `tests/` is in scope because `make
# typecheck` covers it as of S1.7 and the same navigation argument applies -
# a 900-line test module is no easier to read than a 900-line source one.
_ROOTS = ("packages", "tests", "scripts")


def _python_files() -> list[Path]:
    """Every tracked Python file under the roots above."""
    found = [
        path
        for root in _ROOTS
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    assert found, "no Python files found; the roots are wrong"
    return found


def _body_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Lines from the end of the docstring (or the `def`) to the end of the body."""
    first = node.body[0]
    is_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    start = first.end_lineno + 1 if is_docstring and first.end_lineno else node.lineno
    return max(node.end_lineno - start + 1 if node.end_lineno else 0, 0)


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_module_is_longer_than_the_cap(path: Path) -> None:
    lines = len(path.read_text(encoding="utf-8").splitlines())
    assert lines <= _MAX_MODULE_LINES, (
        f"{path.relative_to(REPO_ROOT)} is {lines} lines, over RULES.md 2.4's "
        f"{_MAX_MODULE_LINES}. Split it along a real seam rather than shaving it."
    )


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_function_body_is_longer_than_the_cap(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    over = [
        (node.name, node.lineno, _body_span(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and _body_span(node) > _MAX_FUNCTION_BODY_LINES
    ]
    assert not over, (
        f"{path.relative_to(REPO_ROOT)}: function bodies over "
        f"{_MAX_FUNCTION_BODY_LINES} lines: {over}"
    )


def test_the_docstring_exclusion_is_what_makes_the_cap_a_complexity_signal() -> None:
    """Pin the measurement itself, since the rule turns on it.

    Without this, a future change to `_body_span` could quietly re-introduce the
    strict reading and every well-documented function would start failing for a
    reason that has nothing to do with its complexity.
    """
    module = ast.parse(
        "def f(a, b):\n"
        '    """A docstring.\n'
        "\n"
        "    Spanning several lines, as this repository's do.\n"
        '    """\n'
        "    return a + b\n"
    )
    function = module.body[0]
    assert isinstance(function, ast.FunctionDef)
    assert _body_span(function) == 1
