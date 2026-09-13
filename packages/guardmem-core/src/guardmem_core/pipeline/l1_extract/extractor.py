"""K-sample structured extraction.  S2.2

`MEMORY_ENGINE.md` §1.2 and §1.3. Turns denoised text into `MemoryCandidate`s,
each anchored to a verbatim span of the source, and keeps the K samples that
Layer 3 measures uncertainty over.

**Sample 0 is canonical and is drawn at temperature 0.** §1.2 is explicit:
"Samples are drawn at temperature 0.7 [...] sample 0 is drawn at temperature 0
and is the *canonical* candidate text. The other K-1 exist only to estimate
uncertainty." That cannot be expressed as one call - `LLMClient.complete` takes
a single `temperature` for all `n` samples - so `K > 1` makes two: one canonical
draw at 0.0, then `K-1` at 0.7. The step's snippet
(`temperature=0.0 if k == 1 else 0.7`) draws *every* sample at 0.7, which leaves
no canonical text at all and makes §3.1's minority-cluster drop - "a candidate
that appears in zero clusters containing sample 0's meaning" - arbitrary.

**A short sample count is refused, not absorbed.** If the provider returns fewer
samples than were asked for, this raises. Using what came back looks tolerant
and is the dangerous option: K collapses toward 1, §3.1 sets `H_norm := 0` at
K = 1, and zero entropy is *maximum* confidence on that term. A degraded
provider would then widen the auto-write path, which `ARCHITECTURE.md` §0
forbids in as many words - "degradation never widens the auto-write path".

**The model is only allowed to assert four things.** `ExtractedFact` carries
subject, predicate, object and verbatim. Everything else a candidate needs -
tenant, namespace, trace, source tier, capture time - is `ExtractionContext`,
supplied by the caller; the provenance hash, the pinned model id and the prompt
version are derived here. A hallucinated `tenant_id` would be a tenant-isolation
bug and a hallucinated `source_hash` an unsourced write wearing a receipt, so
neither is reachable by construction rather than by validation. That is what
`ExtractionContext` is *for*: it is not a parameter bag, it is the set of fields
a model may not supply.

**A known gap, recorded rather than half-fixed.** `content` is interpolated
between `<untrusted_content>` delimiters, and nothing here stops the content
itself from containing the closing delimiter and continuing with text the model
may read as instructions. The canary catches an *echo*, not an escape. The fix
is not to sanitise `content`: every `source_span` indexes into exactly this
string and `source_hash` is its digest, so altering it would silently
invalidate the provenance of every candidate. The real answer is the pre-flight
injection detector at **S11.1**, which `ARCHITECTURE.md` §2 places before any
model sees the text - and which quarantines rather than rewrites.

No clock is read and no randomness is drawn except the canary. `captured_at`
lives on the context for that reason: `scripts/replay_trace.py` (S5.6) re-runs
this path against a recorded audit record, and a `datetime.now()` inside it
would make replay disagree with itself.

Two more things the caller owns. **`content`** is the source document *and its
offsets*: a caller that joins denoised turns must pass the joined result and
nothing else, because after S2.1 the spans are into the *denoised* document
rather than the transcript, and the recorded hash says so. **`ontology_yaml`**
is the ontology already rendered, not an `Ontology` object - S3.5 owns that
shape, and predicates are not validated here either, since an unknown predicate
is S4.1's schema gate sending the candidate to quarantine (§2.1).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from pydantic import ValidationError

from guardmem_core.errors import InjectionDetected, ProviderUnavailable, ValidationRejected
from guardmem_core.llm.base import LLMClient, LLMResponse, Tier
from guardmem_core.pipeline.l1_extract.span_linker import link_span
from guardmem_core.prompts.loader import render
from guardmem_core.schemas.base import GMModel
from guardmem_core.schemas.candidate import ExtractedFact, ExtractionResult, MemoryCandidate
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import CandidateId, Namespace, TenantId, TraceId

__all__ = ["ExtractionBatch", "ExtractionContext", "extract"]

_PROMPT_NAME: Final = "extract_memories"
_PROMPT_VERSION: Final = 1

_CANARY_BYTES: Final = 8

# §1.2's ladder is 1 on LOW, 3 by default, 5 on HIGH. Not enforced here - which
# arm applies is the caller's routing decision (S9.x), and `settings.default_k`
# is only the middle one - but K below 1 is not a sample count at all.
_MIN_K: Final = 1


class ExtractionContext(GMModel):
    """The fields a model may not supply, supplied by the caller.

    Grouped rather than passed loose because they share the property that makes
    them a set: each is something the extraction model must never be in a
    position to assert. A hallucinated tenant is a tenant-isolation bug; a
    hallucinated source tier lifts the cap `RULES.md` §4 puts on auto-writable
    impact; a hallucinated capture time rewrites when a fact was believed.

    S5.6's orchestrator builds one per proposal and hands it to every Layer-1
    stage, which is the shape the notebook's `deps` sketch anticipates.

    Attributes:
        tenant_id: Owning tenant, carried onto every candidate and the value
            Postgres RLS is set from.
        namespace: Isolation scope, e.g. `"patient:8812"`.
        trace_id: This proposal's trace. On every candidate and every raise -
            `RULES.md` §2.3 requires it of both.
        source_tier: How far the content may be trusted. Supplied by the gateway
            from the request and never inferred, because inferring it would make
            a security decision by accident.
        captured_at: When the source was captured. A value, not a clock read;
            see the module docstring on replay.
    """

    tenant_id: TenantId
    namespace: Namespace
    trace_id: TraceId
    source_tier: SourceTier
    captured_at: datetime


class ExtractionBatch(GMModel):
    """One sample's reply. Named by `prompts/extract_memories/v1.md`.

    Lives here rather than in `guardmem_core.schemas` for the reason
    `NoiseClassification` does: it is the wire shape of one prompt's reply, and
    it changes when that prompt version changes. The `ExtractedFact` it wraps is
    a schema-layer model, because `ExtractionResult` carries it.

    An empty `facts` list is valid and is frequently the right answer - the
    prompt says so explicitly, because a model that believes it must return
    something will invent it.
    """

    facts: list[ExtractedFact]


def _source_hash(content: str) -> str:
    """The digest the spans are recorded against.

    Prefixed `sha256:` to match the shape `MCP_INTEGRATION.md` §2.2 publishes,
    and so that an algorithm change cannot silently look like the old one.
    """
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _draw(llm: LLMClient, *, prompt: str, k: int, tier: Tier) -> list[LLMResponse]:
    """Draw K samples: the canonical one at temperature 0, the rest at 0.7.

    Args:
        llm: The client.
        prompt: The rendered prompt, identical across both calls - §1.2 draws
            the samples "with the same prompt", and varying it would make the
            spread a measure of the prompt rather than of the model.
        k: How many samples in total.
        tier: Which rung to route to.

    Returns:
        One response for `k == 1`, otherwise two: the canonical draw, then the
        `k-1` entropy draws.

    Raises:
        ProviderUnavailable: propagated from the client.
        BudgetExceeded: propagated from the client.
    """
    canonical = await llm.complete(
        prompt=prompt, schema=ExtractionBatch, tier=tier, temperature=0.0, n=1
    )
    if k == 1:
        return [canonical]
    spread = await llm.complete(
        prompt=prompt, schema=ExtractionBatch, tier=tier, temperature=0.7, n=k - 1
    )
    return [canonical, spread]


def _parse(
    responses: Sequence[LLMResponse], *, k: int, trace_id: TraceId
) -> list[list[ExtractedFact]]:
    """Flatten the responses into exactly K validated sample sets, canonical first.

    Args:
        responses: What the draws returned.
        k: How many samples were asked for.
        trace_id: Attached to either error.

    Returns:
        The facts each sample proposed, in draw order.

    Raises:
        ProviderUnavailable: if the provider returned a different number of
            samples than were asked for. Absorbing a short count would let K
            collapse toward 1, where §3.1 sets ``H_norm := 0`` - zero entropy,
            which is *maximum* confidence on that term. A degraded provider
            would then widen the auto-write path, and `ARCHITECTURE.md` §0
            forbids exactly that. Retryable, so the proposal parks rather than
            being scored on a thinner sample set than its risk hint asked for.
        ValidationRejected: if any sample is not valid `ExtractionBatch` JSON.
            Deliberately fatal for the whole extraction rather than a per-sample
            skip: `RULES.md` §2.1 puts it plainly - `extra="forbid"` "matters
            most on LLM structured output: a hallucinated field should raise,
            not vanish silently" - and dropping a bad sample quietly would make
            the entropy denominator a lie in the same direction a short count
            does. The client was given `schema=`, so the provider was asked to
            constrain generation; a reply that does not validate is a broken
            contract, not noise.
    """
    samples = [sample for response in responses for sample in response.samples]
    if len(samples) != k:
        raise ProviderUnavailable(
            f"extraction asked for {k} samples and received {len(samples)}; "
            "scoring on a short sample set would raise confidence, not lower it",
            trace_id=trace_id,
        )
    parsed: list[list[ExtractedFact]] = []
    for index, sample in enumerate(samples):
        try:
            parsed.append(ExtractionBatch.model_validate_json(sample).facts)
        except ValidationError as error:
            raise ValidationRejected(
                f"extraction sample {index} did not validate against ExtractionBatch",
                trace_id=trace_id,
                sample_index=index,
                errors=error.error_count(),
            ) from error
    return parsed


def _to_candidates(
    facts: Sequence[ExtractedFact],
    *,
    content: str,
    context: ExtractionContext,
    extracted_by: str,
    prompt_version: str,
) -> tuple[list[MemoryCandidate], int]:
    """Span-link the canonical facts and wrap the survivors as candidates.

    Args:
        facts: What the canonical sample proposed.
        content: The document the spans index into.
        context: The caller-owned fields.
        extracted_by: The model id that actually served the call.
        prompt_version: Which prompt file produced the facts.

    Returns:
        The candidates, and how many facts were rejected for want of a span.
    """
    source_hash = _source_hash(content)
    candidates: list[MemoryCandidate] = []
    unsourced = 0
    for fact in facts:
        match = link_span(fact.verbatim, content)
        if match is None:
            unsourced += 1
            continue
        candidates.append(
            MemoryCandidate(
                # `c_1`, `c_2`, … over the *survivors* - the shape
                # `MCP_INTEGRATION.md` §2.2 publishes, and what
                # `schemas/review.py` means by identifying a decision as
                # `(trace_id, candidate_id)`. A uuid would also work, and would
                # make every replay produce different ids for the same inputs.
                candidate_id=CandidateId(f"c_{len(candidates) + 1}"),
                tenant_id=context.tenant_id,
                namespace=context.namespace,
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                provenance=Provenance(
                    source_hash=source_hash,
                    source_span=match.span,
                    source_tier=context.source_tier,
                    # The SOURCE text, not `fact.verbatim`. ADR-0007: a
                    # reviewer's quote and their highlight are rendered from
                    # different fields and must not be able to disagree.
                    verbatim=match.text,
                    alignment=match.alignment,
                    captured_at=context.captured_at,
                ),
                extracted_by=extracted_by,
                prompt_version=prompt_version,
                trace_id=context.trace_id,
            )
        )
    return candidates, unsourced


def _assemble(
    samples: Sequence[list[ExtractedFact]],
    responses: Sequence[LLMResponse],
    *,
    content: str,
    context: ExtractionContext,
    k: int,
    prompt_version: str,
    dropped_noise: int,
) -> ExtractionResult:
    """Span-link the canonical sample and total up what the extraction cost.

    Args:
        samples: Every sample's facts, canonical first.
        responses: The draws they came from, for the billing totals.
        content: The document the spans index into.
        context: The caller-owned fields.
        k: How many samples were asked for.
        prompt_version: Which prompt file produced them.
        dropped_noise: Carried through from the noise filter.

    Returns:
        The completed `ExtractionResult`.
    """
    candidates, unsourced = _to_candidates(
        samples[0],
        content=content,
        context=context,
        # The id that actually served the call, not the tier's configured pin -
        # `llm/base.py` records why: a fallback can serve a FAST call from
        # another provider entirely, and replay has to know which one did.
        extracted_by=responses[0].model,
        prompt_version=prompt_version,
    )
    return ExtractionResult(
        candidates=candidates,
        samples=list(samples),
        k_samples=k,
        dropped_noise=dropped_noise,
        dropped_unsourced=unsourced,
        tokens_in=sum(response.tokens_in for response in responses),
        tokens_out=sum(response.tokens_out for response in responses),
        # Every call, or it is not a hit. A partial hit reported as a hit would
        # overstate the >=40% cache-hit assumption in `PRD.md` §6.5.
        cache_hit=all(response.cache_hit for response in responses),
    )


async def extract(
    content: str,
    llm: LLMClient,
    *,
    context: ExtractionContext,
    ontology_yaml: str,
    k: int,
    tier: Tier,
    dropped_noise: int = 0,
) -> ExtractionResult:
    """Extract durable facts from `content`, K times, and span-link the canonical set.

    The module docstring covers `content`, `ontology_yaml` and the context.

    Args:
        content: The source document; its sha256 becomes `source_hash`.
        llm: The client. Called once for `k == 1`, twice otherwise.
        context: The caller-owned fields a model may not supply.
        ontology_yaml: The tenant ontology, already rendered into text.
        k: How many samples to draw. §1.2's ladder is 1 / 3 / 5 by risk hint.
        tier: Which rung serves the call. Undefaulted like
            `LLMClient.complete`'s, so no call site drifts onto FRONTIER by
            omission, and **not** read from the prompt file - §1.2 makes the
            tier a function of K, so it is a routing decision.
        dropped_noise: How many turns the noise filter ate.

    Returns:
        The canonical candidates, every sample's facts in draw order, the counts
        §1.1 and §1.3 require, and the billing totals summed across every call.

    Raises:
        InjectionDetected: the canary appeared in a completion. `RULES.md` §3
            treats that as confirmed injection, not a heuristic.
        ValidationRejected: a sample did not validate against `ExtractionBatch`.
        ProviderUnavailable: the provider is unreachable, or returned a
            different number of samples than were asked for.
        BudgetExceeded: the tenant's cap is reached.
        ValueError: `k` is below 1.
    """
    if k < _MIN_K:
        raise ValueError(f"k must be at least {_MIN_K}, got {k}")

    canary = secrets.token_hex(_CANARY_BYTES)
    prompt = render(
        _PROMPT_NAME,
        _PROMPT_VERSION,
        {"content": content, "ontology": ontology_yaml, "canary": canary},
    )
    responses = await _draw(llm, prompt=prompt.text, k=k, tier=tier)
    if any(canary in sample for response in responses for sample in response.samples):
        raise InjectionDetected("extraction echoed the canary token", trace_id=context.trace_id)

    samples = _parse(responses, k=k, trace_id=context.trace_id)
    return _assemble(
        samples,
        responses,
        content=content,
        context=context,
        k=k,
        prompt_version=prompt.version_id,
        dropped_noise=dropped_noise,
    )
