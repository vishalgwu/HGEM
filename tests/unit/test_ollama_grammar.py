"""Ollama's grammar compiler, and the schema keywords it cannot take.

Split from `test_llm_providers.py` at `RULES.md` §2.4's cap, and the seam is
real: that module is about three adapters agreeing on one `LLMResponse`, and
this one is about a single vendor's *generation* constraint - a concern the
schema layer has no idea exists and the other two providers do not share.

Ollama turns `format` into a GBNF grammar, and a bounded length or item count
becomes a repetition in it. Repetitions of 2000 or more are refused outright,
with `400 Failed to initialize samplers: failed to parse grammar` - the whole
schema, not the one field. Measured against Ollama 0.34.0 with `llama3.1:8b`
by bisection: **1999 compiles and 2000 does not**, for `maxLength`, `minLength`
and `maxItems` alike.

`ExtractedFact.verbatim` declares `max_length=2000`, mirroring
`Provenance.verbatim`. So the extraction schema sat exactly one over the line,
**no extraction ran on Ollama at all**, and CHECKPOINT B was recorded as
runnable locally twice - once in the README and once in the notebook's own
sign-off block - when it was not.

A mock cannot reproduce the 400. What it pins is the *payload*, which is what
this adapter controls; `tests/live/test_live_providers.py` asserts the real
schema against the real compiler.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError

from fixtures.providers import ollama_adapter, ollama_transport
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers.grammar import grammar_safe


class TestTheGrammarLimitOllamaHasAndTheSchemaLayerDoesNot:
    """Ollama compiles `format` into a GBNF grammar, and repetitions of 2000 or
    more are refused - the whole schema, not the one field.

    **This is not a hypothetical the adapter guards against.**
    `ExtractedFact.verbatim` declares `max_length=2000`, mirroring
    `Provenance.verbatim`, which put the extraction schema exactly one over the
    line. Measured against Ollama 0.34.0 with `llama3.1:8b`: 1999 compiles,
    2000 does not. So **no extraction ran on Ollama at all**, and CHECKPOINT B
    was recorded twice as runnable locally when it was not - once in the README
    and once in the notebook's sign-off block.

    A mock cannot reproduce the 400; what it can pin is the *payload*, which is
    the thing under this adapter's control.
    """

    async def test_a_repetition_at_the_limit_is_stripped_from_the_payload(self) -> None:
        class Capped(BaseModel):
            verbatim: str = Field(max_length=2000)

        seen: list[dict[str, Any]] = []
        client, http = ollama_adapter(ollama_transport(seen=seen))
        try:
            await client.complete(prompt="p", schema=Capped, tier=Tier.FAST)
        finally:
            await http.aclose()

        assert "maxLength" not in json.dumps(seen[0]["format"])

    async def test_a_repetition_below_the_limit_is_left_alone(self) -> None:
        """Stripping everything would be the lazy fix and a worse one: a cap the
        compiler can express is a cap worth generating under."""

        class Short(BaseModel):
            code: str = Field(max_length=8)

        seen: list[dict[str, Any]] = []
        client, http = ollama_adapter(ollama_transport(seen=seen))
        try:
            await client.complete(prompt="p", schema=Short, tier=Tier.FAST)
        finally:
            await http.aclose()

        assert seen[0]["format"]["properties"]["code"]["maxLength"] == 8

    async def test_it_reaches_inside_defs_and_refs(self) -> None:
        """`ExtractedFact.verbatim` is two levels down a `$ref`. A top-level
        pass would have found nothing and looked like it worked."""

        class Fact(BaseModel):
            verbatim: str = Field(max_length=2000)

        class Batch(BaseModel):
            facts: list[Fact]

        seen: list[dict[str, Any]] = []
        client, http = ollama_adapter(ollama_transport(seen=seen))
        try:
            await client.complete(prompt="p", schema=Batch, tier=Tier.FAST)
        finally:
            await http.aclose()

        assert "maxLength" not in json.dumps(seen[0]["format"])

    async def test_the_caller_still_validates_against_the_full_schema(self) -> None:
        """The constraint is not lost, it moves from prevention to detection.
        `RULES.md` §3 leaves parsing with the caller, so a reply that overruns
        the cap the grammar could not express is still rejected."""

        class Capped(BaseModel):
            verbatim: str = Field(max_length=2000)

        with pytest.raises(ValidationError):
            Capped.model_validate_json(json.dumps({"verbatim": "x" * 2001}))

    def test_the_model_s_own_schema_is_not_mutated(self) -> None:
        """pydantic caches `model_json_schema()` and hands back the same object,
        so editing it in place would strip the cap from the validation this
        exists to preserve - in every other caller in the process."""

        class Capped(BaseModel):
            verbatim: str = Field(max_length=2000)

        before = json.dumps(Capped.model_json_schema())
        grammar_safe(Capped.model_json_schema())

        assert json.dumps(Capped.model_json_schema()) == before

    def test_a_numeric_bound_is_not_a_repetition(self) -> None:
        """`maximum` bounds a value, not a count, and was measured to compile at
        2000. Stripping it would loosen a constraint for no reason."""

        class Bounded(BaseModel):
            n: int = Field(le=2000)

        assert grammar_safe(Bounded.model_json_schema())["properties"]["n"]["maximum"] == 2000
