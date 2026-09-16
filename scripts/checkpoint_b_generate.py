"""CHECKPOINT B's generation step: real extraction, real scores, no labels.

The gate's step 3 is "run the pipeline, collect `C` for each", and this is it.
Everything else in `checkpoint_b.py` has shipped since Day 5; this was the one
missing piece, first because no `LLMClient` existed (S9.1), then because
`EntityResolver` and the risk features had no implementation (ADR-0008,
ADR-0009), then because Ollama could not compile the extraction schema.

**Why the candidates have to come from a real model.** The checkpoint is blunt:
"hand-authoring 200 of them grades the scorer against the author's idea of a
plausible mistake, which is the 'no model grading' rule broken in a different
costume". What the gate measures is whether `C` separates the facts a model
*actually proposes*, so the extraction that produces them has to be the real
one - the same prompt, the same K-sampling, the same span linker.

**And why nothing here writes a label.** `keep` is emitted as `null` on every
row. A human sets it. `discriminate` refuses a corpus with any row unlabelled,
so the file this writes is not scoreable until somebody has read it.

**The scores are oriented so that higher is always better.** That is not
cosmetic. `auroc` reports "the probability that a randomly chosen kept candidate
outranks a rejected one", so a term ranked the wrong way round reports an AUROC
*below* 0.5 and reads as anti-predictive when it is doing its job. `H_norm` is
**uncertainty** - §3.2 weighs `w_H(1 - H_norm)` - so what goes in the row is
`uncertainty = 1 - H_norm`, matching `ConfidenceWeights.uncertainty` and
matching diagnostic 1's own words, "AUROC of `1 - H_norm` alone". The template
shipped a raw `semantic_entropy` key and would have inverted exactly this.

**Nothing is written to memory.** `run()` applies no decision, so the corpus can
be generated against any database without changing what it believes - and the
same proposal can be re-run to compare providers.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import TYPE_CHECKING, Any, Final

import anthropic
import asyncpg
import httpx

from guardmem_core.llm.base import Tier
from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.llm.providers import AnthropicClient, OllamaClient, tier_models
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from guardmem_core.pipeline.orchestrator import Proposal, run
from guardmem_core.pipeline.per_candidate import GovernedCandidate
from guardmem_core.schemas.ontology import load_ontology
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.settings import get_settings
from guardmem_core.types import Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from guardmem_core.llm.base import LLMClient

__all__ = ["generate"]

# `MEMORY_ENGINE.md` §1.2's default. Not the `high` ladder rung: K=5 is five
# completions per proposal and the gate needs hundreds of candidates, so the
# cost ladder is the reason to stay at 3. Diagnostic 4 ("is K too small?") is
# what changes it, and it changes it for a re-run rather than for the first one.
_K: Final = 3

# The gate's own sample size, from the checkpoint's step 1.
_TARGET: Final = 200

_OLLAMA_URL: Final = os.environ.get("GM_OLLAMA_URL", "http://localhost:11434")
_OLLAMA_MODEL: Final = os.environ.get("GM_OLLAMA_MODEL", "llama3.1:8b")

# A local 7B model is slow and this is a batch job, so the per-request ceiling is
# generous rather than the 20s `settings.llm_timeout_s` a request path wants.
_LOCAL_TIMEOUT_S: Final = 600.0


def generate(path: pathlib.Path, proposals: pathlib.Path | None, provider: str, tenant: str) -> int:
    """Run the pipeline over every proposal and write an unlabelled corpus.

    Args:
        path: Where to write the corpus. Refused if it exists - overwriting two
            hours of labelling on a typo is not a thing this should do.
        proposals: A JSONL of proposals, one conversation per line. `None` uses
            the shipped seed transcript, which is a *starting point* and not
            enough - see `_read_proposals`.
        provider: `ollama` or `anthropic`.
        tenant: The tenant to generate under, as a UUID.

    Returns:
        0 when the corpus was written, 1 when it could not be.

    The count is reported against the checkpoint's 200 and **does not fail**
    below it: a short corpus is a real intermediate state - it is how you find
    out the transcripts are too thin - and `score` is what refuses to measure an
    incomplete one.
    """
    if path.exists():
        print(f"{path} exists; refusing to overwrite a corpus somebody may have labelled")
        return 1
    rows = asyncio.run(_generate(proposals, provider=provider, tenant=TenantId(tenant)))
    if not rows:
        print("no candidates were produced; nothing written")
        return 1
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} candidates to {path}")
    if len(rows) < _TARGET:
        print(
            f"  {len(rows)} is short of the checkpoint's {_TARGET}. That is a corpus "
            "problem, not a code one: one conversation is one subject, and the "
            "shipped transcript is forty turns for one patient. Add proposals."
        )
    print("  `keep` is null on every row. A human sets it; no model may.")
    return 0


async def _generate(
    proposals: pathlib.Path | None, *, provider: str, tenant: TenantId
) -> list[dict[str, Any]]:
    """Open the infrastructure once and run every proposal through it.

    Args:
        proposals: The proposals file, or `None` for the seed transcript.
        provider: Which adapter to build.
        tenant: The tenant to generate under.

    Returns:
        One row per scored candidate, across every proposal.

    One pool and one HTTP client for the whole batch, per `RULES.md` §2.2 -
    a client per proposal would throw away the connection pool on a job whose
    whole cost is round trips.
    """
    settings = get_settings()
    pool = await create_pool(libpq_dsn(str(settings.database_url)), min_size=1, max_size=4)
    rows: list[dict[str, Any]] = []
    try:
        await _ensure_tenant(pool, tenant)
        async with httpx.AsyncClient(base_url=_OLLAMA_URL) as http:
            llm = _client(provider, http, settings)
            deps = _deps(llm, pool, tenant, settings)
            for index, proposal in enumerate(_read_proposals(proposals, tenant), start=1):
                result, failures = await run(proposal, deps)
                rows.extend(_row(item) for item in result.governed)
                print(
                    f"  [{index}] {proposal.namespace}: {len(result.governed)} scored, "
                    f"{len(result.rejected)} rejected, {len(result.quarantined)} quarantined, "
                    f"{len(failures)} failed"
                )
                for failure in failures:
                    print(f"      {failure.candidate_id}: {failure.error}")
    finally:
        await pool.close()
    return rows


def _row(item: GovernedCandidate) -> dict[str, Any]:
    """One corpus row, in `template`'s format.

    Args:
        item: One candidate and what the pipeline decided about it.

    Returns:
        The row, with `keep` null and every score oriented higher-is-better.

    **The candidate is where every labellable field comes from**, and it took
    a `GovernedCandidate` to have one. `MEMORY_ENGINE.md` §0's `DecisionRecord`
    carries eight fields and not one of them says what was decided *about* -
    `decide()` takes reports and thresholds, not a candidate - so a corpus built
    from records alone would have been two hundred rows of numbers with nothing
    for a human to judge.

    `decision` is carried alongside, unlabelled and unscored. It is not what the
    gate measures - AUROC is over `C` - but a labeller who can see that the
    pipeline would have auto-written a fact they are about to mark `false` is
    looking at the most useful row in the file.
    """
    candidate, record = item.candidate, item.record
    confidence = record.confidence
    return {
        "candidate_id": str(candidate.candidate_id),
        "keep": None,
        "subject": candidate.subject,
        "predicate": candidate.predicate,
        "object": candidate.object,
        "verbatim": candidate.provenance.verbatim,
        "decision": record.decision.value,
        "scores": {
            "confidence": confidence.confidence,
            # 1 - H_norm: see the module docstring. `H_norm` is uncertainty and
            # ranking by it directly inverts the diagnostic.
            "uncertainty": 1.0 - confidence.semantic_entropy,
            "grounding": confidence.grounding,
            "schema_fit": confidence.schema_fit,
            "corroboration": confidence.corroboration,
            "consistency": confidence.consistency,
        },
    }


def _read_proposals(path: pathlib.Path | None, tenant: TenantId) -> Iterator[Proposal]:
    """Read the proposals to govern, one conversation per line.

    Args:
        path: The JSONL file, or `None` for the shipped seed transcript.
        tenant: The tenant to generate under.

    Yields:
        One `Proposal` per line.

    Raises:
        ValueError: a line is not an object with `namespace` and `turns`.

    **The default is a starting point and the checkpoint's own instruction is
    wrong about it.** Step 1 says "take 200 candidates from the seed
    transcript"; that transcript is forty turns for one patient supporting 28
    seeded facts, so it cannot produce 200 of anything. One conversation is one
    subject under one namespace, so reaching 200 means more conversations - and
    that is corpus work rather than code, which is why this reads a file.

    Each line is `{"namespace": ..., "turns": [...], "source_tier": ...}`.
    `source_tier` defaults to `verified_user`, matching `MCP_INTEGRATION.md`
    §2.2's own default being the weaker `unverified_user` only because a gateway
    cannot vouch for a caller; a transcript assembled for an eval can.
    """
    if path is None:
        from scripts.demo_tenant_data import NAMESPACE, TRANSCRIPT

        print("no --proposals given; using the shipped seed transcript (one subject)")
        yield _proposal(tenant, NAMESPACE, list(TRANSCRIPT), SourceTier.VERIFIED_USER, 1)
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if not isinstance(raw, dict) or "namespace" not in raw or "turns" not in raw:
            raise ValueError(f"{path}:{number}: needs an object with `namespace` and `turns`")
        yield _proposal(
            tenant,
            Namespace(str(raw["namespace"])),
            # `model_validate_json`, NOT `model_validate`. `GMModel` is strict,
            # so a dict carrying `"user"` for a `TurnRole` and an ISO string
            # for a `datetime` is refused - the same trap S5.6 hit with audit
            # payloads. Round-tripping through JSON is what accepts the
            # identical data, and a JSONL file is JSON to begin with.
            [Turn.model_validate_json(json.dumps(turn)) for turn in raw["turns"]],
            SourceTier(raw.get("source_tier", "verified_user")),
            number,
        )


def _proposal(
    tenant: TenantId, namespace: Namespace, turns: Sequence[Turn], tier: SourceTier, number: int
) -> Proposal:
    """Build one proposal.

    The trace id is derived from the line number rather than random, so a
    re-run over the same file produces the same traces and `replay_trace.py`
    has something stable to reproduce.
    """
    return Proposal(
        trace_id=TraceId(f"tr_ckb_{number:04d}"),
        tenant_id=tenant,
        namespace=namespace,
        turns=list(turns),
        source_tier=tier,
        k=_K,
        tier=Tier.FAST,
    )


def _client(provider: str, http: httpx.AsyncClient, settings: Any) -> LLMClient:
    """Build the adapter named on the command line.

    Raises:
        ValueError: the provider is unknown, or Anthropic was asked for without
            a key. The second is worth its own message: `GM_ANTHROPIC_API_KEY`
            has been blank since S0.2 and "it silently fell back to the local
            model" is the kind of thing that makes two AUROCs incomparable.
    """
    if provider == "ollama":
        # The tier ladder collapses to one local model: `settings.model_fast`
        # and its siblings are Claude ids, and a local run has one model. The
        # consequence is that a BALANCED judge call and a FAST extraction hit
        # the same 7B weights, which is worth knowing before reading an AUROC
        # off this corpus - MEMORY_ENGINE.md §3.5's ladder is not being
        # exercised at all.
        return OllamaClient(
            http, models=dict.fromkeys(Tier, _OLLAMA_MODEL), timeout_s=_LOCAL_TIMEOUT_S
        )
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError(
                "GM_ANTHROPIC_API_KEY is empty. Set it, or pass `--provider ollama`; "
                "this will not quietly run on a different model than you asked for."
            )
        return AnthropicClient(
            anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            models=tier_models(settings),
            timeout_s=settings.llm_timeout_s,
        )
    raise ValueError(f"unknown provider {provider!r}; expected 'ollama' or 'anthropic'")


def _deps(llm: LLMClient, pool: asyncpg.Pool, tenant: TenantId, settings: Any) -> Deps:
    """Compose a real pipeline.

    `NetworkXGraphStore` rather than Neo4j (S7.1 is unbuilt) and `HashEmbedder`
    rather than a real embedding model, and both matter for reading the result:
    the graph is in-process so `graph_fanout` sees only what this run wrote, and
    hash vectors model no semantics so `novelty` and incumbent retrieval are
    near-noise. Neither feeds `C` - they feed `R` - so the gate's own number is
    unaffected, but a diagnostic over `R` from this corpus would not mean much.
    """
    embedder = HashEmbedder()
    return Deps(
        llm=llm,
        vector=PgVectorStore(pool, embedder, tenant_id=tenant, timeout_s=settings.store_timeout_s),
        graph=NetworkXGraphStore(),
        embedder=embedder,
        nli=LLMJudge(llm),
        entail=LLMEntailer(llm).lookup,
        resolver=NamespaceEntityResolver(pool, timeout_s=settings.store_timeout_s),
        ontology=load_ontology("clinical"),
        thresholds=settings.thresholds(),
        weights=V1_WEIGHTS,
        betas=V1_BETAS,
        policy_version="checkpoint-b",
        max_concurrent_scores=settings.max_concurrent_scores,
    )


async def _ensure_tenant(pool: asyncpg.Pool, tenant: TenantId) -> None:
    """Create the tenant row the entity foreign key needs.

    The resolver writes an `entity`, `entity.tenant_id` references `tenant`, and
    a corpus run is usually the first thing to touch a scratch tenant. Idempotent
    so a second run over the same tenant is a no-op.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant))
        await conn.execute(
            "INSERT INTO tenant (id, slug) VALUES ($1::uuid, $2) ON CONFLICT (id) DO NOTHING",
            str(tenant),
            f"checkpoint-b-{str(tenant)[:8]}",
        )
