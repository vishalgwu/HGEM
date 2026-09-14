"""CHECKPOINT B's harness.  BUILD_NOTEBOOK.md, after Day 5

    uv run python -m scripts.checkpoint_b verify
    uv run python -m scripts.checkpoint_b template corpus.jsonl
    uv run python -m scripts.checkpoint_b score corpus.jsonl
    uv run python -m scripts.checkpoint_b agreement first.jsonl second.jsonl

**The discrimination test cannot be run yet, and the reason is structural rather
than missing work.** Step 3 is "run the pipeline, collect `C` for each", and the
pipeline needs a real `LLMClient` to extract candidates and judge conflicts.
There is no implementation: `llm/base.py` declares the Protocol, `FakeLLM`
implements it for tests, and S9.1 builds the provider adapters. Scoring against
`FakeLLM` would measure scripted responses.

Nor can the corpus be hand-written around that. The candidates have to come from
real extraction, because what the gate measures is whether `C` separates the
facts a model *actually proposes* - hand-authoring 200 of them would grade the
scorer against the author's idea of a plausible mistake, which is the "no model
grading" problem wearing a different hat.

So this ships as everything except generation:

- `verify` runs the manual checks B1-B8 by running the tests that cover them.
- `template` writes the corpus format, which is the contract generation has to
  fill.
- `score` is the gate: AUROC, the per-term diagnostics, the sign-off block.
- `agreement` is diagnostic 3, the self-consistency check on the labels.

`score` works end to end today against a corpus file, so the day S9.1 lands the
only new thing needed is the loop that fills one.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import TYPE_CHECKING, Final

from guardmem_core.eval import Labelled, agreement_rate, discriminate
from guardmem_core.eval.discrimination import SELF_AGREEMENT_FLOOR, Verdict

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parent.parent

# CHECKPOINT B's manual verification table, each row bound to the test that
# actually establishes it. Running them beats reading them: the table says
# "read the function; no awaits, no settings reads" for B3, and
# `test_neither_module_imports_settings_at_all` parses the module and checks.
CHECKS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "B1",
        "Worked entropy example reproduces H_norm = 0.590",
        "tests/unit/test_entropy.py::TestTheWorkedExample",
    ),
    (
        "B2",
        "Critical-impact candidate with perfect confidence still scores R >= 0.80",
        "tests/unit/test_impact.py::TestTheFloorIsTheSafetyProperty",
    ),
    (
        "B3",
        "decide() has no I/O",
        "tests/unit/test_decision.py::TestItReadsNothingButItsArguments",
    ),
    (
        "B4",
        "Escalation cannot recurse",
        "tests/unit/test_decision.py::TestEscalationDoesNotRecurse",
    ),
    (
        "B5",
        "Audit write is in the same transaction as the state change",
        "tests/integration/test_audit_chain.py::TestItCommitsWithTheStateChange",
    ),
    (
        "B6",
        "Tampering with an audit row is detected at the exact break point",
        "tests/integration/test_audit_chain.py::TestTamperingIsCaughtAtTheRightRow",
    ),
    (
        "B7",
        "Replay produces an identical decision",
        "tests/integration/test_replay_trace.py::TestTheDoneWhen",
    ),
    (
        "B8",
        "Every matrix cell and every override has a test (12 + 7)",
        "tests/unit/test_decision.py::TestEveryCell tests/unit/test_overrides.py::TestEachOverride",
    ),
)


def verify() -> int:
    """Run the tests behind B1-B8 and report each row.

    Returns:
        0 when every row passes.

    B5, B6 and B7 need Postgres, so this needs a Docker daemon - and on this
    machine `TESTCONTAINERS_RYUK_DISABLED=true`. A row that cannot run is
    reported as ERROR rather than skipped quietly: the checkpoint's whole point
    is that these are not optional.
    """
    failures = 0
    for number, description, target in CHECKS:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input.
            [sys.executable, "-m", "pytest", "-q", "--no-header", *target.split()],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        status = "PASS" if result.returncode == 0 else "FAIL"
        failures += result.returncode != 0
        print(f"  {number}  {status}  {description}")
        if result.returncode != 0:
            print(f"        {target}")
    print(f"\n{len(CHECKS) - failures}/{len(CHECKS)} manual checks pass")
    return 1 if failures else 0


def template(path: pathlib.Path) -> int:
    """Write the corpus format, with two rows showing what a label looks like.

    Returns:
        0, or 1 if the file exists - overwriting a corpus somebody spent two
        hours labelling is not a thing this should do on a typo.
    """
    if path.exists():
        print(f"{path} exists; refusing to overwrite a labelled corpus")
        return 1
    rows = [
        {
            "candidate_id": "c_1",
            "keep": None,
            "subject": "patient:8812",
            "predicate": "allergy",
            "object": "penicillin",
            "verbatim": "I'm allergic to penicillin - it gives me hives",
            "scores": {"confidence": 0.0, "grounding": 0.0, "semantic_entropy": 0.0},
        },
        {
            "candidate_id": "c_2",
            "keep": None,
            "subject": "patient:8812",
            "predicate": "allergy",
            "object": "sulfa",
            "verbatim": "allergic to sulfa drugs",
            "scores": {"confidence": 0.0, "grounding": 0.0, "semantic_entropy": 0.0},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print("  `keep`: set true where the fact should be stored, false where not.")
    print("  Leave `scores` alone - the pipeline fills them; a hand-edited score")
    print("  is the gate measuring the labeller's expectation of it.")
    return 0


def score(path: pathlib.Path) -> int:
    """The gate. Read a labelled corpus, compute AUROC, print the sign-off.

    Returns:
        0 on PASS, 1 on MARGINAL, 2 on FAIL - so this composes into a check and
        MARGINAL is distinguishable from both, which is the point of having
        three bands rather than two.
    """
    rows = _read(path)
    unlabelled = [row["candidate_id"] for row in rows if row.get("keep") is None]
    if unlabelled:
        print(f"{len(unlabelled)} of {len(rows)} candidates are unlabelled, e.g. {unlabelled[:3]}")
        return 2
    report = discriminate(
        [
            Labelled(
                candidate_id=str(row["candidate_id"]),
                keep=bool(row["keep"]),
                scores=_scores(row),
            )
            for row in rows
        ]
    )

    print("CHECKPOINT B: " + report.verdict.value.upper())
    print(f"AUROC:              {report.auroc:.3f}  (n={report.labelled}, human-labelled)")
    for name, value in sorted(report.by_score.items(), key=lambda pair: -pair[1]):
        print(f"  {name:20} {value:.3f}")
    print(f"kept / rejected:    {report.kept} / {report.labelled - report.kept}")
    print(f"best single score:  {report.best_single_score}")
    if report.best_single_score != "confidence":
        # Diagnostic 1, and the checkpoint's closing advice: "ship the simplest
        # thing that discriminates [...] a working single-signal scorer beats an
        # elegant composite that does not separate."
        print("  ^ a single term beats the composite: the weights are adding noise")
    if report.verdict is Verdict.FAIL:
        print("\nFAIL. Do not build the gateway. Diagnose in the checkpoint's order.")
    return {Verdict.PASS: 0, Verdict.MARGINAL: 1, Verdict.FAIL: 2}[report.verdict]


def agreement(first: pathlib.Path, second: pathlib.Path) -> int:
    """Diagnostic 3: how often the labeller agrees with themselves.

    Returns:
        0 when agreement clears the checkpoint's 90%, 1 when it does not.

    Run this *before* reading anything into a low AUROC. A scorer measured
    against inconsistent labels tells you nothing about the scorer, and the
    checkpoint is explicit about the order: "the task is underspecified and the
    ontology needs work before the scorer does".
    """
    rate = agreement_rate(_labels(first), _labels(second))
    print(f"self-agreement: {rate:.1%}")
    if rate < SELF_AGREEMENT_FLOOR:
        print(
            f"below {SELF_AGREEMENT_FLOOR:.0%}. The task is underspecified - fix the "
            "ontology before the scorer. Any AUROC over these labels is noise."
        )
        return 1
    return 0


def _scores(row: dict[str, object]) -> dict[str, float]:
    """The score map off one corpus row, checked rather than cast.

    Raises:
        ValueError: `scores` is missing or is not an object of numbers. A row
            whose scores are a string would otherwise reach `discriminate` and
            fail there, naming the wrong thing.
    """
    raw = row.get("scores")
    if not isinstance(raw, dict):
        raise ValueError(
            f"{row.get('candidate_id')}: `scores` must be an object, got {type(raw).__name__}"
        )
    return {str(name): float(value) for name, value in raw.items()}


def _read(path: pathlib.Path) -> list[dict[str, object]]:
    """Read a JSONL corpus.

    JSONL rather than JSON because a human edits it: one candidate per line
    means a `git diff` of two hours' labelling is readable, and a syntax error
    names the row it is on.
    """
    rows: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return rows


def _labels(path: pathlib.Path) -> dict[str, bool]:
    """Candidate id to label, for the agreement check."""
    return {
        str(row["candidate_id"]): bool(row["keep"])
        for row in _read(path)
        if row.get("keep") is not None
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a subcommand."""
    parser = argparse.ArgumentParser(description="CHECKPOINT B harness")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="run the tests behind B1-B8")
    template_parser = sub.add_parser("template", help="write an empty corpus file")
    template_parser.add_argument("path", type=pathlib.Path)
    score_parser = sub.add_parser("score", help="the AUROC gate over a labelled corpus")
    score_parser.add_argument("path", type=pathlib.Path)
    agreement_parser = sub.add_parser("agreement", help="diagnostic 3, self-consistency")
    agreement_parser.add_argument("first", type=pathlib.Path)
    agreement_parser.add_argument("second", type=pathlib.Path)

    args = parser.parse_args(argv)
    if args.command == "verify":
        return verify()
    if args.command == "template":
        return template(args.path)
    if args.command == "score":
        return score(args.path)
    return agreement(args.first, args.second)


if __name__ == "__main__":
    sys.exit(main())
