"""The eight features §3.3 scores, and where each one comes from.

Split from `impact.py` along the seam §3.3 itself draws: its table has a column
for what each feature *is* and a column for what it is *worth*. This module owns
the first - the vocabularies, and the conversions from domain objects into
`[0, 1]` - and `impact.py` owns the linear model that weighs them. The seam is
real rather than a line count: these change when the domain gains a new PII
class or a new mutation kind, and the betas change when
`scripts/threshold_tuner.py` refits them from reviewer labels. Different
reasons, different cadences.

**Three of the eight had no producer, and typing them as enums is what made the
answer findable.** `scope`, `pii_class` and `irreversibility` were named by §3.3
and defined by nothing. This docstring used to end "the day an ontology field
appears the mapping has somewhere to live" - ADR-0009 is that day, and the
mapping did not have to change, only move: `PiiClass` and `Irreversibility` are
now `PredicateSpec` fields, so they live in `schemas/verdict.py` beside
`ImpactLevel`, which is there for exactly the same reason. **The ontology may
not import from the pipeline**, which is what forced the move and is the right
direction anyway. They are re-exported here so §3.3's eight features can still
be read in one place.

Had these been `float` arguments, the ontology would have had to declare numbers
and §3.3's mapping would now exist in two places. That is the argument for the
enums, made concrete.

`Scope` stays defined here: it is the one of the three that is a fact about the
*write* rather than a declaration about the predicate, and nothing in the
ontology names it.

**Five do have producers, and they are functions here so the mapping is written
once.** `impact_declared` is `ImpactLevel.risk_feature`; `source_tier_risk`
inverts `SourceTier.grounding_multiplier`; `graph_fanout` reads
`GraphStore.degree`, which S3.4 built for this exact question; `novelty` reads
the cosine `VectorStore.search` now returns; `mutation_type` reads Layer 2's
`ConflictKind`.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from guardmem_core.schemas.risk import Irreversibility, PiiClass
from guardmem_core.schemas.verdict import ConflictKind

if TYPE_CHECKING:
    from guardmem_core.schemas.receipt import SourceTier

__all__ = [
    "Irreversibility",
    "MutationType",
    "PiiClass",
    "Scope",
    "graph_fanout",
    "novelty",
    "source_tier_risk",
]

# §3.3's `graph_fanout` normaliser: `log(1 + deg) / log(1 + 50)`. Fifty is the
# degree at which a node counts as maximally depended-upon, and the log is what
# makes the first few edges matter more than the fiftieth.
_FANOUT_SATURATION: Final = 50


class Scope(StrEnum):
    """How widely shared the namespace a write lands in is.  §3.3

    §3.3's "shared blast radius": a fact in one person's namespace is wrong for
    one person, and a fact in an organisation's namespace is wrong for everyone
    who reads it.
    """

    SESSION = "session"  # ephemeral, dies with the conversation
    USER = "user"  # one subject's own record
    ORG = "org"  # shared across an organisation

    @property
    def risk_feature(self) -> float:
        """§3.3's mapping: session .1, user .5, org 1.0."""
        return _SCOPE_FEATURES[self]


_SCOPE_FEATURES: dict[Scope, float] = {
    Scope.SESSION: 0.1,
    Scope.USER: 0.5,
    Scope.ORG: 1.0,
}


class MutationType(StrEnum):
    """What this write would do to memory.  §3.3

    Scored on what the candidate *would* cause if written, not on what was
    decided - `R` is computed before the decision matrix runs, and feeds it.
    """

    COEXIST = "coexist"  # adds a row, disturbs nothing
    REFINE = "refine"  # narrows an existing belief
    SUPERSEDE = "supersede"  # retires one
    RETRACT = "retract"  # withdraws a belief outright

    @property
    def risk_feature(self) -> float:
        """§3.3's mapping: coexist 0, refine .3, supersede .7, retract 1."""
        return _MUTATION_FEATURES[self]

    @classmethod
    def from_conflict(cls, kind: ConflictKind) -> MutationType:
        """Read the mutation a Layer 2 finding implies.

        Args:
            kind: What `detect` classified the candidate as.

        Returns:
            The mutation type. NONE and DUPLICATE coexist, REFINEMENT refines,
            and the three conflicting kinds supersede.

        Taken from `kind` rather than from `resolution_hint`, because the hint's
        vocabulary and §3.3's do not line up: `merge` and `escalate` are hints
        with no mutation type, and `refine` and `retract` are mutation types
        with no hint. `kind` maps cleanly - and it answers the right question,
        since an escalated CONTRADICTION still describes a write that would
        retire a live fact if a reviewer approved it. Scoring that as `coexist`
        because no hint said `supersede` would price the risk of the decision
        rather than of the write.

        A DUPLICATE is `COEXIST` and not lower: §2.4's merge writes no new row
        at all, so it is the least disturbing thing that can happen, and 0.0 is
        already the floor of this scale.

        `RETRACT` is unreachable today - nothing in the pipeline retracts a
        belief, and `StoredAssertion.retracted_at` is set by no code path. It is
        in the vocabulary because §3.3 names it and because the value it scores
        is the one a retraction would deserve.
        """
        return _CONFLICT_MUTATIONS.get(kind, cls.SUPERSEDE)


_MUTATION_FEATURES: dict[MutationType, float] = {
    MutationType.COEXIST: 0.0,
    MutationType.REFINE: 0.3,
    MutationType.SUPERSEDE: 0.7,
    MutationType.RETRACT: 1.0,
}

# The three conflicting kinds fall through to `SUPERSEDE` via the `.get`
# default above, so a `ConflictKind` added later scores as a supersession until
# somebody decides otherwise - the cautious direction, since the alternative
# default is 0.0 and would price a new conflict kind as harmless.
_CONFLICT_MUTATIONS: dict[ConflictKind, MutationType] = {
    ConflictKind.NONE: MutationType.COEXIST,
    ConflictKind.DUPLICATE: MutationType.COEXIST,
    ConflictKind.REFINEMENT: MutationType.REFINE,
}


def graph_fanout(degree: int) -> float:
    """How much depends on this subject.  §3.3

    Args:
        degree: Live edges touching the subject, from `GraphStore.degree` -
            which S3.4 built counting *both* directions for exactly this
            question: an entity forty assertions point at has the blast radius
            this feature is trying to price, even with no outgoing edges.

    Returns:
        `min(1, log(1 + degree) / log(1 + 50))`, in [0, 1].

    Raises:
        ValueError: `degree` is negative. A count cannot be, and a negative one
            would make the logarithm undefined rather than merely wrong.

    Logarithmic because the first few edges say much more than the fiftieth: an
    entity going from one dependant to five is a real change in blast radius,
    and one going from fifty to fifty-five is not.
    """
    if degree < 0:
        raise ValueError(f"graph degree cannot be negative, got {degree}")
    return min(1.0, math.log1p(degree) / math.log1p(_FANOUT_SATURATION))


def source_tier_risk(tier: SourceTier) -> float:
    """`1 - trust multiplier`.  §3.3

    Args:
        tier: The citation's source tier.

    Returns:
        One minus §3.2's grounding multiplier, in [0, 1].

    The same multiplier §3.2's `S_src` uses, inverted - so §3.2's inversion of
    `RULES.md` §4's ordering is inherited here too: a tool output scores *less*
    risky (0.15) than an unverified human (0.2). Recorded rather than silently
    corrected, and `SourceTier.grounding_multiplier` carries the argument.
    """
    return 1.0 - tier.grounding_multiplier


def novelty(nearest_cosine: float | None) -> float:
    """`1 - max cosine to existing memory`.  §3.3

    Args:
        nearest_cosine: The best similarity retrieval found, or `None` when
            there were no incumbents at all.

    Returns:
        `1 - cosine`, clamped to [0, 1]. `None` scores 1.0 - a claim with no
        neighbour in memory is maximally unprecedented, which is what §3.3's
        parenthesis means by "unprecedented claims are riskier".

    Clamped rather than merely subtracted, because cosine over unnormalised
    embeddings is genuinely negative sometimes - `ConflictReport.cosine` is
    bounded at -1 for that reason - and `1 - (-0.4)` is 1.4, a feature outside
    its own range and weighted as though it were more than maximal.
    """
    if nearest_cosine is None:
        return 1.0
    return min(1.0, max(0.0, 1.0 - nearest_cosine))
