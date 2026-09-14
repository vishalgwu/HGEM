"""What one labelled conflict pair is.  BUILD_NOTEBOOK.md S4.3

Separated from the rows themselves when the corpus passed `RULES.md` §2.4's
400-line module cap. The rows split three ways - `corpus_settled`,
`corpus_contradictions`, `corpus_coexist` - and all three need this type, so it
sits below them rather than inside one of them. `contradiction_corpus` assembles
the three into `PAIRS`, which is what tests import.
"""

from __future__ import annotations

from dataclasses import dataclass

from guardmem_core.schemas.base import ObjectValue
from guardmem_core.schemas.verdict import ConflictKind

__all__ = ["ConflictPair", "pair"]


@dataclass(frozen=True, slots=True)
class ConflictPair:
    """One labelled `(incumbent, candidate)` pair.

    Attributes:
        key: Stable slug, so a failure names the pair rather than an index.
        predicate: Which clinical predicate. Its `cardinality` is what decides
            whether (b) or (c) fires before the judge is consulted.
        incumbent_object: The stored value.
        incumbent_verbatim: The stored claim's supporting text.
        candidate_object: The proposed value.
        candidate_verbatim: The proposed claim's supporting text.
        expected: The `ConflictKind` a careful reader assigns.
        contradiction: What a competent judge would return for this pair, or
            `None` for the pairs (b) or (c) settle before any judge is asked -
            `None` is the assertion that no model call should happen at all.
        entail_fwd: `P(incumbent ⊨ candidate)`, same convention.
    """

    key: str
    predicate: str
    incumbent_object: ObjectValue
    incumbent_verbatim: str
    candidate_object: ObjectValue
    candidate_verbatim: str
    expected: ConflictKind
    contradiction: float | None = None
    entail_fwd: float = 0.0


def pair(
    key: str,
    predicate: str,
    incumbent: tuple[ObjectValue, str],
    candidate: tuple[ObjectValue, str],
    expected: ConflictKind,
    contradiction: float | None = None,
    entail_fwd: float = 0.0,
) -> ConflictPair:
    """Positional shorthand, so sixty rows read as a table."""
    return ConflictPair(
        key=key,
        predicate=predicate,
        incumbent_object=incumbent[0],
        incumbent_verbatim=incumbent[1],
        candidate_object=candidate[0],
        candidate_verbatim=candidate[1],
        expected=expected,
        contradiction=contradiction,
        entail_fwd=entail_fwd,
    )
