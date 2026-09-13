"""Raw conversation turns and what the noise filter did to them.  S2.1

Layer 1's *input* vocabulary. Everything else in this package describes a fact
the pipeline produced; these describe the text it was produced from, and the
record of what was thrown away before a model was ever paid to look at it.

**Nothing here is in `MEMORY_ENGINE.md` §0, and two of these names are used by
S2.1 without being defined anywhere.** The step's snippet takes
`Sequence[Turn]` and returns `NoiseResult`; neither exists in the spec suite.
Their content is fixed by §1.1 all the same - the five drop classes are named
there, and the sentence that governs this whole module is *"Everything dropped
is counted and sampled into the dashboard funnel - you must be able to see what
the filter is eating."* A `dropped` list of bare turns cannot satisfy that, so
`DroppedTurn` carries the reason and the tier that decided it.

`MemoryProposal` - the API object that carries these in - is still deferred to
the gateway (S8.1), for the reason `schemas/candidate.py` gives: its shape is
published in `MCP_INTEGRATION.md` §2.2 as a tool schema, and `memory.propose`
takes `content` as one string. Splitting that string into turns is the
gateway's job; this module is what it produces.

Import direction: this module depends on `base` and `types` only, so it hangs
off the bottom of the chain that `schemas/__init__.py` documents rather than
extending it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from guardmem_core.schemas.base import GMModel
from guardmem_core.types import TurnId

__all__ = [
    "DecidedBy",
    "DroppedTurn",
    "NoiseReason",
    "NoiseResult",
    "Turn",
    "TurnRole",
]


class TurnRole(StrEnum):
    """Who produced a turn.

    Load-bearing for two of the five drop classes rather than decorative:
    `MEMORY_ENGINE.md` §1.1 defines a restatement as "the agent's own prior
    output echoed back", and an imperative as one addressed *to the agent* -
    both of which are questions about who said what.
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Turn(GMModel):
    """One message in a proposal, before anything has been extracted from it.

    Deliberately four fields. A turn is raw input; every attribute the pipeline
    later needs - subject, predicate, span, provenance - is *derived* from it by
    a step that has not run yet, and S1.6's WATCH OUT applies here as much as
    there: every field added now becomes a database column and a migration.

    There is no `index`. Order is the order of the `Sequence` passed to
    `filter_noise`, which is where "a message already in this trace" gets its
    direction; a second, independent ordering field could disagree with it.

    Attributes:
        turn_id: Identity, and what a `DroppedTurn` points at.
        role: Who spoke.
        text: The message, verbatim. Not normalised, not trimmed - `Provenance`
            records character offsets into source text (`MEMORY_ENGINE.md`
            §1.3), so anything that shifts an offset here would shift every span
            derived from it downstream.
        captured_at: When the turn happened. Optional because a document or a
            tool payload pasted into a proposal often has no per-turn timestamp,
            and inventing one would make `valid_from` a fiction.
    """

    turn_id: TurnId
    role: TurnRole
    text: str
    captured_at: datetime | None = None


class NoiseReason(StrEnum):
    """Why Layer 1 dropped a turn - the five classes of `MEMORY_ENGINE.md` §1.1.

    A closed vocabulary, not free text, for the same reason
    `DecisionRecord.reason_codes` is: the dashboard funnel groups dropped items
    by class so an over-eager rule shows up as a spike in one of these rather
    than as a slow, invisible loss of recall.
    """

    EPHEMERAL = "ephemeral"  # "one sec, let me check"
    IMPERATIVE = "imperative"  # "summarize that again"
    RESTATEMENT = "restatement"  # the agent's own prior output, echoed back
    HYPOTHETICAL = "hypothetical"  # "if I moved to Austin, would..."
    THIRD_PARTY = "third_party"  # "my sister says she's vegan"


class DecidedBy(StrEnum):
    """Which tier of the filter decided a drop.

    §1.1 specifies a hybrid - "rules for the cheap 70%, FAST-tier classifier for
    the rest" - and the two failure modes are not the same one. An over-eager
    *rule* is a lexicon edit; an over-eager *classifier* is a prompt change and
    a nightly eval run. A funnel that cannot tell them apart sends the reader to
    the wrong file.
    """

    RULE = "rule"
    CLASSIFIER = "classifier"


class DroppedTurn(GMModel):
    """One turn the filter ate, and why.

    S2.1's DONE WHEN requires that "every dropped turn is recorded with a
    reason", and §1.1 requires that dropped turns be sampled into the funnel -
    so the turn itself is carried, not just its id. A sample that showed only
    ids would need a second lookup against text the gateway may have already
    hashed into blob storage.

    Attributes:
        turn: The dropped turn, whole.
        reason: Which of the five classes it fell into.
        decided_by: Which tier decided. See `DecidedBy`.
    """

    turn: Turn
    reason: NoiseReason
    decided_by: DecidedBy


class NoiseResult(GMModel):
    """What survived Layer 1's filter, and what did not.

    Two fields, exactly as S2.1's snippet returns. The count `§1.1` demands is
    `len(dropped)`, and it is what `ExtractionResult.dropped_noise` is filled
    from at S2.2.

    Notably absent: token and cost accounting for the classifier call. The
    filter may make one FAST-tier call, and `RULES.md` §3 does require every LLM
    call to record its tokens, cache hit and cost - but `LLMResponse` already
    carries all of it, and S13.1 is the step that decides where a call's
    accounting is *emitted*. Adding a second, partial copy of that here now
    would guarantee the two disagree by the time anything reads them.

    Attributes:
        kept: The turns that go on to extraction, in their original order.
        dropped: What was removed, in the order it was removed.
    """

    kept: list[Turn]
    dropped: list[DroppedTurn]
