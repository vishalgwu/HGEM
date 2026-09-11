"""Keeps `.secrets.baseline` portable between Windows and Linux CI.

`docs/RULES.md` §4 puts `detect-secrets` in pre-commit *and* CI. That only works
if the baseline means the same thing on both, and by default it does not:
`detect-secrets` records result paths using the separator of whatever machine
generated the file. A baseline written on Windows stores
``docs\\BUILD_NOTEBOOK.md``; Linux CI scans ``docs/BUILD_NOTEBOOK.md``, fails to
find that key, and reports an already-reviewed finding as a brand-new secret.

That is not hypothetical - it is how the first CI run of this workflow failed.
The `gates` job passed and the `hooks` job did not, on a tree where every hook
passed locally. Forward slashes work on both platforms (verified: the hook
resolves a POSIX key correctly on Windows and does not rewrite it back), so the
portable form is the POSIX one and these tests pin it.

The failure mode this guards against is quiet in the dangerous direction. A
mismatched path means a *previously approved* finding resurfaces, and the
natural response under CI pressure is to re-run the hook and commit whatever
baseline it produces - which is exactly how an unreviewed secret gets
allowlisted. Regenerating the baseline on Windows will reintroduce backslashes,
so this test is the thing that catches it before it lands.

Fix, if this fails: rewrite the offending keys to use `/`. Replace the path
strings only - the `exclude` entries in the same file are regexes whose
backslashes escape literal dots (``^requirements\\.lock\\.txt$``), and a blanket
replacement corrupts them.
"""

from __future__ import annotations

import json
from typing import Any

from conftest import REPO_ROOT

BASELINE = REPO_ROOT / ".secrets.baseline"


def _baseline() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data


def test_baseline_exists() -> None:
    """The baseline must be present, or the detect-secrets hook cannot run."""
    assert BASELINE.is_file(), (
        f"{BASELINE.name} is missing. RULES.md 4 requires detect-secrets in "
        "pre-commit and CI, and the hook is configured with --baseline."
    )


def test_baseline_paths_use_posix_separators() -> None:
    """Result paths must use `/`, so the baseline resolves on Linux CI too.

    A Windows-generated baseline stores backslashes, which Linux never matches -
    turning every reviewed finding back into an unreviewed one.
    """
    results: dict[str, Any] = _baseline()["results"]

    offenders = sorted(path for path in results if "\\" in path)

    assert not offenders, (
        "these .secrets.baseline paths use Windows separators and will not "
        "match on Linux:\n"
        + "\n".join(f"  {path}" for path in offenders)
        + "\nRewrite just these path strings to use '/'. Do not blanket-replace "
        "backslashes in the file - the exclude patterns are regexes."
    )


def test_baseline_filenames_agree_with_their_keys() -> None:
    """Each finding's `filename` must match the key it is stored under.

    They are written from the same value, so a mismatch means one of the two was
    edited by hand and the other was not.
    """
    results: dict[str, Any] = _baseline()["results"]

    mismatched = [
        (path, finding["filename"])
        for path, findings in results.items()
        for finding in findings
        if finding.get("filename") != path
    ]

    assert not mismatched, "baseline key and finding filename disagree:\n" + "\n".join(
        f"  key={path!r} filename={filename!r}" for path, filename in mismatched
    )


def test_baseline_references_only_existing_files() -> None:
    """Every baselined path must still exist.

    A stale entry is dead weight that quietly grows the allowlist, and it hides
    the fact that the finding it approved is gone.
    """
    results: dict[str, Any] = _baseline()["results"]

    missing = sorted(path for path in results if not (REPO_ROOT / path).is_file())

    assert not missing, (
        "these .secrets.baseline paths no longer exist:\n"
        + "\n".join(f"  {path}" for path in missing)
        + "\nRegenerate the baseline and re-review what it finds."
    )
