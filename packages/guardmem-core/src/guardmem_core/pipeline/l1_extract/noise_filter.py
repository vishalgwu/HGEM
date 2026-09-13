"""Layer 1's noise filter: rules first, model second.  S2.1

`MEMORY_ENGINE.md` §1.1. Drops ephemeral, imperative, restated, hypothetical and
third-party turns before any billable call - roughly 35% of input, which is the
first and cheapest term in the ≥55% token-savings target of `PRD.md` §6.5.

The shape is the one S2.1 specifies: deterministic rules settle the cheap
majority (`noise_rules.py`), and whatever they decline that still carries a
noise signal goes to a FAST-tier classifier in **one batched call**, never one
call per turn.

**The bias is asymmetric and it runs through every decision below: when unsure,
keep.** A false keep is scored, conflict-checked and possibly reviewed by a
human before anything is stored, so it gets caught. A false drop is never seen
again - it does not appear in the funnel as an error, it appears as recall that
was quietly never there. Every ambiguous case in this module therefore resolves
toward keeping, including the failure cases:

- the classifier returns a turn we did not send → ignored;
- it returns a turn twice → that turn is kept;
- it says `drop` with no reason → kept, because S2.1 requires every dropped
  turn to carry one;
- its reply does not parse → every ambiguous turn is kept;
- it names a turn we sent but never answers for another → that one is kept.

That last set is `RULES.md` non-negotiable #3 ("fail closed") read for Layer 1.
Closed *here* means keeping: a kept turn stays inside governance, where the
decision matrix can still refuse it. Dropping is the only irreversible act this
module can perform.

A provider failure is different and is **not** swallowed. `ProviderUnavailable`
and `BudgetExceeded` propagate untouched: the extractor two steps later needs
the same provider, so catching them here would defer an identical failure while
hiding which stage first saw it, and `ARCHITECTURE.md` §4 already says what
happens on a dead provider - the proposal parks in `pending_eval`.

**Three departures from the step's snippet**, each forced:

1. `namespace` is a parameter. §1.1's third-party class is defined as
   "subject ≠ namespace subject"; the rule cannot be stated without it.
2. `trace_id` is a parameter. `RULES.md` §2.3 requires every raise inside the
   pipeline to attach it, and this module can raise `InjectionDetected`.
3. Ambiguity is recorded while the rules run rather than recomputed afterwards.
   The snippet's `[t for t in kept if _is_ambiguous(t)]` re-examines turns the
   rules positively kept and loses the `prior` context each verdict was taken
   against.
"""

from __future__ import annotations

import secrets
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import ValidationError

from guardmem_core.errors import InjectionDetected
from guardmem_core.llm.base import LLMClient, LLMResponse
from guardmem_core.pipeline.l1_extract.noise_rules import is_ambiguous, rule_verdict
from guardmem_core.prompts.loader import render
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.turn import DecidedBy, DroppedTurn, NoiseReason, NoiseResult, Turn
from guardmem_core.types import Namespace, TraceId, TurnId

__all__ = ["NoiseClassification", "NoiseVerdict", "filter_noise"]

_PROMPT_NAME: Final = "classify_noise"
_PROMPT_VERSION: Final = 1

# Long enough that a model cannot produce it by chance, short enough to stay
# cheap in the prompt. Same construction S2.2 uses for the extraction canary.
_CANARY_BYTES: Final = 8


class NoiseVerdict(GMModel):
    """The classifier's answer for one turn.

    Attributes:
        turn_id: Which turn. Echoed back by the model, so it is validated
            against what was actually sent rather than trusted.
        drop: Whether the turn is noise.
        reason: Which of the five classes, when `drop` is true. A drop without
            one is discarded - see the module docstring.
    """

    turn_id: TurnId
    drop: bool
    reason: NoiseReason | None = None


class NoiseClassification(GMModel):
    """One batched reply. Named by `prompts/classify_noise/v1.md`'s frontmatter.

    Lives here rather than in `guardmem_core.schemas` on purpose: it is the wire
    shape of one prompt's reply, not a domain object crossing a public boundary,
    and it changes when that prompt version changes. `LLMResponse` sits outside
    the schema layer for the same reason.
    """

    verdicts: list[NoiseVerdict]


def _render_turns(turns: Sequence[Turn]) -> str:
    """Serialise turns for the prompt, one delimited block each."""
    return "\n".join(
        f'<turn id="{turn.turn_id}" role="{turn.role.value}">\n{turn.text}\n</turn>'
        for turn in turns
    )


def _confident_drops(response: LLMResponse, submitted: Sequence[Turn]) -> dict[TurnId, NoiseReason]:
    """Extract the drops that are safe to act on from one classifier reply.

    Args:
        response: What the model returned. Only sample 0 is read - the filter
            asks for `n=1`, and `MEMORY_ENGINE.md` §1.2 reserves multi-sampling
            for entropy, which is not what this call is for.
        submitted: Exactly the turns that were sent.

    Returns:
        Turn id to drop class, containing only verdicts that are unambiguous:
        the id was sent, it appears exactly once, `drop` is true, and a reason
        is given. Everything else is silently omitted, which means kept.
    """
    try:
        parsed = NoiseClassification.model_validate_json(response.samples[0])
    except ValidationError:
        # A malformed reply is a bad answer, not an outage. The conservative
        # response is to keep every turn in the batch rather than to raise and
        # fail a proposal over an optional cost optimisation.
        return {}
    sent = {turn.turn_id for turn in submitted}
    seen = Counter(verdict.turn_id for verdict in parsed.verdicts)
    return {
        verdict.turn_id: verdict.reason
        for verdict in parsed.verdicts
        if verdict.drop
        and verdict.reason is not None
        and verdict.turn_id in sent
        and seen[verdict.turn_id] == 1
    }


async def _classify(
    turns: Sequence[Turn],
    llm: LLMClient,
    *,
    namespace: Namespace,
    trace_id: TraceId,
) -> dict[TurnId, NoiseReason]:
    """Ask the FAST tier about the turns the rules declined. One call, batched.

    Args:
        turns: The ambiguous turns.
        llm: The client to ask.
        namespace: Whose memory this is, so the model can tell a claim about the
            subject from a claim about somebody else.
        trace_id: Attached to `InjectionDetected` if the canary leaks.

    Returns:
        The drops that survived `_confident_drops`.

    Raises:
        InjectionDetected: if the canary token appears in the model's output.
            `RULES.md` §3 treats that as a confirmed injection rather than a
            heuristic - the content inside the delimiters persuaded the model to
            repeat something it was told never to repeat.
        ProviderUnavailable: propagated from the client.
        BudgetExceeded: propagated from the client.
    """
    canary = secrets.token_hex(_CANARY_BYTES)
    prompt = render(
        _PROMPT_NAME,
        _PROMPT_VERSION,
        {"subject": namespace, "canary": canary, "turns": _render_turns(turns)},
    )
    response = await llm.complete(
        prompt=prompt.text,
        schema=NoiseClassification,
        # From the prompt file's frontmatter, not hard-coded. That is what makes
        # the declared tier load-bearing: a prompt authored for FAST cannot be
        # routed onto FRONTIER by an edit at this call site.
        tier=prompt.spec.tier,
        temperature=0.0,
        n=1,
    )
    if any(canary in sample for sample in response.samples):
        raise InjectionDetected("noise classifier echoed the canary token", trace_id=trace_id)
    return _confident_drops(response, turns)


def _apply(
    kept: Sequence[Turn],
    dropped: list[DroppedTurn],
    drops: Mapping[TurnId, NoiseReason],
) -> NoiseResult:
    """Move the classifier's drops out of `kept`, preserving order.

    Args:
        kept: What the rules kept, in conversation order.
        dropped: What the rules dropped. Extended, not replaced - the funnel
            needs both tiers in one list.
        drops: The confident classifier drops, by turn id. A turn absent from
            this mapping survives, which is what makes every failure mode in
            the module docstring resolve toward keeping.

    Returns:
        The filtered result.
    """
    survivors: list[Turn] = []
    for turn in kept:
        classified = drops.get(turn.turn_id)
        if classified is None:
            survivors.append(turn)
        else:
            dropped.append(
                DroppedTurn(turn=turn, reason=classified, decided_by=DecidedBy.CLASSIFIER)
            )
    return NoiseResult(kept=survivors, dropped=dropped)


async def filter_noise(
    turns: Sequence[Turn],
    llm: LLMClient,
    *,
    namespace: Namespace,
    trace_id: TraceId,
) -> NoiseResult:
    """Drop the turns not worth extracting from, and say why for each one.

    Args:
        turns: The proposal's turns, in conversation order. Order is meaningful:
            the restatement class is about what was *already* said.
        llm: FAST-tier client for the ambiguous remainder. Called at most once,
            and not at all when the rules settle everything.
        namespace: Whose memory this is, e.g. `"patient:8812"`.
        trace_id: This proposal's trace.

    Returns:
        The kept turns in their original order, and a `DroppedTurn` for each
        removal carrying its class and the tier that decided it.

    Raises:
        InjectionDetected: the classifier echoed the canary token.
        ProviderUnavailable: the FAST tier is unreachable. Retryable.
        BudgetExceeded: the tenant's cap is reached.
    """
    kept: list[Turn] = []
    dropped: list[DroppedTurn] = []
    ambiguous: list[Turn] = []
    prior: list[Turn] = []

    for turn in turns:
        reason = rule_verdict(turn, prior)
        if reason is None:
            kept.append(turn)
            if is_ambiguous(turn, prior):
                ambiguous.append(turn)
        else:
            dropped.append(DroppedTurn(turn=turn, reason=reason, decided_by=DecidedBy.RULE))
        # Every turn, kept or dropped, is something "already said in this
        # trace" for the turns that follow it.
        prior.append(turn)

    if not ambiguous:
        return NoiseResult(kept=kept, dropped=dropped)

    drops = await _classify(ambiguous, llm, namespace=namespace, trace_id=trace_id)
    return _apply(kept, dropped, drops)
