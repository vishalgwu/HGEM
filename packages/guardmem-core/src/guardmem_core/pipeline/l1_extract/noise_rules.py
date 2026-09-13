"""The deterministic half of the noise filter.  S2.1

`MEMORY_ENGINE.md` §1.1: "Implementation is a hybrid: rules for the cheap 70%,
FAST-tier classifier for the rest." This module is the rules. It never calls a
model and never does I/O, and every function in it is pure - which is what lets
the golden corpus pin its behaviour exactly rather than statistically.

**Every rule here is deliberately a strict subset of the class it detects.**
§1.1 defines a restatement by cosine ≥ 0.93 and a third-party claim by the
absence of an ontology licence; neither the embedder (S3.2) nor the ontology
(S3.5) exists yet. Rather than approximate a semantic threshold with a lexical
one - the precise confusion §3.1 warns about - each rule fires only on the part
it can decide soundly today, and `is_ambiguous` routes the remainder to the
classifier. S2.1's stated bias is the reason: *when unsure, keep*. A false drop
is invisible; a false keep is caught downstream.

What that costs, stated plainly: this tier under-detects, and it is meant to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from rapidfuzz import fuzz

from guardmem_core.schemas.turn import NoiseReason, Turn

__all__ = ["TurnHistory", "is_ambiguous", "normalise", "rule_verdict"]

# The four lexicons below are written as whitespace-delimited prose and split
# once at import. Every one carries a SIM905 suppression, and the reason is the same
# for all four: ruff formats the list literal that rule prefers one element per
# line, which turns ~150 words into ~150 lines and puts this module over the
# 400-line cap `RULES.md` §2.4 sets. SIM905 exists to save a runtime split of a
# tiny literal; these are module constants evaluated once at import, so there is
# nothing to save, and a word list is legible as prose in a way that 150 quoted
# strings is not. Keep them sorted - that is what makes an addition reviewable.

# Filler and acknowledgement tokens. A turn drops as ephemeral only when EVERY
# token is in here, so a single content word keeps it - that asymmetry is the
# whole safety margin.
#
# Deliberately absent: yes / no / yeah / yep / nope / right / correct. In an
# intake transcript a bare "No." is the answer to "any allergies?", and dropping
# it while keeping the question destroys the answer. They fall through to the
# classifier, which sees the conversation around them.
_FILLER: Final[frozenset[str]] = frozenset(
    """
    a afternoon again ah alright and anyway bye certainly
    check checking cool evening excuse fine for give go
    good goodbye got great hang hello hey hi hmm
    hold is it just k let look looking me
    mhm minute moment morning nice np of ok okay
    on one perfect please problem roger sec second sorry
    sounds sure thank thanks that there thing u uh
    uhm um understood welcome while worries you your
    """.split()  # noqa: SIM905
)

# The longest a turn can be and still be pure filler. Anything longer has said
# something, even if it said it badly.
_MAX_FILLER_TOKENS: Final = 6

# Verbs that address the assistant rather than describe the world.
_AGENT_VERBS: Final[frozenset[str]] = frozenset(
    """
    clarify continue delete explain forget ignore list read
    recap repeat rephrase reread restate say search show
    stop summarise summarize translate
    """.split()  # noqa: SIM905
)

# Politeness and modal scaffolding that can precede an agent verb.
_IMPERATIVE_PREFIXES: Final[tuple[tuple[str, ...], ...]] = (
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
    ("go", "ahead", "and"),
    ("please",),
    ("just",),
    ("now",),
)

# Anaphora and meta-reference. An agent-directed imperative whose object is only
# these has stated no fact; one that mentions anything else may well have.
_META: Final[frozenset[str]] = frozenset(
    """
    again all back everything for i it just last me my of
    one please point previous said that the them these this
    those to up what you your
    """.split()  # noqa: SIM905
)

# Explicit speculation, matched on word boundaries so that "supposed to take it
# daily" is not read as "suppose". A bare conditional is NOT here - see
# `_COUNTERFACTUAL`.
_HYPOTHETICAL: Final = re.compile(
    r"\b(?:hypothetically|theoretically|in theory|what if|suppose|supposing"
    r"|imagine|lets say|lets pretend|for the sake of argument)\b"
)

# A conditional is only speculative when it is also counterfactual. "if I take
# penicillin I get hives" is a fact about an allergy; "if I moved to Austin,
# would my coverage change?" is not. Restricted to two unambiguous modals on
# purpose - "were" and "had" inversions are ambiguous enough to be left to the
# classifier.
_COUNTERFACTUAL: Final[frozenset[str]] = frozenset({"would", "might"})

# Relation nouns. Used ONLY to route a turn to the classifier, never to drop it:
# whether a claim about somebody else matters to this namespace is an ontology
# question - family history, emergency contact, counterparty, dependant - and
# the ontology arrives at S3.5.
_RELATIONS: Final[frozenset[str]] = frozenset(
    """
    aunt boss boyfriend brother colleague cousin coworker
    dad daughter father friend girlfriend grandfather grandma
    grandmother grandpa husband mom mother mum neighbor
    neighbour nephew niece partner roommate sister son
    uncle wife
    """.split()  # noqa: SIM905
)

_SHORT_TURN_TOKENS: Final = 6

# Lexical near-duplicate threshold, for ROUTING ONLY. §1.1's restatement rule is
# cosine ≥ 0.93 over embeddings, which is a semantic claim; this is cheap
# lexical triage that decides whether to spend a model call, and it never
# decides a drop by itself.
_NEAR_DUPLICATE_RATIO: Final = 93.0

_APOSTROPHES: Final = re.compile(r"['\u2019]")
_NON_WORD: Final = re.compile(r"[^a-z0-9]+")


def normalise(text: str) -> str:
    """Lowercase, drop apostrophes, and reduce everything else to single spaces.

    Args:
        text: Raw turn text.

    Returns:
        The comparable form. Apostrophes are removed rather than replaced, so
        `don't` becomes `dont` rather than two tokens; every other punctuation
        mark becomes a boundary.
    """
    return _NON_WORD.sub(" ", _APOSTROPHES.sub("", text.lower())).strip()


@dataclass(slots=True)
class TurnHistory:
    """What has already been said in this trace, normalised once.

    Both backward-looking rules need the normalised form of every earlier turn:
    restatement compares for equality, and `is_ambiguous` scans for a lexical
    near-duplicate. Taking `Sequence[Turn]` and normalising inside those checks
    re-derived the same strings on every turn, which made the whole filter
    quadratic *in work that had already been done* - measured at 5,350
    `normalise` calls for a 100-turn conversation where linear is about 200, and
    159 ms for 400 turns against 2.8 ms for 50. This product's premise is
    long-running conversations, so that curve mattered.

    Normalising on `add` makes it linear, and keeping a `set` alongside the list
    makes the exact-match half O(1) rather than a scan. The near-duplicate half
    is inherently a comparison against every earlier turn and stays linear per
    turn; what it no longer does is recompute the strings it compares against.
    """

    _texts: list[str] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def add(self, turn: Turn) -> None:
        """Record a turn. Every turn, kept or dropped - both are "already said"."""
        normalised = normalise(turn.text)
        self._texts.append(normalised)
        self._seen.add(normalised)

    def contains(self, normalised: str) -> bool:
        """Has this exact normalised text been said before?"""
        return normalised in self._seen

    def texts(self) -> Sequence[str]:
        """Every earlier turn's normalised text, in order."""
        return self._texts


def _is_restatement(normalised: str, history: TurnHistory) -> bool:
    """Is this turn an exact repeat of something already said in this trace?

    §1.1 scopes the class to "the agent's own prior output echoed back" and
    detects it at cosine ≥ 0.93. This fires on an exact match after
    normalisation, from *any* speaker - wider in one direction, strictly
    narrower in the other, and sound in both:

    - Exact equality is cosine 1.0, so nothing dropped here would have survived
      the stated threshold.
    - Widening past the assistant costs no information, because the first copy
      is always kept. Nor does it cost corroboration: `StoredAssertion` counts
      *independent sources*, and two mentions in one conversation are one
      source, as its docstring says.

    The near-duplicate half of the rule needs the embedder from S3.2. Until
    then `is_ambiguous` routes those to the classifier.
    """
    return bool(normalised) and history.contains(normalised)


def _is_ephemeral(tokens: Sequence[str]) -> bool:
    """Is the turn nothing but filler?"""
    if not tokens or len(tokens) > _MAX_FILLER_TOKENS:
        return False
    return all(token in _FILLER for token in tokens)


def _strip_imperative_prefix(tokens: Sequence[str]) -> list[str]:
    """Remove one leading politeness or modal prefix, if present."""
    for prefix in _IMPERATIVE_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            return list(tokens[len(prefix) :])
    return list(tokens)


def _heads_an_agent_verb(tokens: Sequence[str]) -> bool:
    """Does the turn open with an instruction to the assistant?"""
    body = _strip_imperative_prefix(tokens)
    return bool(body) and body[0] in _AGENT_VERBS


def _is_imperative(tokens: Sequence[str]) -> bool:
    """Is this an instruction to the assistant that states no fact?

    Both halves are required. "remember that I'm allergic to penicillin" is
    addressed to the assistant and carries the most important fact in the
    conversation, so the object of the imperative has to be anaphora and
    nothing else before this fires.
    """
    if not _heads_an_agent_verb(tokens):
        return False
    return all(token in _META for token in _strip_imperative_prefix(tokens)[1:])


def _is_hypothetical(tokens: Sequence[str], normalised: str) -> bool:
    """Is this speculation rather than a claim?

    Either an explicit opener, or a conditional that is also counterfactual.
    """
    if _HYPOTHETICAL.search(normalised):
        return True
    return "if" in tokens and any(token in _COUNTERFACTUAL for token in tokens)


def rule_verdict(turn: Turn, history: TurnHistory) -> NoiseReason | None:
    """Decide a turn deterministically, or decline to.

    Args:
        turn: The turn under consideration.
        history: Every turn already seen in this trace. Only the restatement
            check reads it.

    Returns:
        The class to drop the turn under, or `None` when no rule is confident -
        which is not the same as "keep", only "the rules are done". Whether
        `None` is worth a model call is `is_ambiguous`'s question.

    Order matters, and ephemeral comes first. A repeated "thanks" satisfies both
    the ephemeral and the restatement rule, and filing the first copy under one
    class and the second under another would split one phenomenon across two
    buckets in the funnel. Nothing is lost by the ordering: a turn substantive
    enough to be a real echo cannot be pure filler, so restatement still catches
    every case ephemeral cannot.

    `NoiseReason.THIRD_PARTY` is never returned here. Deciding it needs the
    ontology (S3.5); until then it is a classifier judgement.
    """
    normalised = normalise(turn.text)
    tokens = normalised.split()
    if _is_ephemeral(tokens):
        return NoiseReason.EPHEMERAL
    if _is_restatement(normalised, history):
        return NoiseReason.RESTATEMENT
    if _is_imperative(tokens):
        return NoiseReason.IMPERATIVE
    if _is_hypothetical(tokens, normalised):
        return NoiseReason.HYPOTHETICAL
    return None


def _near_duplicate(normalised: str, history: TurnHistory) -> bool:
    """Is the turn lexically close to something already said, without matching it?

    Assumes a non-empty normalised form, which its only caller guarantees:
    `is_ambiguous` returns early on one. The guard that used to be here could
    not run, and an unreachable guard is worse than none - it reads as
    protection while the real protection lives somewhere else. Note what it was
    protecting against, since a second caller would need it: `fuzz.ratio("", "")`
    is 100, so two empty turns would read as near-duplicates of each other.
    """
    return any(
        fuzz.ratio(normalised, earlier) >= _NEAR_DUPLICATE_RATIO for earlier in history.texts()
    )


def is_ambiguous(turn: Turn, history: TurnHistory) -> bool:
    """Is this turn worth spending a FAST-tier classifier call on?

    Only asked of turns `rule_verdict` declined. A turn with no noise signal at
    all is kept without a model call - that is where §1.1's "cheap 70%" comes
    from - so every call this returns `True` for has a nameable reason:

    - it mentions a relation, which needs the ontology (S3.5) to settle and a
      model's judgement until then;
    - it is a conditional the counterfactual test did not catch;
    - it is agent-directed but carries content, so the imperative rule declined;
    - it is short without being pure filler;
    - it is lexically close to an earlier turn without matching it.

    Args:
        turn: The turn `rule_verdict` returned `None` for.
        history: Every turn already seen in this trace.

    Returns:
        Whether to include the turn in the batch sent to the classifier.
    """
    normalised = normalise(turn.text)
    tokens = normalised.split()
    if not tokens:
        return False
    return (
        any(token in _RELATIONS for token in tokens)
        or "if" in tokens
        or _heads_an_agent_verb(tokens)
        or len(tokens) <= _SHORT_TURN_TOKENS
        or _near_duplicate(normalised, history)
    )
