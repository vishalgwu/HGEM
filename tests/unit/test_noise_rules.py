"""The deterministic tier, and the gate S2.1 is measured by.  S2.1

Two kinds of test here, and the second is the one that matters.

The per-rule cases pin each of the four rules the tier can decide today, in both
directions - what fires, and the near miss that must not. Those near misses are
the point: "if I take penicillin I get hives" against "if I moved to Austin,
would my coverage change?", "remember that I'm allergic to penicillin" against
"summarize that again". Every one of them is a turn that would cost real recall
if the rule were a little looser, and a rule with no negative case is a rule
nobody has actually constrained.

`test_drop_precision_on_the_golden_corpus` is S2.1's DONE WHEN. It is a gate,
not an illustration: it asserts precision ≥ 0.95 over the forty labelled turns,
and it asserts a recall floor alongside, because precision alone is trivially
satisfied by a filter that drops nothing at all.
"""

from __future__ import annotations

import pytest

from fixtures.noise_corpus import GOLDEN_TURNS
from guardmem_core.pipeline.l1_extract.noise_rules import (
    is_ambiguous,
    normalise,
    rule_verdict,
)
from guardmem_core.schemas.turn import NoiseReason, Turn, TurnRole
from guardmem_core.types import TurnId

# S2.1: "precision on drops >= 0.95".
_PRECISION_GATE = 0.95

# Precision is free if nothing is ever dropped, so the gate needs a floor under
# it. Fifteen of the corpus's twenty labelled drops is below what the tier
# achieves today (seventeen) and above anything a broken tier would reach - it
# catches a regression without failing on a deliberate loosening of one rule.
_RULE_RECALL_FLOOR = 15


def _turn(text: str, *, role: TurnRole = TurnRole.USER, turn_id: str = "t1") -> Turn:
    return Turn(turn_id=TurnId(turn_id), role=role, text=text)


class TestNormalise:
    def test_folds_case_punctuation_and_whitespace(self) -> None:
        assert normalise("  Hold ON,   a second!! ") == "hold on a second"

    def test_removes_apostrophes_rather_than_splitting_on_them(self) -> None:
        # Both the ASCII and the typographic form, because a transcript pasted
        # out of a word processor carries the latter and a rule that saw
        # "don" + "t" would stop matching its own lexicon.
        assert normalise("don't") == "dont"
        assert normalise("don" + chr(0x2019) + "t") == "dont"

    def test_a_turn_of_pure_punctuation_normalises_to_nothing(self) -> None:
        assert normalise("...!?") == ""


class TestEphemeral:
    @pytest.mark.parametrize(
        "text",
        [
            "One sec, let me check.",
            "Okay, sounds good.",
            "Got it, thank you.",
            "Hold on a second.",
            "Um.",
            "Thanks!",
            "Good morning.",
        ],
    )
    def test_pure_filler_drops(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is NoiseReason.EPHEMERAL

    @pytest.mark.parametrize(
        "text",
        [
            # One content word is enough to survive, which is the whole safety
            # margin of the all-tokens-filler rule.
            "Check my sugar.",
            "Just the metformin.",
            # Bare affirmatives and negatives are deliberately not filler: in an
            # intake call this is the answer to "any allergies?".
            "No.",
            "Yes.",
            # Long enough that it has said something, even in filler words.
            "Okay okay okay okay okay okay okay.",
        ],
    )
    def test_near_misses_are_kept(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is None

    def test_an_empty_turn_is_not_a_drop(self) -> None:
        # Nothing to extract, but nothing to file under a class either, and
        # `DroppedTurn` requires a reason. Keeping costs nothing downstream.
        assert rule_verdict(_turn("   "), []) is None


class TestImperative:
    @pytest.mark.parametrize(
        "text",
        [
            "Summarize that again.",
            "Can you repeat the last one?",
            "Read that back to me, please.",
            "Ignore what I just said.",
            "Could you list them all?",
        ],
    )
    def test_agent_directed_with_no_content_drops(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is NoiseReason.IMPERATIVE

    @pytest.mark.parametrize(
        "text",
        [
            # The most important negative case in this file. It is addressed to
            # the assistant AND carries the central clinical fact of the call.
            "Remember that I'm allergic to penicillin.",
            "List my current medications: metformin and lisinopril.",
            "Delete the old address, it's 12 Oak Street now.",
            "Explain why my coverage lapsed in March.",
        ],
    )
    def test_an_imperative_carrying_a_fact_is_kept(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is None


class TestHypothetical:
    @pytest.mark.parametrize(
        "text",
        [
            "If I moved to Austin, would my coverage change?",
            "Hypothetically, what happens if I stop the metformin?",
            "Suppose the biopsy comes back positive - what then?",
            "What if I missed a dose?",
            "If I switched plans I might lose the referral.",
        ],
    )
    def test_speculation_drops(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is NoiseReason.HYPOTHETICAL

    @pytest.mark.parametrize(
        "text",
        [
            # An indicative conditional is a statement about an allergy, not a
            # speculation, and dropping it would lose a CRITICAL-impact fact.
            "If I take penicillin I get hives.",
            "I moved to Austin in January.",
            # "supposed" must not read as "suppose".
            "I'm supposed to take it with food.",
            "I'd like to update my address.",
        ],
    )
    def test_indicative_statements_are_kept(self, text: str) -> None:
        assert rule_verdict(_turn(text), []) is None


class TestRestatement:
    def test_an_exact_echo_of_an_earlier_turn_drops(self) -> None:
        said = _turn(
            "I've recorded a penicillin allergy with hives as the reaction.",
            role=TurnRole.ASSISTANT,
            turn_id="t1",
        )
        echo = _turn(said.text, turn_id="t2")
        assert rule_verdict(echo, [said]) is NoiseReason.RESTATEMENT

    def test_the_match_ignores_case_and_punctuation(self) -> None:
        said = _turn("Your pharmacy is CVS #4021.", turn_id="t1")
        echo = _turn("your pharmacy is cvs 4021", turn_id="t2")
        assert rule_verdict(echo, [said]) is NoiseReason.RESTATEMENT

    def test_the_first_occurrence_is_always_kept(self) -> None:
        said = _turn("My blood type is O negative.", turn_id="t1")
        assert rule_verdict(said, []) is None

    def test_a_shorter_paraphrase_is_not_an_exact_echo(self) -> None:
        # The cosine ≥ 0.93 half of §1.1's rule needs the embedder from S3.2.
        # Until then this is the classifier's call, not the rules'.
        said = _turn("I'm allergic to penicillin - it gives me hives.", turn_id="t1")
        shorter = _turn("I'm allergic to penicillin.", turn_id="t2")
        assert rule_verdict(shorter, [said]) is None

    def test_an_empty_turn_does_not_match_another_empty_turn(self) -> None:
        blank = _turn("...", turn_id="t1")
        assert rule_verdict(_turn("!!", turn_id="t2"), [blank]) is None


class TestThirdParty:
    @pytest.mark.parametrize(
        "text",
        ["My sister says she's gone vegan.", "My neighbour thinks the new clinic is terrible."],
    )
    def test_is_never_decided_by_a_rule(self, text: str) -> None:
        # Whether a claim about somebody else matters to this namespace is an
        # ontology question, and the ontology arrives at S3.5. Until then the
        # rules may only route it.
        assert rule_verdict(_turn(text), []) is None
        assert is_ambiguous(_turn(text), []) is True

    def test_a_relation_in_a_fact_about_the_subject_also_routes(self) -> None:
        # Family history is exactly the case the ontology has to settle, so it
        # must reach the classifier rather than being kept silently.
        assert is_ambiguous(_turn("My mother had breast cancer in her fifties."), []) is True


class TestAmbiguity:
    @pytest.mark.parametrize(
        "text",
        [
            "My blood type is O negative.",  # short
            "If I take penicillin I get hives.",  # conditional the rules kept
            "Explain why my coverage lapsed in March.",  # agent verb with content
        ],
    )
    def test_a_noise_signal_the_rules_declined_buys_a_model_call(self, text: str) -> None:
        assert is_ambiguous(_turn(text), []) is True

    def test_a_plain_substantive_turn_costs_nothing(self) -> None:
        assert (
            is_ambiguous(
                _turn("I take metformin, 500 milligrams, twice a day, with breakfast."), []
            )
            is False
        )

    def test_a_near_duplicate_is_routed_rather_than_dropped(self) -> None:
        # Close enough that a cosine threshold would probably call it a
        # restatement, different enough that it is not an exact echo - which is
        # exactly the band §1.1's rule covers and this tier cannot, until the
        # embedder lands at S3.2.
        said = _turn("Your preferred pharmacy is the CVS on Elm Street.", turn_id="t1")
        near = _turn("Your preferred pharmacy is a CVS on Elm Street.", turn_id="t2")
        assert rule_verdict(near, [said]) is None
        assert is_ambiguous(near, [said]) is True

    def test_an_empty_turn_is_not_worth_a_call(self) -> None:
        assert is_ambiguous(_turn("   "), []) is False


class TestGoldenCorpus:
    """S2.1's DONE WHEN."""

    def test_the_corpus_is_forty_turns_and_covers_every_class(self) -> None:
        assert len(GOLDEN_TURNS) == 40
        labelled = {item.expected for item in GOLDEN_TURNS if item.expected is not None}
        assert labelled == set(NoiseReason)
        assert any(item.expected is None for item in GOLDEN_TURNS)

    def test_every_turn_carries_the_labeller_s_reasoning(self) -> None:
        assert all(item.note.strip() for item in GOLDEN_TURNS)

    def test_drop_precision_on_the_golden_corpus(self) -> None:
        prior: list[Turn] = []
        correct = 0
        wrong: list[tuple[str, str]] = []
        for item in GOLDEN_TURNS:
            verdict = rule_verdict(item.turn, prior)
            if verdict is not None:
                if verdict is item.expected:
                    correct += 1
                else:
                    wrong.append((item.turn.turn_id, f"{verdict} != {item.expected}"))
            prior.append(item.turn)

        dropped = correct + len(wrong)
        assert dropped >= _RULE_RECALL_FLOOR, (
            f"the rule tier dropped only {dropped} of the corpus's labelled noise; "
            "precision is meaningless if the filter stops filtering"
        )
        precision = correct / dropped
        assert precision >= _PRECISION_GATE, f"precision {precision:.3f}, mislabelled: {wrong}"

    def test_the_rules_settle_most_of_the_corpus_without_a_model(self) -> None:
        # §1.1 budgets "rules for the cheap 70%". This is that number, measured
        # rather than asserted in prose: anything the rules decide, plus
        # anything they keep without wanting a second opinion.
        prior: list[Turn] = []
        settled = 0
        for item in GOLDEN_TURNS:
            if rule_verdict(item.turn, prior) is not None or not is_ambiguous(item.turn, prior):
                settled += 1
            prior.append(item.turn)
        assert settled / len(GOLDEN_TURNS) >= 0.70
