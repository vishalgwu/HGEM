"""Shared scaffolding for the extraction tests.  S2.2

Split out when `tests/unit/test_extractor.py` reached the 400-line module cap
`RULES.md` §2.4 sets. The seam is the same one `fixtures/` exists for: what
lives here is the *setup* two test modules share - a source document, a
scripted reply, the caller-owned context - and the test modules keep the
assertions.

The source document is a short clinical intake exchange, and the three fact
constants are written against it deliberately: two that it supports and one it
does not. `INVENTED` is the confabulation case `MEMORY_ENGINE.md` §1.3 exists to
kill, and having it here means every extraction test can reach for it rather
than inventing its own near-miss.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final

from fixtures.fakes import FakeLLM
from guardmem_core.llm.base import LLMClient, LLMResponse, Tier
from guardmem_core.pipeline.l1_extract.extractor import ExtractionContext, extract
from guardmem_core.schemas import ExtractionResult, SourceTier
from guardmem_core.types import Namespace, TenantId, TraceId

__all__ = [
    "ALLERGY",
    "CONTENT",
    "CONTEXT",
    "INVENTED",
    "ONTOLOGY",
    "PHARMACY",
    "TRACE",
    "batch",
    "response",
    "run_extract",
    "two_call_llm",
]

CONTENT: Final = (
    "Patient: I'm allergic to penicillin - it gives me hives. "
    "I use the CVS on Elm Street now. Dr. Alvarez is my PCP."
)
ONTOLOGY: Final = "predicates:\n  allergy: {cardinality: many, impact: critical}\n"
TRACE: Final = TraceId("tr_9f2a3c")

CONTEXT: Final = ExtractionContext(
    tenant_id=TenantId("t_acme"),
    namespace=Namespace("patient:8812"),
    trace_id=TRACE,
    source_tier=SourceTier.VERIFIED_USER,
    captured_at=datetime(2026, 3, 12, 14, 31, 2, tzinfo=UTC),
)

ALLERGY: Final[Mapping[str, object]] = {
    "subject": "patient:8812",
    "predicate": "allergy",
    "object": "penicillin",
    "verbatim": "allergic to penicillin",
}
PHARMACY: Final[Mapping[str, object]] = {
    "subject": "patient:8812",
    "predicate": "preferred_pharmacy",
    "object": "CVS Elm Street",
    "verbatim": "the CVS on Elm Street",
}
# A fact the source does not contain. §1.3's whole purpose: a model that invents
# a fact must also invent the sentence it came from, and that sentence is not
# there to be found.
INVENTED: Final[Mapping[str, object]] = {
    "subject": "patient:8812",
    "predicate": "allergy",
    "object": "sulfa",
    "verbatim": "allergic to sulfa drugs",
}


def batch(*facts: Mapping[str, object]) -> str:
    """One sample's reply, as `ExtractionBatch` JSON."""
    return json.dumps({"facts": list(facts)})


def response(*samples: str, **overrides: object) -> LLMResponse:
    """An `LLMResponse` carrying `samples`, with plausible billing defaults."""
    fields: dict[str, object] = {
        "samples": list(samples),
        "model": "claude-haiku-4-5",
        "temperature": 0.0,
        "seed": None,
        "tokens_in": 100,
        "tokens_out": 30,
        "cache_hit": False,
        "latency_ms": 12.0,
        "cost_usd": 0.0001,
    }
    return LLMResponse.model_validate(fields | overrides)


def two_call_llm(*, canonical: str | None = None, spread: list[str] | None = None) -> FakeLLM:
    """A `FakeLLM` scripted for the canonical draw and then the spread draw.

    `FakeLLM` hands back `responses[i]` for call `i`, which is exactly the
    canonical-then-spread order `extract` makes them in.
    """
    return FakeLLM(
        responses=[
            response(canonical if canonical is not None else batch(ALLERGY, PHARMACY)),
            response(*(spread if spread is not None else [batch(ALLERGY), batch(PHARMACY)])),
        ]
    )


async def run_extract(
    llm: LLMClient, *, k: int = 3, tier: Tier = Tier.FAST, **overrides: object
) -> ExtractionResult:
    """Call `extract` with this module's defaults."""
    kwargs: dict[str, object] = {
        "context": CONTEXT,
        "ontology_yaml": ONTOLOGY,
        "k": k,
        "tier": tier,
    }
    return await extract(CONTENT, llm, **(kwargs | overrides))  # type: ignore[arg-type]
