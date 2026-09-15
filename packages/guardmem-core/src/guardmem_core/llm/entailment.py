"""`EntailFn`'s only producer.  BUILD_NOTEBOOK.md S5.1, MEMORY_ENGINE.md §3.1/§3.2

`pipeline/l3_score/entropy.py` declares `EntailFn` and deliberately never
chooses one: §3.1's reuse note says the clustering there "is the same machinery
as LID's semantic-entropy detector", and S5.1 asks that the callable stay
injected "so LID can back it later without touching this module". Nothing
injected one. `pipeline/deps.py` records the consequence - `entail` is one of
the three `Deps` members with no producer - and it is the largest of the three
by weight: §3.2 gives `w_H = 0.35` to the entropy term this clusters for and
`w_src = 0.25` to the grounding it measures, so **0.60 of `C` arrives through
this file**.

**Why an LLM and not a cross-encoder.** §2.2(a) asks for "DeBERTa-v3-MNLI class,
or BALANCED-tier LLM judge", and `l2_validate/nli.py` already took the second
road for the same reason this does: a cross-encoder means torch, which
`requirements/ml-local.txt` keeps out of the default install on purpose. The
seam is a plain callable, so a local model replaces this without touching a
consumer - which is the whole point of S5.1 having asked for a callable.

**The batch-then-lookup shape is not a convenience, it is what `entropy.py`
asked for.** Its docstring: "an async backend - `LLMJudge`, say - should
precompute the pairs it needs and pass a lookup, because every comparison here
is independent and none of them need to be sequential." `EntailFn` is sync, and
an async client cannot be called from it; more to the point, `cluster_meanings`
makes up to `K(K-1)` comparisons and one round trip each would be the single
slowest thing in the pipeline. So `LLMEntailer.lookup` takes every pair a caller
is about to need, scores them in one call, and returns the sync callable that
reads the answers back.

**An unasked pair raises.** The returned callable does not fall back to 0.0, or
to 0.5, or to a second model call. Returning a number for a pair that was never
scored is precisely the failure CHECKPOINT B's list calls "a scorer that
produces plausible numbers with no discriminative power", and S9.1 found a live
instance of it - a fixed seed made every entropy sample identical and `H_norm`
would have been 0 forever, with no error anywhere. A missing pair is a caller
bug; it raises, `_score_one` attributes it to its candidate, and the batch keeps
its other results.

**What this does not do.** It does not cache across calls. Two proposals that
share a pair pay for it twice, which is a real cost and the wrong thing to fix
here: a cache keyed on prompt text is `llm/cache.py` at S9.4, it belongs in
front of the client rather than inside one caller of it, and a cache added here
would have to be invalidated on a prompt-version bump this module does not own.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated, Final, NamedTuple

from pydantic import Field

from guardmem_core.errors import InjectionDetected, ValidationRejected
from guardmem_core.llm.base import Tier
from guardmem_core.prompts.loader import render
from guardmem_core.schemas.base import GMModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.pipeline.l3_score import EntailFn
    from guardmem_core.types import TraceId

__all__ = ["EntailmentBatch", "EntailmentPair", "LLMEntailer"]

_PROMPT_NAME: Final = "entail_pairs"
_PROMPT_VERSION: Final = 1

# Same length and same reason as the extractor's and the judge's: long enough
# that a model cannot emit it by chance, short enough not to spend tokens on it.
_CANARY_BYTES: Final = 16

# `entail(x, x)` is 1.0 by the definition of entailment, so it is answered here
# rather than bought from a provider. Not an optimisation dressed as a rule: a
# model asked whether a sentence entails itself can return 0.97, and §3.1's
# 0.8 `_SAME_MEANING` cut would still cluster correctly - but the *entropy*
# would differ between two runs over identical samples, which breaks the replay
# guarantee `RULES.md` §1.6 makes about this number.
_SELF_ENTAILMENT: Final = 1.0


class EntailmentPair(NamedTuple):
    """One question: does `premise` entail `hypothesis`?

    Attributes:
        premise: The text that would have to be true.
        hypothesis: The text that would then follow.

    A `NamedTuple` rather than a `GMModel` because it is used as a dictionary
    key - the lookup the caller gets back is keyed on exactly this pair, and a
    pydantic model is not hashable. It also makes the direction structural: both
    fields are `str` and the question is not symmetric, so `EntailmentPair(a, b)`
    is a different question from `EntailmentPair(b, a)` and the two cannot be
    silently transposed by a positional call.
    """

    premise: str
    hypothesis: str


class EntailmentBatch(GMModel):
    """The model's reply: one probability per pair, in order.

    Attributes:
        scores: `P(premise entails hypothesis)` per pair, positionally.

    Bounded at the field rather than checked afterwards, so a model that returns
    1.4 raises inside `model_validate_json` with the index in the message. The
    bound is load-bearing twice over: §3.1 compares against a 0.8 cut, and §3.2
    multiplies `S_src` into a composite that `ConfidenceReport` declares to be
    in [0, 1] - an out-of-range entailment produces a `C` above 1.0 that fails
    validation on some candidates and not others.

    A wrapper rather than a bare list because `output_schema` in the prompt
    frontmatter names a model, and `GMModel`'s `extra="forbid"` is what makes a
    hallucinated field raise instead of being dropped on the way to a threshold.
    """

    scores: list[Annotated[float, Field(ge=0.0, le=1.0)]]


class LLMEntailer:
    """An `EntailFn` producer backed by the BALANCED tier and `entail_pairs@v1`.

    BALANCED because `MEMORY_ENGINE.md` §3.5 puts "conflict adjudication and
    NLI" there and this is NLI, asked one direction at a time. Not FAST: the
    number decides whether a fact is believed without a human, and §3.5 budgets
    the tier ladder for exactly this kind of call.

    Bound to a client rather than constructing one, like `LLMJudge` - S1.7's
    protocols are what make the provider an operator decision, and a component
    that picks its own provider takes that decision away.
    """

    def __init__(self, llm: LLMClient) -> None:
        """Bind a client.

        Args:
            llm: The client. Called once per `lookup`, whatever the pair count.
        """
        self._llm = llm

    async def lookup(self, pairs: Sequence[EntailmentPair], *, trace_id: TraceId) -> EntailFn:
        """Score every pair in one call and return the sync callable over them.

        Args:
            pairs: Every pair the caller is about to ask about. Duplicates and
                self-pairs are free - see below - so a caller should pass the
                pairs it *might* need rather than computing the minimal set and
                risking a miss.
            trace_id: This proposal's trace, for the errors below.

        Returns:
            An `EntailFn`. It is total over `pairs` and raises on anything else.

        Raises:
            InjectionDetected: the reply echoed the canary token.
            ValidationRejected: the reply did not validate, or carried the wrong
                number of scores.
            ProviderUnavailable: the model is unreachable. Retryable.
            BudgetExceeded: the tenant's cap is reached.

        Deduplicates before calling: `cluster_meanings` asks both directions of
        every sample pair and the same rendered claim appears in several of
        them, so the pair list a caller assembles has repeats in it by
        construction. Paying twice for one question would be the largest
        avoidable cost in the pipeline, and the two answers could differ.
        """
        wanted = [pair for pair in dict.fromkeys(pairs) if pair.premise != pair.hypothesis]
        scores = await self._score(wanted, trace_id=trace_id) if wanted else []
        return _reader(dict(zip(wanted, scores, strict=True)))

    async def _score(self, pairs: Sequence[EntailmentPair], *, trace_id: TraceId) -> list[float]:
        """One BALANCED call over the deduplicated pairs.

        Args:
            pairs: The distinct, non-self pairs.
            trace_id: For the errors.

        Returns:
            One score per pair, in the order sent.

        Raises:
            InjectionDetected: the reply echoed the canary.
            ValidationRejected: see `_parse`.
        """
        canary = secrets.token_hex(_CANARY_BYTES)
        prompt = render(
            _PROMPT_NAME,
            _PROMPT_VERSION,
            {"pairs": _numbered(pairs), "canary": canary},
        )
        reply = await self._llm.complete(
            prompt=prompt.text, schema=EntailmentBatch, tier=Tier.BALANCED, temperature=0.0
        )
        if any(canary in sample for sample in reply.samples):
            raise InjectionDetected("entailment scoring echoed the canary token", trace_id=trace_id)
        return _parse(reply.samples[0], expected=len(pairs), trace_id=trace_id)


def _reader(answers: dict[EntailmentPair, float]) -> EntailFn:
    """Close over the scored pairs and answer from them alone.

    Args:
        answers: Every pair that was scored.

    Returns:
        The `EntailFn`. Self-pairs answer 1.0 without a lookup; everything else
        must be present.

    Raises:
        KeyError: raised by the returned callable, for a pair nobody scored.

    That raise is the point of this function. See the module docstring: a
    default return here would be a number nothing measured, arriving inside a
    confidence score that decides whether a fact is believed.
    """

    def entail(premise: str, hypothesis: str) -> float:
        if premise == hypothesis:
            return _SELF_ENTAILMENT
        pair = EntailmentPair(premise, hypothesis)
        if pair not in answers:
            raise KeyError(
                "entailment was not scored for this pair, and this callable does "
                "not invent one. Pass every pair to `LLMEntailer.lookup` before "
                f"scoring. Missing premise={premise!r} hypothesis={hypothesis!r}"
            )
        return answers[pair]

    return entail


def _numbered(pairs: Sequence[EntailmentPair]) -> str:
    """Render the pairs as a numbered list the model can count with.

    Args:
        pairs: The pairs to send.

    Returns:
        One block per pair, numbered from 1.

    Numbered for `LLMJudge._numbered`'s reason - the reply is paired back by
    position and an unnumbered list makes a dropped entry invisible to both
    sides - and labelled `premise`/`hypothesis` rather than laid out as two bare
    lines, because the question is directional and a reader of the rendered
    prompt should be able to see which way round it was asked.
    """
    return "\n\n".join(
        f"{index}.\n  premise: {pair.premise}\n  hypothesis: {pair.hypothesis}"
        for index, pair in enumerate(pairs, start=1)
    )


def _parse(sample: str, *, expected: int, trace_id: TraceId) -> list[float]:
    """Validate one reply into scores.

    Args:
        sample: The completion.
        expected: How many pairs were sent.
        trace_id: For the errors.

    Returns:
        The scores, in reply order.

    Raises:
        ValidationRejected: the reply is not an `EntailmentBatch`, or the count
            does not match. The count check is `LLMJudge._parse`'s, for the same
            reason: scores are paired back to pairs positionally, so a short
            list silently answers one pair with another pair's number - and here
            that lands in `H_norm`, where it is a plausible value nothing can
            distinguish from a measured one.
    """
    try:
        batch = EntailmentBatch.model_validate_json(sample)
    except ValueError as exc:
        raise ValidationRejected(
            f"entailment scoring did not return EntailmentBatch: {exc}", trace_id=trace_id
        ) from exc
    if len(batch.scores) != expected:
        raise ValidationRejected(
            f"entailment scoring returned {len(batch.scores)} scores for {expected} "
            "pairs; they are paired positionally, so a mismatch would score the "
            "wrong pair",
            trace_id=trace_id,
        )
    return batch.scores
