"""Can the scorer tell a good candidate from a bad one?  CHECKPOINT B

The checkpoint's own framing: "everything after this point - gateway,
guardrails, dashboard, HITL, evals, deploy - assumes the scoring can tell good
candidates from bad ones. If it cannot, you are about to spend three weeks
building operations tooling for a system that does not work."

The failure it catches is named there too, and it is the one unit tests cannot
see: "a scorer that produces plausible numbers with no discriminative power.
This is the default outcome of implementing a formula correctly without ever
validating it." Every term of §3.2 is unit-tested to 100% and every one of those
tests would still pass if `C` correlated with nothing.

**AUROC is computed by ranks, not by sweeping thresholds**, and the difference
matters at this sample size. With n=200 a threshold sweep is a step function
whose area depends on where the steps land; the rank form is exact, has a
closed-form tie correction, and is the same number the Mann-Whitney U test
reports. Ties are not hypothetical here: `S_cor` is 0.0 for every
single-sourced candidate, so a diagnostic AUROC over that term alone is *mostly*
ties, and a tie-blind implementation would score it anywhere between 0 and 1.

**Nothing here labels anything.** The checkpoint says "a human labels each one
[...] No model grading", so `Labelled` takes a label it was given and this
module never produces one. That is the whole validity of the measurement: a
scorer graded against its own judgements measures self-consistency.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import Field

from guardmem_core.schemas.base import GMModel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "DiscriminationReport",
    "Labelled",
    "Verdict",
    "agreement_rate",
    "auroc",
    "discriminate",
]

# CHECKPOINT B's bands, verbatim: ">= 0.80 -> PASS", "0.75-0.80 -> MARGINAL",
# "< 0.75 -> FAIL. Stop. Do not build the gateway."
PASS_FLOOR: Final = 0.80
MARGINAL_FLOOR: Final = 0.75

# Diagnostic 3's bar: "if they disagree with themselves more than 10% of the
# time, the task is underspecified and the ontology needs work before the scorer
# does."
SELF_AGREEMENT_FLOOR: Final = 0.90


class Verdict(StrEnum):
    """What the AUROC says to do next."""

    PASS = "pass"  # noqa: S105 - a gate outcome, not a credential. Same as GateOutcome.PASS.
    MARGINAL = "marginal"
    FAIL = "fail"


class Labelled(GMModel):
    """One candidate, its human label, and every score the pipeline gave it.

    Attributes:
        candidate_id: Which candidate.
        keep: The human's judgement - true when this should be stored. **Given,
            never derived.** See the module docstring.
        scores: Every number worth ranking by, keyed by name: `confidence` for
            the composite, and each term beside it so the diagnostics can ask
            what is carrying it.

    A `dict` of scores rather than named fields, because CHECKPOINT B's
    diagnostics name two by hand (`1 - H_norm`, `S_src`) and the interesting
    question is which of the five is doing the work - which means all five, and
    anything a later diagnostic wants to add.
    """

    candidate_id: str
    keep: bool
    scores: dict[str, float]


class DiscriminationReport(GMModel):
    """The gate's answer, and enough to act on it.

    Attributes:
        verdict: PASS, MARGINAL or FAIL.
        auroc: The composite's AUROC against the human labels.
        by_score: Every score's AUROC, including the composite's. This is
            diagnostics 1 and 2 - "is entropy doing anything?", "is grounding
            doing anything?" - computed for all of them rather than the two the
            checkpoint names, because the answer is only useful in comparison.
        labelled: How many candidates carried a label.
        kept: How many the human would store. Reported because AUROC is
            undefined at either extreme and a lopsided split makes it fragile
            long before that.
    """

    verdict: Verdict
    auroc: float = Field(ge=0.0, le=1.0)
    by_score: dict[str, float]
    labelled: int = Field(ge=0)
    kept: int = Field(ge=0)

    @property
    def best_single_score(self) -> str:
        """Which single score discriminates best.

        CHECKPOINT B's diagnostic 1 turns on this: "if entropy alone beats the
        composite, the weights are wrong and the other terms are adding noise."
        And its closing advice is the same point positively - "ship the simplest
        thing that discriminates [...] a working single-signal scorer beats an
        elegant composite that does not separate."
        """
        return max(self.by_score, key=lambda name: self.by_score[name])


def auroc(labels: Sequence[bool], scores: Sequence[float]) -> float:
    """Area under the ROC curve, by ranks.

    Args:
        labels: True where the human would keep the candidate.
        scores: The number being ranked by, positionally aligned with `labels`.

    Returns:
        The probability that a randomly chosen kept candidate outranks a
        randomly chosen rejected one, with ties counted as half. 0.5 is chance.

    Raises:
        ValueError: the two lengths differ, the set is empty, or every label is
            the same. **Not 0.5 in that last case**: a set with no rejected
            candidate has no discrimination to measure, and returning the
            chance value would report "this scorer is useless" where the truth
            is "this question was not asked". The distinction decides whether
            somebody re-labels or rewrites the scorer.

    The rank form with average ranks for ties, which is exact rather than a
    threshold sweep's approximation - see the module docstring on why that is
    load-bearing at n=200 with a term that is mostly ties.
    """
    if len(labels) != len(scores):
        raise ValueError(f"{len(labels)} labels against {len(scores)} scores")
    if not labels:
        raise ValueError("no labelled candidates; AUROC is undefined over an empty set")
    kept = int(np.sum(labels))
    rejected = len(labels) - kept
    if not kept or not rejected:
        raise ValueError(
            f"every candidate is labelled {'keep' if kept else 'reject'}; AUROC "
            "needs both classes. This is a labelling problem, not a scorer one"
        )
    ranks = _average_ranks(np.asarray(scores, dtype=float))
    kept_rank_sum = float(ranks[np.asarray(labels, dtype=bool)].sum())
    # Mann-Whitney U over the kept class, normalised. Subtracting the rank sum a
    # perfectly-losing class would have leaves the number of winning pairs.
    return (kept_rank_sum - kept * (kept + 1) / 2) / (kept * rejected)


def discriminate(
    labelled: Sequence[Labelled], *, composite: str = "confidence"
) -> DiscriminationReport:
    """Run the gate over a labelled corpus.

    Args:
        labelled: The corpus. Every entry must carry the same score names -
            a missing one is a silent hole in a diagnostic, so it raises.
        composite: Which score is `C`. Named rather than assumed, so a run that
            wants to gate on a single term can.

    Returns:
        The report, with every score's AUROC alongside the composite's.

    Raises:
        ValueError: the corpus is empty, one-sided, or the score names differ
            between entries.

    Note what is *not* here: no threshold, no decision, no writing. The gate
    asks one question about one number, and the thresholds `decide()` reads are
    a separate thing that this measurement is supposed to inform.
    """
    names = _score_names(labelled)
    if composite not in names:
        raise ValueError(f"{composite!r} is not among the scores: {sorted(names)}")
    labels = [item.keep for item in labelled]
    by_score = {
        name: auroc(labels, [item.scores[name] for item in labelled]) for name in sorted(names)
    }
    return DiscriminationReport(
        verdict=_verdict(by_score[composite]),
        auroc=by_score[composite],
        by_score=by_score,
        labelled=len(labelled),
        kept=sum(labels),
    )


def agreement_rate(first: Mapping[str, bool], second: Mapping[str, bool]) -> float:
    """How often two labellings of the same candidates agree.  Diagnostic 3

    Args:
        first: Candidate id to label, from the first pass.
        second: The same candidates, labelled blind a second time.

    Returns:
        The fraction they agree on, over the candidates both carry.

    Raises:
        ValueError: they share no candidate, so there is nothing to compare.

    CHECKPOINT B: "have the human re-label 30 items blind. If they disagree with
    themselves more than 10% of the time, the task is underspecified and the
    ontology needs work before the scorer does." That ordering is the point of
    the check - a low AUROC against inconsistent labels says nothing about the
    scorer, and rewriting the scorer against them is how a project spends a week
    fitting noise.
    """
    shared = first.keys() & second.keys()
    if not shared:
        raise ValueError("the two labellings share no candidate")
    return sum(first[key] == second[key] for key in shared) / len(shared)


def _verdict(score: float) -> Verdict:
    """CHECKPOINT B's three bands.

    Half-open upward, like every other band in this codebase: 0.80 exactly is a
    PASS, because the checkpoint writes ">= 0.80".
    """
    if score >= PASS_FLOOR:
        return Verdict.PASS
    if score >= MARGINAL_FLOOR:
        return Verdict.MARGINAL
    return Verdict.FAIL


def _score_names(labelled: Sequence[Labelled]) -> set[str]:
    """The score names every entry carries.

    Raises:
        ValueError: the corpus is empty, or two entries carry different names.
            Ragged scores would mean a diagnostic silently ran over a subset,
            and an AUROC over a subset is a different measurement wearing the
            same name.
    """
    if not labelled:
        raise ValueError("no labelled candidates")
    names = set(labelled[0].scores)
    for item in labelled[1:]:
        if set(item.scores) != names:
            raise ValueError(
                f"{item.candidate_id} carries scores {sorted(item.scores)}, but the "
                f"corpus carries {sorted(names)}; a diagnostic over a subset is a "
                "different measurement with the same name"
            )
    return names


def _average_ranks(scores: np.ndarray) -> np.ndarray:
    """Ranks from 1, with tied values sharing their average rank.

    The tie correction is what makes this exact. Without it a term like `S_cor`
    - 0.0 for every single-sourced candidate - would have its ties broken by
    array order, which is the order the candidates happened to be extracted in,
    and the resulting AUROC would be an artefact of that ordering.
    """
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=float)
    ordered = scores[order]
    start = 0
    for index in range(1, len(ordered) + 1):
        if index == len(ordered) or ordered[index] != ordered[start]:
            if index - start > 1:
                ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index
    return ranks
