"""The design suite is load-bearing, and nothing was checking it was there.

**This exists because a scripted edit deleted 2800 lines of
`BUILD_NOTEBOOK.md` and every gate stayed green.** `ruff` and `mypy` do not read
markdown, no test read the notebook, and the only symptom was a CI failure two
commits later in `detect-secrets`, whose baseline records a line number in that
file. The document the whole build is driven from can be destroyed silently, and
was.

`RULES.md` §8 makes these documents the spec of record - "if the code and that
doc disagree, the code is wrong until an ADR says otherwise" - which is a strong
claim to make about files nothing verifies. These are the cheapest checks that
would have caught it: a document that has lost its title, its ending, or most of
its body is broken in a way no reader would miss and no tool was looking for.

**Deliberately structural, not a word count.** Asserting on prose would make
every edit a test failure and teach people to update the number without reading
why. What is pinned is the shape a reader relies on: the title, the closing
line, the step sequence, and the appendices - each of which a truncation removes
and an ordinary edit does not touch.
"""

from __future__ import annotations

import re

import pytest

from conftest import REPO_ROOT

NOTEBOOK = REPO_ROOT / "docs" / "BUILD_NOTEBOOK.md"

# S0.1 through S28.4. The notebook's own count, and `docs/README.md` states it:
# "the same 96 steps S0.1 to S28.4".
#
# **Two heading levels, and the first version of this test missed it.** Part 0's
# four setup steps are `## S0.1 -- ...`; every later step is `### SN.M -- ...`.
# Matching only `###` counted 92 and read as a truncation, which is the right
# failure for the wrong reason - and a good reminder that a guard asserting a
# number has to be checked against the document before it is trusted.
_EXPECTED_STEPS = 96

_STEP = re.compile(r"^#{2,3} S\d+\.\d+ ", re.MULTILINE)
_APPENDIX = re.compile(r"^## ([A-G])\. ", re.MULTILINE)


def _notebook() -> str:
    return NOTEBOOK.read_text(encoding="utf-8")


def test_the_notebook_still_has_its_title() -> None:
    """A truncation from the top removes this first.

    That is exactly what happened: the file began mid-CHECKPOINT-B, with no
    title, no §0, and no Days 1-5.
    """
    assert _notebook().startswith("# GuardMem AI — Master Build Notebook"), (
        "BUILD_NOTEBOOK.md does not start with its title. It has almost "
        "certainly been truncated - check `git log --stat -- docs/BUILD_NOTEBOOK.md` "
        "and restore from the last commit whose copy is intact."
    )


def test_the_notebook_still_has_its_closing_line() -> None:
    """A truncation from the bottom removes this one."""
    assert (
        _notebook().rstrip().endswith("a notebook that drifts from the code is worse than none.*")
    ), "BUILD_NOTEBOOK.md has lost its closing line; it has been truncated at the end."


def test_every_step_from_s0_1_to_s28_4_is_present() -> None:
    """The body, counted the way the documents already count it.

    `docs/README.md` pins the number - "the same 96 steps S0.1 to S28.4" - so
    this is not a new fact, it is an existing one that nothing enforced.
    """
    found = len(_STEP.findall(_notebook()))

    assert found == _EXPECTED_STEPS, (
        f"BUILD_NOTEBOOK.md has {found} steps, not {_EXPECTED_STEPS}. "
        "Steps are not added or removed by ordinary edits: a change here means "
        "either a genuine plan change (update this number and say why in the "
        "commit) or a truncation."
    )


def test_the_appendices_are_all_there() -> None:
    """A-G, which `docs/README.md` names and `PHASES_AND_ROADMAP.md` points at.

    Worth its own check: a 2026-09-14 correction claimed the notebook "has no
    appendices" and repointed two documents away from them, on a tree where all
    seven were present. A test makes that claim checkable rather than arguable.
    """
    assert _APPENDIX.findall(_notebook()) == ["A", "B", "C", "D", "E", "F", "G"]


@pytest.mark.parametrize(
    "name",
    [
        "PRD.md",
        "ARCHITECTURE.md",
        "RULES.md",
        "MEMORY_ENGINE.md",
        "MCP_INTEGRATION.md",
        "PHASES_AND_ROADMAP.md",
        "PROJECT_TREE.md",
        "DESIGN_SYSTEM.md",
        "BUILD_NOTEBOOK.md",
        "README.md",
    ],
)
def test_every_design_document_exists_and_is_not_empty(name: str) -> None:
    """The suite `docs/README.md` indexes, present and non-trivial.

    The bar is deliberately low - a file that still has a heading and some body
    passes. What it catches is a document deleted, emptied, or replaced by a
    stub, which is the failure mode this module was written for.
    """
    path = REPO_ROOT / "docs" / name

    assert path.is_file(), f"docs/{name} is missing; docs/README.md indexes it."
    body = path.read_text(encoding="utf-8")
    assert body.lstrip().startswith("#"), f"docs/{name} has lost its heading."
    assert len(body.splitlines()) > 50, (
        f"docs/{name} is {len(body.splitlines())} lines. That is far below "
        "anything this suite has ever contained - it has probably been truncated."
    )
