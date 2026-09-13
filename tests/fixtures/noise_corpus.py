"""Forty hand-labelled turns, in conversation order.  S2.1

S2.1's DONE WHEN: "golden test over 40 hand-labelled turns - precision on drops
>= 0.95, and every dropped turn is recorded with a reason."

These are the forty. They are one continuous clinical intake call rather than
forty independent samples, because two of the five classes are only definable
against what came before: a restatement is an echo of an earlier turn, and a
near-duplicate is near-duplicate *of something*. A shuffled bag of turns would
silently stop testing either.

**The labels are the human's, and they are not a prediction of what the rules
do.** Several turns are labelled as noise that the deterministic tier cannot
catch today and is not meant to - the two `third_party` claims need the ontology
(S3.5) to settle, and turn 33 is an imperative whose verb is outside the
agent-verb lexicon. They are here on purpose. A corpus that only contained what
the implementation already handles would measure the implementation against
itself, and the recall gap is the honest part of the picture: `RULE_RECALL_FLOOR`
in the test is what stops precision being won by dropping nothing.

Labelling rule applied throughout, from `MEMORY_ENGINE.md` §1.1 and S2.1's
stated bias: a turn is noise only if extracting from it could yield nothing
durable about the subject. Anything that could produce a fact - however small,
however likely to be rejected later - is `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from guardmem_core.schemas.turn import NoiseReason, Turn, TurnRole
from guardmem_core.types import Namespace, TurnId

__all__ = ["GOLDEN_NAMESPACE", "GOLDEN_TURNS", "GoldenTurn", "golden_turns"]

GOLDEN_NAMESPACE: Final = Namespace("patient:8812")


@dataclass(frozen=True, slots=True)
class GoldenTurn:
    """One labelled turn.

    Attributes:
        turn: The turn itself.
        expected: The class a human assigned, or `None` for keep.
        note: Why. Written down because the label is the gate - a corpus whose
            reasoning lives only in the labeller's head cannot be argued with,
            and `MEMORY_ENGINE.md` §1.1's whole point is that what the filter
            eats must be inspectable.
    """

    turn: Turn
    expected: NoiseReason | None
    note: str


def _turn(index: int, role: TurnRole, text: str) -> Turn:
    """Build a corpus turn with a stable id."""
    return Turn(turn_id=TurnId(f"t{index:02d}"), role=role, text=text)


_U: Final = TurnRole.USER
_A: Final = TurnRole.ASSISTANT

# (role, text, expected, note) in conversation order.
_CORPUS: Final[tuple[tuple[TurnRole, str, NoiseReason | None, str], ...]] = (
    (_A, "Good morning.", NoiseReason.EPHEMERAL, "greeting, no content"),
    (_U, "Morning.", NoiseReason.EPHEMERAL, "greeting, no content"),
    (
        _U,
        "I'm allergic to penicillin - it gives me hives all up my arms.",
        None,
        "the central clinical fact of the call",
    ),
    (_A, "One sec, let me check.", NoiseReason.EPHEMERAL, "filler while the agent works"),
    (
        _U,
        "My date of birth is the third of March, 1978.",
        None,
        "identity attribute",
    ),
    (_U, "Um.", NoiseReason.EPHEMERAL, "pure disfluency"),
    (
        _U,
        "I take metformin, 500 milligrams, twice a day.",
        None,
        "medication with dose and frequency",
    ),
    (_U, "Hold on a second.", NoiseReason.EPHEMERAL, "filler"),
    (
        _U,
        "If I take penicillin I get hives.",
        None,
        "indicative conditional - a real statement about the allergy, not speculation",
    ),
    (
        _U,
        "If I moved to Austin, would my coverage change?",
        NoiseReason.HYPOTHETICAL,
        "counterfactual; nothing here is true yet",
    ),
    (_U, "Okay, sounds good.", NoiseReason.EPHEMERAL, "acknowledgement"),
    (
        _U,
        "Dr. Alvarez has been my primary care provider since 2019.",
        None,
        "one-cardinality predicate with a start date",
    ),
    (_A, "Summarize that again.", NoiseReason.IMPERATIVE, "instruction to the agent, no content"),
    (
        _U,
        "I switched pharmacies last month; I use the CVS on Elm Street now.",
        None,
        "supersedes the preferred pharmacy",
    ),
    (
        _U,
        "Hypothetically, what happens if I stop the metformin?",
        NoiseReason.HYPOTHETICAL,
        "explicit speculation",
    ),
    (_U, "No.", None, "a one-word answer is still an answer; the question gives it content"),
    (
        _U,
        "My insurance is Blue Cross, group number 4471-B.",
        None,
        "coverage attributes",
    ),
    (_U, "Can you repeat the last one?", NoiseReason.IMPERATIVE, "agent-directed, no content"),
    (
        _U,
        "My mother had breast cancer in her fifties.",
        None,
        "about somebody else, but family history is a fact about this patient's record",
    ),
    (_U, "Got it, thank you.", NoiseReason.EPHEMERAL, "acknowledgement"),
    (
        _U,
        "My sister says she's gone vegan.",
        NoiseReason.THIRD_PARTY,
        "purely social claim about a third party; needs the ontology (S3.5) to decide",
    ),
    (
        _U,
        "The rash started about four days ago, mostly on my forearms.",
        None,
        "symptom with onset",
    ),
    (
        _U,
        "Suppose the biopsy comes back positive - what then?",
        NoiseReason.HYPOTHETICAL,
        "explicit speculation",
    ),
    (
        _A,
        "I've recorded a penicillin allergy with hives as the reaction.",
        None,
        "first statement of the assertion; the echo below is what is redundant",
    ),
    (
        _U,
        "I've recorded a penicillin allergy with hives as the reaction.",
        NoiseReason.RESTATEMENT,
        "the agent's line, echoed back verbatim",
    ),
    (_U, "Read that back to me, please.", NoiseReason.IMPERATIVE, "agent-directed, no content"),
    (
        _U,
        "I'm pregnant, about fourteen weeks.",
        None,
        "high-impact clinical state",
    ),
    (_U, "Thanks!", NoiseReason.EPHEMERAL, "acknowledgement"),
    (
        _U,
        "I'd like to add my husband as my emergency contact.",
        None,
        "mentions a relation, but the fact is about this patient's record",
    ),
    (
        _U,
        "My neighbour thinks the new clinic is terrible.",
        NoiseReason.THIRD_PARTY,
        "an opinion held by somebody else about something else",
    ),
    (_U, "Penicillin. P-E-N-I-C-I-L-L-I-N.", None, "spelling out a value already given"),
    (
        _U,
        "I don't smoke, and I have maybe two drinks a week.",
        None,
        "social history, two facts",
    ),
    (
        _U,
        "Let me know when the referral goes through.",
        NoiseReason.IMPERATIVE,
        "a request to the agent; states nothing durable. The rule tier misses this one - "
        "'let' is not an agent verb - and that is what the recall floor is for",
    ),
    (_U, "I had my gallbladder out in 2015.", None, "surgical history"),
    (_U, "Ignore what I just said.", NoiseReason.IMPERATIVE, "agent-directed retraction"),
    (
        _U,
        "I get migraines maybe twice a month.",
        None,
        "condition with frequency",
    ),
    (_U, "My blood type is O negative.", None, "identity attribute"),
    (
        _U,
        "I'm allergic to penicillin.",
        None,
        "a shorter restatement of turn 3 - lexically far enough that it is not an exact "
        "echo, so it stays a keep; the near-duplicate half of the rule needs S3.2",
    ),
    (
        _U,
        "I moved to Austin in January.",
        None,
        "indicative - contrast with the counterfactual above",
    ),
    (_A, "Perfect, thanks.", NoiseReason.EPHEMERAL, "acknowledgement"),
)

GOLDEN_TURNS: Final[tuple[GoldenTurn, ...]] = tuple(
    GoldenTurn(turn=_turn(index, role, text), expected=expected, note=note)
    for index, (role, text, expected, note) in enumerate(_CORPUS, start=1)
)


def golden_turns() -> list[Turn]:
    """The corpus as plain turns, in order, for end-to-end use."""
    return [item.turn for item in GOLDEN_TURNS]
