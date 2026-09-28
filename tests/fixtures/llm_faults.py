"""LLM clients that misbehave on purpose: one leaks its canary, one is down.

`graph_faults.py` is the same idea for the graph store. `FakeLLM` scripts its
replies ahead of time, which cannot express "echo back the canary you were just
given" - the canary is minted per call inside the code under test and differs
every time, which is the property under test. These read it back out of the
prompt instead. Four test modules each carried their own copy of that, and two
carried their own copy of a provider that is down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fixtures.extraction import response
from fixtures.fakes import RecordedCall
from guardmem_core.errors import ProviderUnavailable
from guardmem_core.types import TraceId

if TYPE_CHECKING:
    from pydantic import BaseModel

    from guardmem_core.llm.base import LLMClient, LLMResponse, Tier

__all__ = ["EchoingLLM", "UnreachableLLM", "canary_of"]

_TRACE: Final = TraceId("tr_unreachable")

# Every template carries its token as `canary="<token>"`, so this recovers one
# minted per call without knowing its length.
_CANARY_MARKER = 'canary="'


def canary_of(prompt: str) -> str:
    """Recover the canary token `render` put into `prompt`."""
    return prompt.split(_CANARY_MARKER, 1)[1].split('"', 1)[0]


@dataclass(slots=True)
class EchoingLLM:
    """An `LLMClient` whose every sample contains its prompt's own canary.

    `fields` is the rest of the reply, so it can be well-formed for the schema
    under test - an echo must be refused even from a reply that would parse. It
    reads back whatever the prompt carried, so it still holds if the token
    changes length or the template moves it.
    """

    fields: dict[str, object] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        """Return `n` samples, each echoing this prompt's canary."""
        self.calls.append(
            RecordedCall(prompt=prompt, tier=tier, temperature=temperature, n=n, schema=schema)
        )
        return response(*[json.dumps({**self.fields, "note": canary_of(prompt)})] * n)


@dataclass(slots=True)
class UnreachableLLM:
    """An `LLMClient` whose provider is down: every call raises `ProviderUnavailable`."""

    trace_id: TraceId = _TRACE
    calls: list[str] = field(default_factory=list)

    async def complete(
        self,
        *,
        prompt: str,
        schema: type[BaseModel] | None = None,
        tier: Tier,
        temperature: float = 0.0,
        n: int = 1,
    ) -> LLMResponse:
        """Record the prompt, then fail as an open circuit does."""
        self.calls.append(prompt)
        raise ProviderUnavailable("circuit open", trace_id=self.trace_id)


# Structural conformance, checked by the tool that can check it (S1.7).
_echoing: LLMClient = EchoingLLM()
_unreachable: LLMClient = UnreachableLLM()
