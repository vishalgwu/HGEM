"""`LLMEntailer` against a real model.  BUILD_NOTEBOOK.md S5.1

    uv run pytest -m live

Deselected by default, for `test_live_providers.py`'s reasons - `RULES.md` §5
wants no skips on main and live calls only in the nightly job, and a deselected
test satisfies both.

**Why this file exists at all.** `tests/unit/test_entailment.py` is thorough
about the adapter's *contract* - batching, positional pairing, the refusal to
invent a score - and every one of those assertions would pass against a model
that returned 0.7 for everything. The number is the product here: §3.2 routes
0.60 of `C` through it, and a scorer that returns a constant is CHECKPOINT B's
own headline failure, "plausible numbers with no discriminative power ...
invisible to unit tests".

So what is asserted below is not correctness on any single pair. It is that the
function *discriminates*: that the same model, in the same call, separates a
pair that entails from one that does not. That is the weakest claim worth making
and the strongest a 7B model can support.

Measured against `llama3.1:8b` while S5.1's correction 4 was written: 0.95 for
the forward direction of a wider/narrower pair and 0.00 for the reverse, 1.00
for a paraphrase, 0.00 for a contradiction and for two unrelated texts, and 0.80
for a real grounding pair out of the seed transcript.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

import httpx
import pytest

from guardmem_core.llm.base import Tier
from guardmem_core.llm.entailment import EntailmentPair, LLMEntailer
from guardmem_core.llm.providers import OllamaClient
from guardmem_core.types import TraceId

if TYPE_CHECKING:
    from guardmem_core.pipeline.l3_score import EntailFn

pytestmark = pytest.mark.live

# `test_live_providers.py`'s constants, and its reasoning: a cold 7B model on CPU
# is slow, and the model a developer has pulled is a property of their machine.
_LOCAL_TIMEOUT_S: Final = 300.0
_OLLAMA_URL: Final = os.environ.get("GM_OLLAMA_URL", "http://localhost:11434")
_OLLAMA_MODEL: Final = os.environ.get("GM_OLLAMA_MODEL", "llama3.1:8b")

TRACE = TraceId("tr_live_s51")

# Both directions of a wider/narrower pair, a paraphrase, a contradiction, two
# unrelated texts, and the grounding trap the prompt names by name. Sent as one
# batch because that is how the adapter is used and because the comparison the
# tests make is only fair within a single call.
_WIDER = "I'm allergic to penicillin and amoxicillin"
_NARROWER = "allergic to penicillin"

_PAIRS: Final = (
    EntailmentPair(_WIDER, _NARROWER),
    EntailmentPair(_NARROWER, _WIDER),
    EntailmentPair("lives in Austin", "resides in Austin, TX"),
    EntailmentPair(_NARROWER, "not allergic to penicillin"),
    EntailmentPair("prefers email", "speaks French"),
    EntailmentPair("we should talk about your allergies", _NARROWER),
)

# §3.1's `same_meaning` cut. Named here rather than imported because `entropy.py`
# keeps it private on purpose - it is part of the definition of the measurement -
# and a test that reached in would couple this file to that decision.
_SAME_MEANING: Final = 0.8


@pytest.fixture(scope="module")
async def entail() -> EntailFn:
    """One real batched call, shared by every assertion in this module.

    Module-scoped because a 7B model answering six pairs is slow and the
    comparisons below are between entries of *one* batch. Re-drawing per test
    would cost minutes and would also make the directionality assertion compare
    two different completions, which is a weaker claim than the one intended.
    """
    async with httpx.AsyncClient(base_url=_OLLAMA_URL) as http:
        client = OllamaClient(
            http,
            models=dict.fromkeys(Tier, _OLLAMA_MODEL),
            timeout_s=_LOCAL_TIMEOUT_S,
        )
        return await LLMEntailer(client).lookup(_PAIRS, trace_id=TRACE)


class TestItDiscriminates:
    """The claim a mock cannot make."""

    async def test_entailment_beats_non_entailment(self, entail: EntailFn) -> None:
        """The weakest useful property, and the one whose failure is silent.

        If this does not hold, `S_src` is noise and 0.25 of `C` is a constant -
        which CHECKPOINT B's second diagnostic asks about directly ("AUROC of
        `S_src` alone. If it is near 0.5, ...").
        """
        assert entail(_WIDER, _NARROWER) > entail("prefers email", "speaks French")

    async def test_the_reverse_direction_scores_lower(self, entail: EntailFn) -> None:
        """Bidirectional clustering is only meaningful if the model can tell the
        directions apart. A model answering symmetrically would merge every
        narrow claim into a vague one, and §3.1's whole mechanism - "requiring
        both directions means the two claims have to be interchangeable" - would
        be a more expensive one-way test.
        """
        assert entail(_WIDER, _NARROWER) > entail(_NARROWER, _WIDER)

    async def test_a_paraphrase_clusters_and_a_contradiction_does_not(
        self, entail: EntailFn
    ) -> None:
        """§3.1's own example, at §3.1's own cut point. "Lexical variance is not
        uncertainty - 'lives in Austin' and 'resides in Austin, TX' are one
        meaning."
        """
        assert entail("lives in Austin", "resides in Austin, TX") >= _SAME_MEANING
        assert entail(_NARROWER, "not allergic to penicillin") < _SAME_MEANING

    async def test_a_span_merely_about_the_topic_does_not_ground_a_claim(
        self, entail: EntailFn
    ) -> None:
        """The failure mode `S_src` exists to catch, and the one the prompt
        calls out by name. An extractor that proposes "allergic to penicillin"
        from a turn that only raised the subject has confabulated, and grounding
        is what is supposed to notice.
        """
        assert entail("we should talk about your allergies", _NARROWER) < _SAME_MEANING


class TestTheContractHoldsAgainstARealModel:
    async def test_every_pair_sent_comes_back_bounded(self, entail: EntailFn) -> None:
        """`EntailmentBatch` bounds the field, so this cannot fail without the
        validation having failed first - which is the point. It is the assertion
        that the count check and the bound were exercised by a real reply rather
        than by a scripted one.
        """
        assert all(0.0 <= entail(*pair) <= 1.0 for pair in _PAIRS)

    async def test_an_unasked_pair_still_raises(self, entail: EntailFn) -> None:
        """The refusal to invent, against a real batch."""
        with pytest.raises(KeyError, match="was not scored"):
            entail(_WIDER, "something nobody asked about")
