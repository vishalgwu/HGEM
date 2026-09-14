"""Measurement the package owns.  CHECKPOINT B, and S22.1's suites later.

`discrimination` is the AUROC gate: can the confidence composite tell a
candidate a human would keep from one they would not? `PROJECT_TREE.md`'s
ownership table lets `evals/*` import `guardmem-core`, so the metric lives here
and both the one-off checkpoint and the nightly suites read the same
implementation.
"""

from guardmem_core.eval.discrimination import (
    DiscriminationReport,
    Labelled,
    Verdict,
    agreement_rate,
    auroc,
    discriminate,
)

__all__ = [
    "DiscriminationReport",
    "Labelled",
    "Verdict",
    "agreement_rate",
    "auroc",
    "discriminate",
]
