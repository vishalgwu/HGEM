"""Provenance, write receipts and the audit chain.  BUILD_NOTEBOOK.md S1.6

`ARCHITECTURE.md` §0: "The audit log is the product. Every other component is
instrumented to feed it. If a decision isn't reconstructable, it's a bug of the
same severity as a wrong decision." This module is the shape of that record.

`Provenance` lives here rather than with `MemoryCandidate`, per
`PROJECT_TREE.md`, and it is imported by `candidate.py` and `entity.py` both.
That direction is deliberate: provenance is the thing a receipt is *for*, and
`RULES.md` §1.1 makes a write without it a P0 bug, so the module that owns
receipts owns the proof they rest on.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.verdict import Decision
from guardmem_core.types import AssertionId, CandidateId, TenantId, TraceId

__all__ = ["AuditEvent", "Provenance", "SourceTier", "WriteReceipt"]


class SourceTier(StrEnum):
    """How far the content this claim rests on may be trusted.

    `RULES.md` §4 orders them `TRUSTED_SYSTEM > VERIFIED_USER > UNVERIFIED_USER
    > TOOL_OUTPUT > RETRIEVED_WEB`, and the tier caps the maximum auto-writable
    impact level: retrieved web content can never auto-write a HIGH-impact
    predicate regardless of confidence. It also supplies the trust multiplier in
    `S_src` (`MEMORY_ENGINE.md` §3.2).
    """

    TRUSTED_SYSTEM = "trusted_system"  # verified EHR record, signed API payload
    VERIFIED_USER = "verified_user"  # authenticated human in-session
    UNVERIFIED_USER = "unverified_user"
    TOOL_OUTPUT = "tool_output"
    RETRIEVED_WEB = "retrieved_web"  # lowest trust; never auto-writes HIGH impact

    def at_least(self, floor: SourceTier) -> bool:
        """Is this tier at least as trustworthy as `floor`?

        Args:
            floor: The weakest tier the caller will accept - typically a
                predicate's `min_source_tier` from the tenant ontology.

        Returns:
            True when this tier is `floor` or stronger.

        The ordering is `RULES.md` §4's, quoted in the class docstring above,
        and it lives here because that is where the enum lives. It was a tuple
        in a seed script first, which is one file away from the definition it
        describes - and a safety ordering with two homes is one that can
        disagree with itself. The S3.5 schema gate and the S3.6 seed both ask
        this question; a `StrEnum` compares alphabetically and would answer it
        wrong without ever raising.
        """
        order = list(SourceTier)
        return order.index(self) <= order.index(floor)


class Provenance(GMModel):
    """Where a claim came from, exactly.

    Attributes:
        source_hash: sha256 of the source document or turn.
        source_span: Half-open character offsets `[start, end)` into that
            document. Postgres stores it as `INT4RANGE`
            (`ARCHITECTURE.md` §5), which is where the half-open convention
            comes from.
        source_tier: Trust tier of the source.
        verbatim: The exact text the claim rests on, capped at 2000 characters
            by the spec. This is what the reviewer reads and what NLI compares
            against, so it is the text itself and never a paraphrase - and from
            S2.3 that is enforced by construction: `span_linker` returns the
            *source* text at the span it found, not the model's claim, so
            `source[source_span] == verbatim` always (ADR-0007).
        alignment: How closely the model quoted, normalised to [0, 1]. Exactly
            1.0 on an exact match, which is what every provenance meant before
            this field existed, hence the default. Below 1.0 says the model
            paraphrased its own citation - a grounding signal in its own right,
            and the input `MEMORY_ENGINE.md` §3.2's fuzzy-match penalty needs.
            It cannot be recovered later: `verbatim` is the source text, so
            comparing the two would return 1.0 by construction.
        captured_at: When the source was captured.
    """

    source_hash: str
    source_span: tuple[int, int]
    source_tier: SourceTier
    verbatim: str = Field(max_length=2000)
    alignment: float = Field(default=1.0, ge=0.0, le=1.0)
    captured_at: datetime

    @model_validator(mode="after")
    def _span_must_be_a_real_interval(self) -> Provenance:
        """Reject a span that cannot point at anything.

        A negative offset or an inverted interval is a bug in the span linker,
        and without this it travels: pydantic accepts it, `INT4RANGE` rejects it
        at S3.1, and the failure surfaces at write time on the far side of
        scoring rather than at the boundary that produced it. The reviewer UI
        would also slice the source with it and silently highlight nothing.

        Returns:
            The provenance unchanged, once the span is a real interval.

        Raises:
            ValueError: if the span is negative or not strictly increasing.
        """
        start, end = self.source_span
        if start < 0:
            raise ValueError(f"source_span offsets must be non-negative, got {start}")
        if start >= end:
            raise ValueError(
                f"source_span must be half-open and non-empty, got [{start}, {end}). "
                "A zero-width span quotes nothing, which RULES.md 1.1 treats the "
                "same as no span at all."
            )
        return self


class WriteReceipt(GMModel):
    """Proof that one candidate became one durable assertion.

    Attributes:
        assertion_id: The assertion that now exists.
        candidate_id: The candidate it came from. Distinct types on purpose -
            the transition between them *is* the moment governance happened.
        decision: The decision that authorised the write.
        confidence: `C` at the time of the write, denormalised from the
            `ConfidenceReport` so a receipt is readable on its own.
        risk: `R` at the time of the write, for the same reason.
        trace_id: The proposal this belongs to.
        written_at: System time of the write.
        superseded: The assertion this one retired, where it retired one.
            `MEMORY_ENGINE.md` §2.3: "Supersession is never silent."
    """

    assertion_id: AssertionId
    candidate_id: CandidateId
    decision: Decision
    confidence: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)
    trace_id: TraceId
    written_at: datetime
    superseded: AssertionId | None = None


class AuditEvent(GMModel):
    """One link in the append-only hash chain.

    `RULES.md` invariant I5: ``digest_n == sha256(payload_n ‖ digest_{n-1})``.
    S5.5 computes it over canonical JSON and writes it **in the same transaction
    as the state change** - `RULES.md` non-negotiable #4, "not after, not
    best-effort".

    Attributes:
        seq: Position in the chain. `None` before insert; Postgres assigns it
            from a `BIGSERIAL` (`ARCHITECTURE.md` §5), so the model cannot know
            it at construction time.
        tenant_id: The chain this event belongs to. Chains are per tenant, which
            is what makes `verify_chain(tenant_id)` a meaningful question.
        trace_id: The proposal that produced the event.
        kind: What happened.
        payload: The event body. Must be JSON-safe end to end - the digest is
            taken over its canonical JSON, so a value that does not survive a
            round trip (a `datetime` nested inside, say) would break
            verification for every later link in the chain. Not enforced here;
            see `schemas/base.py` and the test that pins it.
        prev_digest: The previous link's digest.
        digest: This link's digest.
        created_at: System time of the event.

    The digest fields are plain `str` rather than a fixed-width hex constraint.
    S5.5 owns the chain's format, including whatever the genesis link uses for
    `prev_digest`, and a length rule written here before that step would be a
    guess that `verify_chain` then has to work around.
    """

    seq: int | None = Field(default=None, ge=1)
    tenant_id: TenantId
    trace_id: TraceId
    kind: Literal[
        "DECISION",
        "WRITE",
        "REVIEW",
        "POLICY_CHANGE",
        "QUARANTINE",
        "SUPERSEDE",
    ]
    payload: dict[str, object]
    prev_digest: str
    digest: str
    created_at: datetime
