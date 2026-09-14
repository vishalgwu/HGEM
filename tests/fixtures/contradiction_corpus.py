"""Sixty hand-labelled conflict pairs.  BUILD_NOTEBOOK.md S4.3

S4.3: "the 60-pair contradiction probe set classifies >= 90% correctly. Build
that set by hand today - it is the fastest quality signal you will have all
month."

**What this measures, and what it does not.** Each pair carries a predicate, an
incumbent, a candidate, and the `ConflictKind` a careful reader would assign.
Eleven are settled by arithmetic before any model is consulted - a `ONE` or
`ONE_PER_TIME` predicate with a differing value - and those measure `detect` end
to end; `contradiction=None` on such a pair is the assertion that no judge is
called at all.

The other forty-nine turn on natural-language inference, and there is no NLI
model to run: `GM_ANTHROPIC_API_KEY` is empty and `RULES.md` §5 bans live model
calls from the unit suite outright. So each of those carries the numbers a
competent judge would return, hand-set, and the probe measures **`detect`'s
reading of those numbers** - §2.2(a)'s thresholds and the order the three checks
run in.

That is a real measurement of the part this repository owns and a fake
measurement of the part it does not, so the distinction is written here rather
than implied. The *judge's* accuracy against these same pairs is the nightly
eval gate's question (`RULES.md` §5, S27.1), and this corpus is the input it
will use - which is what makes "the fastest quality signal you will have all
month" true even though today it cannot be pointed at a model.

**The labels are a careful reader's, not a prediction of what the code does.**
Several pairs are deliberately near-misses of the thresholds: `ambiguous-*` sit
in §2.3's 0.3-0.65 band where "the NLI is unsure, so a human or frontier model
decides", and a corpus containing only comfortable cases would measure nothing.

Two things a pair must never be: a duplicate labelled as a contradiction (that
retires a fact somebody relies on) and a contradiction labelled as coexistence
(that is the `contradiction_escape_rate` metric going quietly wrong). The set is
weighted towards those two mistakes, and it is split into modules along exactly
that line - `corpus_settled` (no judge is asked), `corpus_contradictions` (must
be caught), `corpus_coexist` (must not be flagged). The split came from
`RULES.md` §2.4's 400-line cap; grouping by which mistake a row guards against
was the seam already written into the paragraph above.
"""

from __future__ import annotations

from typing import Final

from fixtures.conflict_pair import ConflictPair
from fixtures.corpus_coexist import COEXIST
from fixtures.corpus_contradictions import CONTRADICTIONS
from fixtures.corpus_settled import SETTLED

__all__ = ["PAIRS", "ConflictPair"]

PAIRS: Final[tuple[ConflictPair, ...]] = (*SETTLED, *CONTRADICTIONS, *COEXIST)
