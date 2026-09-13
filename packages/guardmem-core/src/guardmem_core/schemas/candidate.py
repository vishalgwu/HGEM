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

__all__ = ["ExtractedFact", "ExtractionResult", "MemoryCandidate"]


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


class ExtractedFact(GMModel):
    """One fact exactly as the extraction model returned it.  ADR-0006

    Deliberately much narrower than `MemoryCandidate`: it holds **only what a
    model is allowed to assert**. Everything else a candidate carries - the
    tenant, the namespace, the trace, the provenance, the pinned model id, the
    prompt version - is known by the caller, and a model must never be in a
    position to supply it. A hallucinated `tenant_id` is a tenant-isolation bug;
    a hallucinated `source_hash` is an unsourced write wearing a receipt.

    This is also the unit `MEMORY_ENGINE.md` §3.1 clusters. Entropy is taken
    over the K samples "for a given `(subject, predicate)`", so the pair has to
    survive extraction intact rather than being folded into a rendered string.

    Attributes:
        subject: Entity surface form or canonical ref, as the model read it.
            Still a plain `str` for the same reason `MemoryCandidate.subject`
            is: entity resolution has not run.
        predicate: Must exist in the tenant ontology, or S4.1's schema gate
            sends the candidate to quarantine. Not validated here - the
            extractor has no ontology object, only its rendered form, and
            §2.1 owns that decision.
        object: The claimed value.
        verbatim: The exact substring of the source that supports the claim,
            capped at 2000 characters as `Provenance.verbatim` is. This is what
            §1.3's span linker locates in the source, and a fact whose verbatim
            cannot be located is rejected as `UNSOURCED` rather than stored.
    """

    subject: str
    predicate: str
    object: ObjectValue
    verbatim: str = Field(max_length=2000)


class ExtractionResult(GMModel):
    """What Layer 1 produced from one proposal, and what it cost.

    Two fields here are amendments to `MEMORY_ENGINE.md` §0, recorded in
    ADR-0006: `samples`, without which Layer 3 has nothing to cluster, and
    `dropped_unsourced`, without which the rule §1.3 calls the one that "kills
    most confabulated facts" has an invisible activation count.

    Attributes:
        candidates: The surviving candidates, from the canonical sample. A
            `list`, as the spec of record declares - note that `frozen=True`
            does not make it immutable, see `schemas/base.py`.
        samples: What every sample extracted, in **draw order**, sample 0 first
            and canonical. Ungrouped on purpose: §3.1 clusters by
            `(subject, predicate)` and S5.1 owns that grouping, so freezing one
            reading of it into Layer 1 would settle a Layer 3 question early.
            Note this is not redundant with `candidates` - it includes facts
            that were dropped for want of a span, which is what makes the
            funnel's drop sample possible.
        k_samples: How many samples were drawn (`MEMORY_ENGINE.md` §1.2: 1, 3 or
            5 by risk hint). Equals `len(samples)`, and is kept as its own field
            because the spec of record declares it. Semantic entropy is
            undefined below 1 and `H_norm := 0` at exactly 1.
        dropped_noise: How many turns the noise filter ate. §1.1 is emphatic
            that everything dropped is counted and sampled into the dashboard
            funnel - "you must be able to see what the filter is eating" - which
            is only possible if the count survives here. The filter itself is a
            separate call, so this arrives as an argument to `extract`.
        dropped_unsourced: How many extracted facts were rejected because their
            verbatim could not be located in the source (§1.3).
        tokens_in: Prompt tokens billed, across every call the extraction made.
        tokens_out: Completion tokens billed, likewise.
        cache_hit: Whether the prompt cache served the extraction. True only if
            it served *every* call - a partial hit is not a hit, and reporting
            one would overstate the ≥40% cache-hit assumption in `PRD.md` §6.5.
    """

    candidates: list[MemoryCandidate]
    samples: list[list[ExtractedFact]]
    k_samples: int = Field(ge=1)
    dropped_noise: int = Field(ge=0)
    dropped_unsourced: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cache_hit: bool

    @model_validator(mode="after")
    def _k_samples_must_match_the_samples_drawn(self) -> ExtractionResult:
        """Reject a result whose declared sample count is not what it carries.

        `k_samples` is the denominator in §3.1's ``H_norm = H / log K``. If it
        disagrees with `len(samples)`, entropy is normalised against a sample
        set that was never drawn - and the error is invisible, because the
        result is a plausible number in the right range.

        Returns:
            The result unchanged, once the two agree.

        Raises:
            ValueError: if `k_samples != len(samples)`.
        """
        if self.k_samples != len(self.samples):
            raise ValueError(
                f"k_samples is {self.k_samples} but {len(self.samples)} sample "
                "sets were carried; MEMORY_ENGINE.md 3.1 normalises entropy by "
                "log K and the two may not disagree"
            )
        return self
