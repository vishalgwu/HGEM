"""Uncertainty as entropy over meanings, not over words.  BUILD_NOTEBOOK.md S5.1

`MEMORY_ENGINE.md` §3.1: "Lexical variance is not uncertainty - 'lives in
Austin' and 'resides in Austin, TX' are one meaning." So the K samples are
clustered by **bidirectional** entailment and the entropy is taken over the
clusters, following Kuhn et al. 2023 and Farquhar et al. 2024.

Bidirectional is the whole mechanism. One-way entailment is satisfied by any
claim narrower than another - "allergic to penicillin and amoxicillin" entails
"allergic to penicillin" - so a one-way test would merge a specific answer into
a vague one and report agreement where the samples actually differ. Requiring
both directions means the two claims have to be interchangeable.

**`entail` is injected and this module never chooses one.** §3.1's reuse note
says the clustering here "is the same machinery as LID's semantic-entropy
detector" and S5.1 asks that `EntailFn` stay "an injected callable so LID can
back it later without touching this module". It is a plain sync callable, which
is what a local cross-encoder is; an async backend - `LLMJudge`, say - should
precompute the pairs it needs and pass a lookup, because every comparison here
is independent and none of them need to be sequential.

**What the `H_norm := 0` rule at K = 1 costs, and why it is still right.** Zero
entropy is *maximum* confidence on §3.2's `w_H(1 - H_norm)` term, so a single
sample scores as certain. That is only safe because §1.2 draws K by risk hint
and `extract` refuses a short sample count rather than absorbing it - the two
rules hold each other up, and relaxing either one turns a degraded provider into
a confident one.

**A sample that proposed nothing is `None`, and that is S5.6's correction to
this step.** §3.1 clusters "the K samples for a given `(subject, predicate)`"
and S5.1 read that as K rendered claims. Composing the pipeline showed the
question it leaves open: when three of five samples propose a fact, is `K` three
or five?

Five. Three divides by `log 3` over a set that all agreed, so a fact only three
draws mentioned scores `H_norm = 0` - and one that only sample 0 proposed
reaches `K = 1`, where §3.1 sets `H_norm := 0` outright. That is maximum
confidence from minimum evidence, which is the exact shape `ARCHITECTURE.md` §0
forbids.

**Each abstention is its own cluster, and getting that wrong is subtle.** The
first attempt clustered all the silent draws together - silence being one
meaning, not one per sample - and the arithmetic said otherwise: one claim
against four shared abstentions is a 0.2/0.8 split scoring 0.31, while three
agreeing claims against two is 0.6/0.4 scoring 0.42. Entropy measures *spread*,
so a lopsided split is low-entropy, and a fact one sample proposed came out more
confident than one three samples agreed on. Leaving abstentions unclustered
makes support monotone: five agreeing scores 0.0, three of five 0.59, one of
five 1.0. The justification is not the arithmetic, though - it is that
`same_meaning` is bidirectional entailment, an abstention asserts no
proposition, and a non-assertion entails nothing. Including another one.

**The minority-cluster drop is built and cannot fire today.** §3.1 adds that "a
candidate that appears in zero clusters containing sample 0's meaning is dropped
as a minority hallucination", and S5.1's own "Do NOT" list says not to skip it
for being fiddly. It is `MeaningClusters.minority` below. But
`ExtractionResult.candidates` is drawn from the canonical sample alone, so every
candidate is sample 0's and is in sample 0's cluster by construction - there is
no candidate today that the rule could drop. It becomes live the moment
candidates are pooled across samples, and it is the signal CHECKPOINT B's first
diagnostic asks for either way. Recorded here rather than discovered later from
a counter that never increments.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from typing import TYPE_CHECKING, Final

import numpy as np
from pydantic import Field

from guardmem_core.schemas.base import GMModel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["EntailFn", "MeaningClusters", "cluster_meanings", "semantic_entropy"]

type EntailFn = Callable[[str, str], float]
"""`P(premise ⊨ hypothesis)`, in that argument order, as a probability.

A plain callable rather than a Protocol: it has one method and no state, and
S5.1 asks for a callable by name so LID's detector can be bound to it.
"""

# §3.1 step 1: `same_meaning(s_i, s_j)` iff entailment is at least this in both
# directions. Not tunable from settings - it is part of the definition of the
# measurement, and a run that moved it would not be comparable with the AUROC
# CHECKPOINT B records against it.
_SAME_MEANING: Final = 0.8


class MeaningClusters(GMModel):
    """How K samples partition into meanings, and how uncertain that is.

    Attributes:
        labels: One cluster id per sample, positionally. The id is the lowest
            sample index in the cluster, so `labels[0]` is always 0 and the ids
            are stable under reordering of the pairwise comparisons - two runs
            over the same samples give identical labels, which is what makes
            this replayable under `RULES.md` §1.6.
        entropy: `H_norm`, normalised to [0, 1]. **Uncertainty**, so §3.2's
            composite uses `1 - H_norm`; a high value here lowers confidence.
        minority: Indices whose meaning is not sample 0's - §3.1's minority
            hallucinations. Sorted, and never contains 0.

    Carries all three because the clustering costs up to `K(K-1)` calls to a
    model and computing them separately would pay that twice.
    """

    labels: list[int]
    entropy: float = Field(ge=0.0, le=1.0)
    minority: list[int]


def cluster_meanings(samples: Sequence[str | None], entail: EntailFn) -> MeaningClusters:
    """Partition `samples` by meaning and score the spread.  §3.1

    Args:
        samples: The K rendered claims for one `(subject, predicate)`, in draw
            order, with `None` where a sample proposed nothing for that pair.
            **Sample 0 must be the canonical draw** - §1.2 draws it at
            temperature 0 and `minority` is defined relative to it, so passing
            these out of order silently redefines which answers count as
            hallucinations.
        entail: The entailment function. Called at most `K(K-1)` times and
            fewer in practice, because the reverse direction is only asked for
            when the forward one already agreed.

    Returns:
        The clustering, with `H_norm` and the minority indices.

    Raises:
        ValueError: `samples` is empty. §3.1 defines `H_norm` at K = 1 and
            above; at K = 0 there is nothing to be uncertain *about*, and the
            arithmetic would hand back 0.0 - which reads as total confidence
            and would feed §3.2 a full-weight term derived from no evidence.

    Deterministic given the same samples and the same `entail`: union-find
    always keeps the lowest index as the root, so cluster ids do not depend on
    comparison order.
    """
    size = len(samples)
    if size == 0:
        raise ValueError(
            "semantic entropy needs at least one sample; MEMORY_ENGINE.md 3.1 "
            "defines H_norm from K = 1 upward, and K = 0 would score as certain"
        )
    parent = list(range(size))
    for left, right in combinations(range(size), 2):
        first, second = samples[left], samples[right]
        if first is None or second is None:
            # An abstention asserts nothing, so it entails nothing - including
            # another abstention. It is never shown to `entail` (there is no
            # text to judge) and never unions, which leaves each silent draw its
            # own cluster by the ordinary rule rather than by a special case.
            # See the module docstring: clustering them together instead scores
            # a fact one sample proposed as *more* certain than one three
            # samples agreed on.
            continue
        # `and` short-circuits, so a pair that fails forward is never asked in
        # reverse. Most pairs in a disagreeing sample set fail forward.
        if entail(first, second) >= _SAME_MEANING and entail(second, first) >= _SAME_MEANING:
            _union(parent, left, right)
    labels = [_find(parent, index) for index in range(size)]
    return MeaningClusters(
        labels=labels,
        entropy=_normalised_entropy(labels),
        minority=[index for index, label in enumerate(labels) if label != labels[0]],
    )


def semantic_entropy(samples: Sequence[str | None], entail: EntailFn) -> float:
    """`H_norm` over the meanings in `samples`.  §3.1

    Args:
        samples: The K rendered claims, canonical first.
        entail: The entailment function.

    Returns:
        `H / log K`, in [0, 1]. Zero when every sample means the same thing,
        and 1.0 when no two of them do.

    Raises:
        ValueError: `samples` is empty - see `cluster_meanings`.

    The name S5.1 gives this. Callers that also want the minority indices
    should use `cluster_meanings` and read `.entropy`, rather than calling both
    and paying for the clustering twice.
    """
    return cluster_meanings(samples, entail).entropy


def _normalised_entropy(labels: Sequence[int]) -> float:
    """§3.1 steps 3-5: cluster sizes to `H_norm`.

    Args:
        labels: One cluster id per sample.

    Returns:
        `H / log K`, or 0.0 at K = 1.

    `log` is natural throughout. The base cancels in the ratio, so any
    consistent base gives the same `H_norm` - but §3.1's worked example quotes
    `H = 0.950` unnormalised, and that number is only reproducible in nats.

    **The clamp is not defensive programming; without it this raises on a real
    input.** When every sample is its own cluster, `H` is `log K` exactly in
    arithmetic and `log K` plus or minus an ulp in float64 - measured at up to
    8e-16 over K = 2..199, and *above* 1.0 for 51 of those, K = 5 among them.
    Five is §1.2's largest sample count, so "five samples that all disagree"
    is both the commonest maximum-uncertainty case and one that would fail
    `MeaningClusters.entropy`'s `le=1.0` bound. The clamp corrects float noise
    and nothing else: anything further out is a bug in the partition above, and
    would show up as a wrong `H_norm` rather than be hidden by this.
    """
    size = len(labels)
    if size == 1:
        # §3.1: "H_norm := 0 when K = 1". Not a special case of the formula -
        # `log 1` is 0 and the division would raise.
        return 0.0
    counts = Counter(labels)
    probabilities = np.array(list(counts.values()), dtype=float) / size
    entropy = float(-(probabilities * np.log(probabilities)).sum())
    return min(1.0, max(0.0, entropy / math.log(size)))


def _find(parent: list[int], node: int) -> int:
    """The root of `node`'s cluster, compressing the path on the way.

    Iterative rather than recursive: K is small today, but this is the same
    machinery §3.1 says LID reuses, and a recursive find on a long chain is a
    stack overflow that only appears at scale.
    """
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _union(parent: list[int], left: int, right: int) -> None:
    """Merge two clusters, keeping the lower index as the root.

    By rank or by size would be marginally faster and would make the root
    depend on the order the pairs were visited. The lowest index instead, so
    that `labels[0] == 0` always holds and `minority` can be defined against
    it - and so that two runs produce byte-identical labels.
    """
    root_left, root_right = _find(parent, left), _find(parent, right)
    if root_left != root_right:
        parent[max(root_left, root_right)] = min(root_left, root_right)
