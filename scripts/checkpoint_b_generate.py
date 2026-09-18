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
import pathlib
from typing import TYPE_CHECKING, Any, Final

import asyncpg
import httpx

from guardmem_core.errors import ProviderUnavailable
from guardmem_core.llm.entailment import LLMEntailer
from guardmem_core.llm.providers import build_llm
from guardmem_core.memory.entities import NamespaceEntityResolver
from guardmem_core.memory.graph.networkx_store import NetworkXGraphStore
from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.pgvector_store import PgVectorStore
from guardmem_core.memory.vector.pool import create_pool, libpq_dsn
from guardmem_core.pipeline.deps import Deps
from guardmem_core.pipeline.l2_validate import LLMJudge
from guardmem_core.pipeline.l3_score import V1_BETAS, V1_WEIGHTS
from guardmem_core.pipeline.orchestrator import run
from guardmem_core.pipeline.per_candidate import GovernedCandidate
from guardmem_core.schemas.ontology import load_ontology
from guardmem_core.settings import Settings, get_settings
from guardmem_core.types import TenantId
from scripts.checkpoint_b_proposals import read_proposals

if TYPE_CHECKING:
    from guardmem_core.llm.base import LLMClient

__all__ = ["generate"]

# K comes from `Settings.default_k`, not a literal here. It was `_K: Final = 3`
# until 2026-09-18, on the cost ladder; the 88-candidate run then measured
# `uncertainty` at three distinct values while it carried `w_H = 0.35`, the
# largest weight in `C`, so the heaviest term was also the coarsest.
#
# Reading the setting is the point rather than swapping one literal for another:
# a hardcoded K left `GM_DEFAULT_K` decorative *for this script*, so an operator
# could set 5, sample 3, and get a corpus disagreeing with its own configuration
# with nothing raising.

# The gate's own sample size, from the checkpoint's step 1.
_TARGET: Final = 200

# What `--provider` accepts. `Settings.llm_provider` also offers `openai`; this
# harness does not, because nothing has been measured against it and the gate's
# sign-off names the provider that produced the corpus.
#
# The local URL, model id and request timeout come from `Settings` - see
# `_provider_settings` for the three `os.environ.get` constants that used to
# stand here and why reading them that way silently ignored `.env`.
_PROVIDERS: Final = ("ollama", "anthropic")


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
    settings = _provider_settings(provider, get_settings())
    pool = await create_pool(libpq_dsn(str(settings.database_url)), min_size=1, max_size=4)
    rows: list[dict[str, Any]] = []
    barren: list[str] = []
    try:
        await _ensure_tenant(pool, tenant)
        async with (
            httpx.AsyncClient(base_url=settings.ollama_url) as http,
            build_llm(settings, http) as llm,
        ):
            deps = _deps(llm, pool, tenant, settings)
            batch = read_proposals(proposals, tenant, settings.default_k)
            for index, proposal in enumerate(batch, start=1):
                try:
                    result, failures = await run(proposal, deps)
                except ProviderUnavailable:
                    # Rows live in memory until the end, so one unreachable call
                    # used to discard the whole batch. Named, so a re-run targets it.
                    barren.append(f"{proposal.namespace} (provider unreachable)")
                    print(f"  [{index}] {proposal.namespace}: SKIPPED, unreachable")
                    continue
                rows.extend(_row(item) for item in result.governed)
                if not result.governed:
                    barren.append(f"{proposal.namespace} (0 scored)")
                print(
                    f"  [{index}] {proposal.namespace}: {len(result.governed)} scored, "
                    f"{len(result.rejected)} rejected, {len(result.quarantined)} quarantined, "
                    f"{len(failures)} failed"
                )
                for failure in failures:
                    print(f"      {failure.candidate_id}: {failure.error}")
    finally:
        await pool.close()
    if barren:  # a conversation can contribute nothing; the total hides which
        print(f"  {len(barren)} contributed nothing: {'; '.join(barren)}")
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


def _provider_settings(provider: str, settings: Settings) -> Settings:
    """Point `settings` at the provider named on the command line.

    Args:
        provider: `ollama` or `anthropic`, from `--provider`.
        settings: The process configuration, as the environment supplied it.

    Returns:
        A copy whose `llm_provider` is what the command line asked for, ready to
        hand to `build_llm`.

    Raises:
        ValueError: the provider is unknown, or Anthropic was asked for without
            a key. The second is worth its own message: `GM_ANTHROPIC_API_KEY`
            has been blank since S0.2 and "it silently fell back to the local
            model" is the kind of thing that makes two AUROCs incomparable.

    **This used to build the adapters itself**, and `selection.build_llm`'s own
    docstring names this module as the second composition root that must not be
    allowed to answer differently - "a second copy of the mapping would be free
    to drift on which provider a blank key falls back to". It drifted anyway,
    not on the fallback but on everything around it: the local URL, model and
    timeout came from three module-level `os.environ.get` constants rather than
    from `Settings`. `pydantic-settings` reads `.env` directly and does **not**
    export it to `os.environ`, so `GM_OLLAMA_MODEL` set in `.env` - which is
    where `.env.example` says to set it - configured the rest of the process and
    was silently ignored here. A corpus generated against a different model than
    the operator selected is the one defect this whole harness exists to avoid.

    The key check stays local because the message has to: `build_llm` tells an
    operator to set `GM_LLM_PROVIDER=ollama`, which is the right advice for a
    server and the wrong advice here, where `--provider` is what selects and the
    environment variable would be overwritten a line later. It is one `if` and
    it cannot drift on the fallback, because it does not fall back.
    """
    if provider not in _PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r}; expected "
            + " or ".join(repr(name) for name in _PROVIDERS)
        )
    if provider == "anthropic" and not settings.anthropic_api_key:
        raise ValueError(
            "GM_ANTHROPIC_API_KEY is empty. Set it, or pass `--provider ollama`; "
            "this will not quietly run on a different model than you asked for."
        )
    return settings.model_copy(update={"llm_provider": provider})


def _deps(llm: LLMClient, pool: asyncpg.Pool, tenant: TenantId, settings: Settings) -> Deps:
    """Compose a real pipeline.

    `NetworkXGraphStore` and `HashEmbedder` rather than the real things, and both
    matter for reading the result: the graph is in-process so `graph_fanout` sees
    only what this run wrote, and hash vectors model no semantics so `novelty`
    and incumbent retrieval are near-noise. Neither feeds `C` - they feed `R` -
    so the gate's own number is unaffected, but a diagnostic over `R` from this
    corpus would not mean much.

    **S7.1 built the Neo4j backend and this deliberately does not use it.** The
    line above used to say "S7.1 is unbuilt", which was the whole reason; the
    reason now is better. A durable graph would let run N+1 see run N's edges,
    so `graph_fanout` would drift between two runs of the same corpus and two
    AUROCs measured a week apart would not be comparable. `build_graph` is the
    right call for a server and the wrong one for a benchmark, and a harness
    whose inputs move underneath it is measuring the wrong thing. Naming the
    class here is the deliberate opt-out.
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
