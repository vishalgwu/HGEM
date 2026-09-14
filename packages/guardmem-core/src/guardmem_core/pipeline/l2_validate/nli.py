"""Where entailment and contradiction numbers come from.  BUILD_NOTEBOOK.md S4.3

`MEMORY_ENGINE.md` §2.2(a) asks for a cross-encoder NLI model "DeBERTa-v3-MNLI
class, or BALANCED-tier LLM judge for long/typed objects", and S4.3 says to
start with the judge and "swap in a local cross-encoder in week 2 if latency
demands it". A swap named in the step is a `Protocol` in this codebase, the same
way `VectorStore` and `GraphStore` are - so `NLIJudge` is the contract and
`LLMJudge` is today's only implementation.

Separate from `conflict.py` because the two answer different questions.
*How do we get these numbers* is a model choice with a latency budget and a
replacement already scheduled; *what the numbers mean for this candidate* is
§2.2's rules and does not change when the model does. Putting them together
would make the week-2 swap touch the file that holds the safety logic.

**One call for the whole incumbent set, not one per pair.** §2.2 retrieves up to
ten incumbents and every one has to be judged against the candidate. Ten
BALANCED calls per candidate would be the single largest cost in the pipeline
and `MEMORY_ENGINE.md` §3.5 budgets tiers deliberately; the prompt takes the
list and returns one judgement per entry, in order.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import Field

from guardmem_core.errors import InjectionDetected, ValidationRejected
from guardmem_core.llm.base import Tier
from guardmem_core.prompts.loader import render
from guardmem_core.schemas.base import GMModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.types import TraceId

__all__ = ["AdjudicationBatch", "Judgement", "LLMJudge", "NLIJudge"]

_PROMPT_NAME: Final = "adjudicate_conflict"
_PROMPT_VERSION: Final = 1

# Same length as the extractor's, and for the same reason: long enough that a
# model cannot emit it by chance, short enough not to spend tokens on it.
_CANARY_BYTES: Final = 16


class Judgement(GMModel):
    """What the judge said about one `(incumbent, candidate)` pair.

    Attributes:
        entail_fwd: `P(incumbent ⊨ candidate)`. §2.2(a)'s forward direction.
        entail_rev: `P(candidate ⊨ incumbent)`.
        contradiction: `P(mutually exclusive)`.

    Both directions, because entailment is not symmetric and §2.3 reads the
    asymmetry: a REFINEMENT is precisely the case where the candidate entails
    the incumbent and the incumbent does not entail the candidate. A single
    "similarity" score cannot express that, which is why there are three numbers
    here and not one.
    """

    entail_fwd: float = Field(ge=0.0, le=1.0)
    entail_rev: float = Field(ge=0.0, le=1.0)
    contradiction: float = Field(ge=0.0, le=1.0)


class AdjudicationBatch(GMModel):
    """The judge's reply: one judgement per incumbent, in order.

    A wrapper rather than a bare list because `GMModel` is what the prompt's
    `output_schema` frontmatter names and what `extra="forbid"` is enforced on -
    a hallucinated field in the reply raises here rather than being dropped on
    the way to a threshold comparison.
    """

    judgements: list[Judgement]


class NLIJudge(Protocol):
    """Scores a candidate against the incumbents it might conflict with.

    The seam S4.3 names: an LLM judge today, a local cross-encoder in week 2 if
    latency demands it. Structural, so a cross-encoder satisfies it without
    importing anything from this package.
    """

    async def compare(
        self, candidate: str, incumbents: Sequence[str], *, trace_id: TraceId
    ) -> list[Judgement]:
        """Judge `candidate` against each of `incumbents`.

        Args:
            candidate: The new claim's supporting text - `Provenance.verbatim`,
                which ADR-0007 makes the *source* text rather than the model's
                paraphrase of it. Comparing paraphrases would measure the
                extractor's wording rather than the claims.
            incumbents: One supporting text per incumbent, in the caller's
                order.
            trace_id: This proposal's trace, for the errors below.

        Returns:
            One `Judgement` per incumbent, in the same order. An implementation
            must not reorder or drop: the caller pairs them back up positionally
            and a shifted list attributes one incumbent's contradiction to
            another.

        Raises:
            InjectionDetected: the reply echoed the canary token.
            ValidationRejected: the reply did not validate, or returned the
                wrong number of judgements.
            ProviderUnavailable: the model is unreachable. Retryable.
            BudgetExceeded: the tenant's cap is reached.
        """
        ...


class LLMJudge:
    """An `NLIJudge` backed by the BALANCED tier and `adjudicate_conflict@v1`.

    Structurally an `NLIJudge`, not a subclass of one, for the reason S1.7 made
    these contracts Protocols.
    """

    def __init__(self, llm: LLMClient) -> None:
        """Bind a client.

        Args:
            llm: The client. Called once per candidate, whatever the incumbent
                count - see the module docstring on why the batch is one call.
        """
        self._llm = llm

    async def compare(
        self, candidate: str, incumbents: Sequence[str], *, trace_id: TraceId
    ) -> list[Judgement]:
        """Judge the whole incumbent set in one BALANCED call.

        See `NLIJudge.compare`. Returns an empty list without calling the model
        when there are no incumbents - a novel fact is the common case, and
        paying for a completion to be told there was nothing to compare is the
        kind of cost that only shows up on a bill.
        """
        if not incumbents:
            return []
        canary = secrets.token_hex(_CANARY_BYTES)
        prompt = render(
            _PROMPT_NAME,
            _PROMPT_VERSION,
            {
                "candidate": candidate,
                "incumbents": _numbered(incumbents),
                "canary": canary,
            },
        )
        reply = await self._llm.complete(
            prompt=prompt.text, schema=AdjudicationBatch, tier=Tier.BALANCED, temperature=0.0
        )
        if any(canary in sample for sample in reply.samples):
            raise InjectionDetected(
                "conflict adjudication echoed the canary token", trace_id=trace_id
            )
        return _parse(reply.samples[0], expected=len(incumbents), trace_id=trace_id)


def _numbered(incumbents: Sequence[str]) -> str:
    """Render the incumbents as a numbered list.

    Numbered rather than bulleted so the model has something to count with: the
    reply has to carry one judgement per entry in the same order, and an
    unnumbered list makes a dropped entry invisible to both sides.
    """
    return "\n".join(f"{index}. {text}" for index, text in enumerate(incumbents, start=1))


def _parse(sample: str, *, expected: int, trace_id: TraceId) -> list[Judgement]:
    """Validate one reply into judgements.

    Args:
        sample: The completion.
        expected: How many incumbents were sent.
        trace_id: For the error.

    Returns:
        The judgements, in reply order.

    Raises:
        ValidationRejected: the reply is not `AdjudicationBatch`, or the count
            does not match. The count check is not pedantry - the caller pairs
            judgements back to incumbents positionally, so a short list silently
            attributes one incumbent's score to a different incumbent, and a
            contradiction lands on the wrong fact.
    """
    try:
        batch = AdjudicationBatch.model_validate_json(sample)
    except ValueError as exc:
        raise ValidationRejected(
            f"conflict adjudication did not return AdjudicationBatch: {exc}", trace_id=trace_id
        ) from exc
    if len(batch.judgements) != expected:
        raise ValidationRejected(
            f"conflict adjudication returned {len(batch.judgements)} judgements for "
            f"{expected} incumbents; they are paired positionally, so a mismatch "
            "would score the wrong fact",
            trace_id=trace_id,
        )
    return batch.judgements
