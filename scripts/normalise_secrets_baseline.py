"""Rewrite `.secrets.baseline` result paths to POSIX separators.

`detect-secrets` records each finding under the path separator of the machine
that produced it. On Windows that means `docs\\BUILD_NOTEBOOK.md`; Linux CI
scans `docs/BUILD_NOTEBOOK.md`, fails to match the key, and reports an
already-reviewed finding as a brand-new secret. That failure took two CI runs to
diagnose the first time.

Fixing the file once was not enough. The hook **rewrites the baseline every time
it updates it**, and it updates it whenever a line number shifts in any
baselined file - so an ordinary edit to a document reintroduces the Windows
separators, and `tests/unit/test_secrets_baseline.py` then fails. Repairing that
by hand on every commit is exactly the kind of recurring manual step that gets
skipped under pressure, on the one file where a careless change can allowlist a
real secret.

So this runs straight after `detect-secrets` in `.pre-commit-config.yaml`.

Only the result path strings are rewritten, by their exact JSON representation.
The `exclude` entries in the same file are regexes whose backslashes escape
literal dots (``^requirements\\.lock\\.txt$``); a blanket replacement turns that
into ``^requirements/.lock/.txt$`` and silently disables the exclusion. That
mistake was made once already and the hooks stayed green through it.

Exit codes follow the pre-commit convention for a hook that edits files:
0 when nothing changed, 1 when the baseline was rewritten, so the change is
staged and reviewed rather than applied invisibly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / ".secrets.baseline"


def normalise(baseline: Path) -> list[str]:
    """Rewrite Windows-separated result paths in place.

    Returns the paths that were rewritten, empty if the file was already
    POSIX-clean. Raises nothing on a missing file - a repository without a
    baseline is a valid state, and the detect-secrets hook reports that itself.
    """
    if not baseline.is_file():
        return []

    raw = baseline.read_text(encoding="utf-8")
    results: dict[str, object] = json.loads(raw)["results"]

    rewritten: list[str] = []
    for key in [path for path in results if "\\" in path]:
        raw = raw.replace(json.dumps(key), json.dumps(key.replace("\\", "/")))
        rewritten.append(key)

    if rewritten:
        baseline.write_text(raw, encoding="utf-8")
    return rewritten


def main() -> int:
    rewritten = normalise(BASELINE)
    if not rewritten:
        return 0

    print(f"{BASELINE.name}: rewrote {len(rewritten)} path(s) to POSIX separators")
    for path in rewritten:
        posix = path.replace("\\", "/")
        print(f"  {path} -> {posix}")
    print("Baseline updated - review the diff and `git add .secrets.baseline`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
