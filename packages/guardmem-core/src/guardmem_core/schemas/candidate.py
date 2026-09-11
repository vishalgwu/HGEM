"""Proposed facts and the result of extracting them.  BUILD_NOTEBOOK.md S1.6

Copied from `MEMORY_ENGINE.md` §0, with the `NewType` ids from `types.py` in
place of the spec's bare `str`. The spec writes `candidate_id: str`; `RULES.md`
§2.1 requires that "passing a raw `str` where an `AssertionId` is expected must
be a type error", and S1.6's own PROMPT says to "use the NewType ids from
types.py". The two instructions conflict only in spelling - `NewType` erases to
`str` at runtime, so the wire format and the database column are identical
either way. The stricter reading wins.

`MemoryProposal`, the API-level input that produces these, is **not** here. Its
shape is published in `MCP_INTEGRATION.md` §2.2 as a tool schema rather than as
a domain model, and the gateway is what turns one into candidates (S8.1); it
arrives with the surface that accepts it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from guardmem_core.schemas.base import GMModel, ObjectValue
from guardmem_core.schemas.receipt import Provenance
from guardmem_core.types import CandidateId, Namespace, TenantId, TraceId

__all__ = ["ExtractionResult", "MemoryCandidate"]


class MemoryCandidate(GMModel):
    """One proposed fact, not yet governed and not yet durable.

    Attributes:
        candidate_id: This proposal's id. Becomes an `AssertionId` only if it is
            written, and the two types stay distinct because that transition is
            the moment governance happened.
        tenant_id: Owning tenant. Present on every row, and the value Postgres
            RLS is set from.
        namespace: Isolation scope, e.g. `"patient:8812"`, `"org:acme"`, or the
            `"quarantine:<tenant>"` namespace failed content lands in.
        subject: Entity canonical ref or surface form. Still a plain `str` at
            this layer: Layer 1 extracts a surface form and entity resolution
            has not run yet, so typing it as `EntityId` here would be a promise
            the extractor cannot keep. `StoredAssertion.subject_id` is where it
            becomes one.
        predicate: Must exist in the tenant ontology, or the candidate goes to
            quarantine (`MEMORY_ENGINE.md` §2.1).
        object: The claimed value. See `ObjectValue` for why a list is not one.
        valid_from: World-time the fact became true. Distinct from when it was
            recorded - that is the second axis, and it lives on the stored
            assertion, not here.
        valid_to: World-time it stopped being true, if known.
        provenance: The span this claim rests on. Required, no default: a
            candidate that cannot say where it came from is rejected before it
            is scored (`MEMORY_ENGINE.md` §1.3), so there is no valid state of
            this object without it.
        extracted_by: Pinned model id, e.g. `"claude-haiku-4-5"`. `RULES.md` §3
            forbids floating aliases here - replay is dishonest without it.
        prompt_version: Which versioned prompt produced it. A prompt change is a
            semver-minor change (`RULES.md` §3), and this is what ties a
            candidate to the one that made it.
        trace_id: The proposal this belongs to.
    """

    candidate_id: CandidateId
    tenant_id: TenantId
    namespace: Namespace
    subject: str
    predicate: str
    object: ObjectValue
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provenance: Provenance
    extracted_by: str
    prompt_version: str
    trace_id: TraceId

    @model_validator(mode="after")
    def _validity_interval_must_not_be_inverted(self) -> MemoryCandidate:
        """Reject a fact that stops being true before it starts.

        `MEMORY_ENGINE.md` §2.2(c) detects conflicts by intersecting
        `[valid_from, valid_to)` intervals. An inverted interval intersects
        nothing, so a temporal-overlap conflict silently fails to fire and two
        contradictory facts coexist - which is the `contradiction_escape_rate`
        metric going quietly wrong rather than loudly.

        Returns:
            The candidate unchanged, once the interval is coherent.

        Raises:
            ValueError: if both bounds are set and `valid_to` precedes
                `valid_from`.
        """
        # Bound to locals so mypy narrows both Optionals in one condition. The
        # equivalent nested `if` reads no better and trips ruff's SIM102.
        valid_from, valid_to = self.valid_from, self.valid_to
        if valid_from is not None and valid_to is not None and valid_to < valid_from:
            raise ValueError(
                f"valid_to {valid_to.isoformat()} precedes valid_from "
                f"{valid_from.isoformat()}; the validity interval is half-open "
                "[valid_from, valid_to) and cannot run backwards"
            )
        return self


class ExtractionResult(GMModel):
    """What Layer 1 produced from one proposal, and what it cost.

    Attributes:
        candidates: The surviving candidates. A `list`, as the spec of record
            declares - note that `frozen=True` does not make it immutable, see
            `schemas/base.py`.
        k_samples: How many samples were drawn (`MEMORY_ENGINE.md` §1.2: 1, 3 or
            5 by risk hint). Semantic entropy is undefined below 1 and
            `H_norm := 0` at exactly 1.
        dropped_noise: How many turns the noise filter ate. `MEMORY_ENGINE.md`
            §1.1 is emphatic that everything dropped is counted and sampled into
            the dashboard funnel - "you must be able to see what the filter is
            eating" - which is only possible if the count survives here.
        tokens_in: Prompt tokens billed.
        tokens_out: Completion tokens billed.
        cache_hit: Whether the prompt cache served this extraction.
    """

    candidates: list[MemoryCandidate]
    k_samples: int = Field(ge=1)
    dropped_noise: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cache_hit: bool
