# MEMORY_ENGINE.md — The 3-Layer Pipeline & Scoring Specification

This is the spec of record. Code that disagrees with this document is wrong until an ADR changes the
document.

```
raw turns ──► L1 EXTRACTION ──► L2 VALIDATION & CONFLICT ──► L3 SCORING ──► DECISION MATRIX
              noise → cands       ontology, NLI, dedupe        H, C, R        AUTO|HITL|REJECT|ESC
```

---

## 0. Core Schemas

```python
from datetime import datetime
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

class Cardinality(StrEnum):
    ONE = "one"          # employer, primary_dx, account_owner  → new value supersedes
    MANY = "many"        # allergy, hobby, matter_tag           → accumulates
    ONE_PER_TIME = "one_per_time"   # address, role — one valid per interval

class ImpactLevel(StrEnum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"

class SourceTier(StrEnum):
    TRUSTED_SYSTEM = "trusted_system"     # verified EHR record, signed API payload
    VERIFIED_USER  = "verified_user"      # authenticated human in-session
    UNVERIFIED_USER = "unverified_user"
    TOOL_OUTPUT    = "tool_output"
    RETRIEVED_WEB  = "retrieved_web"      # lowest trust; never auto-writes HIGH impact

class Provenance(_M):
    source_hash: str                       # sha256 of the source document/turn
    source_span: tuple[int, int]           # char offsets into that document
    source_tier: SourceTier
    verbatim: str = Field(max_length=2000) # the SOURCE text at that span, never the claim
    alignment: float = Field(default=1.0, ge=0, le=1)  # claim vs stored text (ADR-0007)
    captured_at: datetime

class MemoryCandidate(_M):
    candidate_id: str
    tenant_id: str
    namespace: str                         # e.g. "patient:8812" | "org:acme" | "session:xyz"
    subject: str                           # entity canonical ref or surface form
    predicate: str                         # must exist in tenant ontology
    object: str | float | bool | dict
    valid_from: datetime | None = None     # world-time the fact became true
    valid_to: datetime | None = None
    provenance: Provenance
    extracted_by: str                      # pinned id, e.g. "claude-haiku-4-5"
    prompt_version: str
    trace_id: str

class ExtractedFact(_M):                   # what a model is allowed to assert
    subject: str
    predicate: str
    object: str | float | bool | dict
    verbatim: str = Field(max_length=2000)

class ExtractionResult(_M):
    candidates: list[MemoryCandidate]
    samples: list[list[ExtractedFact]]     # draw order; sample 0 is canonical (ADR-0006)
    k_samples: int
    dropped_noise: int
    dropped_unsourced: int                 # facts §1.3 rejected for want of a span (ADR-0006)
    tokens_in: int
    tokens_out: int
    cache_hit: bool

class ConflictKind(StrEnum):
    NONE = "none"
    DUPLICATE = "duplicate"            # merge, no new row
    REFINEMENT = "refinement"          # strictly more specific than incumbent
    CONTRADICTION = "contradiction"    # NLI says mutually exclusive
    CARDINALITY = "cardinality"        # ONE-predicate already has a live value
    TEMPORAL_OVERLAP = "temporal_overlap"

class ConflictReport(_M):
    kind: ConflictKind
    incumbent_assertion_id: str | None
    entailment: float                  # P(incumbent ⊨ candidate)
    contradiction: float               # P(mutually exclusive)
    cosine: float
    resolution_hint: Literal["merge", "supersede", "coexist", "escalate"]

class ConfidenceReport(_M):
    semantic_entropy: float            # H_norm ∈ [0,1]
    grounding: float                   # S_src  ∈ [0,1]
    schema_fit: float                  # S_sch  ∈ [0,1]
    corroboration: float               # S_cor  ∈ [0,1]
    consistency: float                 # S_con  ∈ [0,1]
    confidence: float                  # C      ∈ [0,1]
    weights_version: str

class RiskVerdict(_M):
    impact_level: ImpactLevel
    risk: float                        # R ∈ [0,1]
    features: dict[str, float]         # named contributions, for the UI + audit
    obligations: list[str]             # from policy engine, e.g. ["require_corroboration"]

class Decision(StrEnum):
    AUTO_WRITE = "auto_write"
    HITL_REVIEW = "hitl_review"
    REJECT = "reject"
    ESCALATE = "escalate"

class DecisionRecord(_M):
    decision: Decision
    reason_codes: list[str]            # ["C_BELOW_TAU_HI", "R_ABOVE_RHO_LO", "POL_PHI_REVIEW"]
    confidence: ConfidenceReport
    risk: RiskVerdict
    conflict: ConflictReport
    thresholds_version: str
    policy_version: str
    escalated_from: Decision | None = None
```

---

## 1. Layer 1 — Extraction & Noise Reduction

### 1.1 Noise filter (pre-model, cheap)
Runs before any billable call. Drops:

| Class | Example | Rule |
|---|---|---|
| Ephemeral | "one sec, let me check" | discourse-marker classifier + length |
| Imperative to agent | "summarize that again" | second-person imperative, no declarative content |
| Restatement | agent's own prior output echoed back | ≥0.93 cosine to a message already in this trace |
| Hypothetical | "if I moved to Austin, would…" | conditional/subjunctive markers |
| Third-party gossip | "my sister says she's vegan" | subject ≠ namespace subject and no ontology license for it |

Implementation is a hybrid: rules for the cheap 70%, FAST-tier classifier for the rest. Everything
dropped is *counted* (`dropped_noise`) and sampled into the dashboard funnel — you must be able to
see what the filter is eating.

### 1.2 K-sample structured extraction
`K` is chosen by risk hint and namespace policy:

| risk_hint | K | tier |
|---|---|---|
| `LOW` (session-scoped, MANY-cardinality predicates) | 1 | FAST |
| `DEFAULT` | 3 | FAST |
| `HIGH` (HIGH/CRITICAL impact namespace, or ONE-cardinality) | 5 | BALANCED |

Samples are drawn at temperature 0.7 with the same prompt; sample 0 is drawn at temperature 0 and is
the *canonical* candidate text. The other K−1 exist only to estimate uncertainty.

### 1.3 Span linking
Every candidate must resolve to a verbatim span. The extractor is asked to return the supporting
substring; `span_linker` then locates it in the source with exact match, falling back to fuzzy
alignment (rapidfuzz, ratio ≥ 92) and rejecting below that. **No span → `REJECT(reason=UNSOURCED)`.**
This single rule kills most confabulated facts before any scoring happens.

A fuzzy span is snapped to whole words and stripped of surrounding whitespace before it is stored,
and `Provenance.verbatim` is then the *source* text at that span rather than the model's claim —
the aligner's raw span truncates words (`allergic to penicilli`), which is not a quote a reviewer
can act on. How far the claim was from the stored text is recorded as `Provenance.alignment`, which
is the input §3.2's fuzzy-match penalty needs. See ADR-0007.

Every rejection is *counted*, into `ExtractionResult.dropped_unsourced`, for the
same reason §1.1 counts noise drops: a rule whose activation count cannot be seen
cannot be tuned, and a span linker that quietly stopped matching would look like
a drop in recall rather than a bug. See ADR-0006.

---

## 2. Layer 2 — Entity Schema Validation & Conflict Detection

### 2.1 Schema gate
The tenant ontology declares entity types, predicates, value types, cardinality, impact level, and
allowed source tiers:

```yaml
# ontology/clinical.yaml
predicates:
  allergy:
    subject: Patient
    object: {type: coded, system: RxNorm}
    cardinality: many
    impact: critical
    min_source_tier: verified_user
    requires_corroboration: true
  primary_care_provider:
    subject: Patient
    object: {type: entity_ref, entity: Provider}
    cardinality: one
    impact: medium
  preferred_pharmacy:
    subject: Patient
    object: {type: entity_ref, entity: Pharmacy}
    cardinality: one_per_time
    impact: low
```

Unknown predicate → `quarantine` namespace (retrievable, flagged, never promoted without review).
Type coercion failure → `REJECT(reason=SCHEMA)`. Ontologies are versioned; a schema change is an
audited event and triggers a revalidation sweep of affected assertions.

### 2.2 Conflict detection — three independent checks

Retrieve incumbents: top-k (k=10) by cosine within `(namespace, subject, predicate)` plus graph
neighbors 1 hop out. Then:

**(a) NLI contradiction.** Cross-encoder NLI (DeBERTa-v3-MNLI class, or BALANCED-tier LLM judge for
long/typed objects) over the pair `(incumbent_verbatim, candidate_verbatim)` in both directions:

```
entail_fwd = P(incumbent ⊨ candidate)
entail_rev = P(candidate ⊨ incumbent)
contra     = max(P(contradiction_fwd), P(contradiction_rev))
```

**(b) Cardinality.** If `cardinality == ONE` and a live incumbent exists with a different object →
`CARDINALITY` conflict regardless of NLI.

**(c) Temporal overlap.** If `ONE_PER_TIME` and `[valid_from, valid_to)` intervals intersect with a
differing object → `TEMPORAL_OVERLAP`.

### 2.3 Resolution matrix

| cosine | entail_fwd ∧ entail_rev | contra | cardinality hit | Kind | Resolution |
|---|---|---|---|---|---|
| ≥ 0.95 | both ≥ 0.85 | — | — | DUPLICATE | **merge** — bump corroboration, add provenance, no new row |
| ≥ 0.80 | rev ≥ 0.85, fwd < 0.85 | < 0.3 | no | REFINEMENT | **supersede** (candidate is more specific) |
| any | — | ≥ 0.65 | — | CONTRADICTION | **supersede** if candidate newer *and* C ≥ τ_hi, else **escalate** |
| any | — | < 0.65 | yes | CARDINALITY | **supersede** with tombstone (always audited) |
| < 0.80 | — | < 0.3 | no | NONE | **coexist** |
| — | — | 0.3–0.65 | — | ambiguous | **escalate** — the NLI is unsure, so a human or frontier model decides |

**Supersession is never silent.** It writes: `prior.valid_to = candidate.valid_from`,
`prior.superseded_by = new.id`, a `SUPERSEDES` graph edge, and an audit event. Retrieval stops
returning the prior assertion; audit and point-in-time queries still can.

### 2.4 Deduplication & merge
Merging increments `corroboration_count`, appends the new `Provenance`, and recomputes confidence
with the corroboration term — a fact stated by three independent sources should score higher than the
same fact stated once, and this is the mechanism.

---

## 3. Layer 3 — Risk / Entropy Scoring

### 3.1 Semantic Entropy (uncertainty)

Lexical variance is not uncertainty — "lives in Austin" and "resides in Austin, TX" are one meaning.
So cluster the K samples by **bidirectional entailment** and take entropy over meaning clusters
(Kuhn et al. 2023; Farquhar et al. 2024).

```
1. For each pair (s_i, s_j) in the K samples for a given (subject, predicate):
       same_meaning(s_i, s_j)  ⟺  entail(s_i → s_j) ≥ 0.8  ∧  entail(s_j → s_i) ≥ 0.8
2. Union-find → clusters c_1..c_m with sizes n_1..n_m,  Σ n = K
3. p(c_i) = n_i / K
4. H  = − Σ p(c_i) · log p(c_i)
5. H_norm = H / log K            (K > 1;  H_norm := 0 when K = 1)
```

Additionally, a candidate that appears in **zero** clusters containing sample 0's meaning is dropped
as a minority hallucination.

> **Reuse note:** the entailment-clustering and AUROC scaffolding here is the same machinery as
> LID's semantic-entropy detector. The `EntropyScorer` protocol is deliberately shaped so LID can be
> plugged in as the backend implementation rather than reimplemented.

**Worked example** (K=5, predicate `primary_care_provider`):
samples → {"Dr. Alvarez", "Dr. Alvarez", "Alvarez, MD", "Dr. Chen", "unclear"}
clusters → {Alvarez: 3}, {Chen: 1}, {unclear: 1} → p = (0.6, 0.2, 0.2)
H = −(0.6 ln0.6 + 0.2 ln0.2 + 0.2 ln0.2) = 0.950; H_norm = 0.950 / ln 5 = **0.590**
→ high uncertainty on a `ONE`-cardinality predicate → will not auto-write.

### 3.2 Confidence composite

```
C = w_H·(1 − H_norm) + w_g·S_src + w_s·S_sch + w_c·S_cor + w_k·S_con

default weights (v1, namespace-overridable, sum = 1):
  w_H = 0.35   uncertainty
  w_g = 0.25   source grounding
  w_s = 0.10   schema fit
  w_c = 0.15   corroboration
  w_k = 0.15   consistency with existing memory
```

| Term | Definition |
|---|---|
| `S_src` | grounding: entailment of the candidate by its own verbatim span, × source-tier multiplier `{trusted 1.0, verified 0.95, unverified 0.8, tool 0.85, web 0.6}`; fuzzy-match penalty applied if span alignment < 1.0 |
| `S_sch` | 1.0 exact ontology fit; 0.7 coerced; 0.4 unknown-but-plausible; 0 reject (never reaches scoring) |
| `S_cor` | `1 − exp(−λ·(n_independent_sources − 1))`, λ=0.8 → 1 source 0.0, 2 → 0.55, 3 → 0.80, 4 → 0.91 |
| `S_con` | `1 − contra` from Layer 2 against live memory; `REFINEMENT` scores 0.9 rather than penalizing |

### 3.3 Impact Risk (blast radius)

Risk is *not* the inverse of confidence. A perfectly-confident write to the account-owner field is
still a high-risk operation. Compute a linear score, squash, then floor by declared impact.

```
z = Σ β_f · x_f
R_raw = σ(z) = 1 / (1 + e^(−z))
R = max(R_raw, floor[impact_level])        floors: low .15, medium .35, high .60, critical .80
```

| Feature `x_f` | Definition | β |
|---|---|---|
| `impact_declared` | ontology impact mapped {0,.33,.66,1} | 2.20 |
| `mutation_type` | coexist 0, refine .3, supersede .7, delete/retract 1 | 1.60 |
| `scope` | session .1, user .5, org 1.0 (shared blast radius) | 1.30 |
| `graph_fanout` | `min(1, log(1+deg(subject))/log(1+50))` — how much depends on this node | 0.90 |
| `pii_class` | none 0, quasi-identifier .5, direct .8, special-category 1 | 1.10 |
| `irreversibility` | can this be practically undone downstream? {0,.5,1} | 1.40 |
| `source_tier_risk` | 1 − trust multiplier | 1.00 |
| `novelty` | 1 − max cosine to existing memory (unprecedented claims are riskier) | 0.50 |
| bias | | −3.40 |

`features` is persisted verbatim on the `RiskVerdict` so the review UI can show *why* something was
flagged, and so `threshold_tuner.py` can refit β from reviewer labels (logistic regression on
approve/reject, refit weekly, gated by a κ check before promotion).

### 3.4 Decision Matrix

Default thresholds (per-namespace, versioned): `τ_lo = 0.45`, `τ_mid = 0.60`, `τ_hi = 0.78`,
`ρ_lo = 0.35`, `ρ_hi = 0.70`.

The matrix has **four** confidence bands, so it needs **three** confidence thresholds. `τ_mid`
separates the escalate band from the auto-write-at-low-risk band; without it the 0.60 boundary
below is a magic number and `decide()` cannot be configured from settings. All five thresholds
are settings fields (`GM_TAU_LO`, `GM_TAU_MID`, `GM_TAU_HI`, `GM_RHO_LO`, `GM_RHO_HI`) and are
carried on `thresholds_version`.

```
                                   R (impact risk)
                        ≤ ρ_lo      ρ_lo – ρ_hi       > ρ_hi
                       (≤ 0.35)    (0.35 – 0.70)     (> 0.70)
                    ┌────────────┬───────────────┬───────────────┐
 C ≥ τ_hi           │ AUTO_WRITE │ AUTO_WRITE *  │  HITL_REVIEW  │   * only if corroboration ≥ 2
 (≥ 0.78)           ├────────────┼───────────────┼───────────────┤
 τ_mid ≤ C < τ_hi   │ AUTO_WRITE │  HITL_REVIEW  │  HITL_REVIEW  │
 (0.60 – 0.78)      ├────────────┼───────────────┼───────────────┤
 τ_lo ≤ C < τ_mid   │  ESCALATE  │   ESCALATE    │  HITL_REVIEW  │
 (0.45 – 0.60)      ├────────────┼───────────────┼───────────────┤
 C < τ_lo           │   REJECT   │    REJECT     │ HITL_REVIEW † │   † CRITICAL impact: a human
 (< 0.45)           └────────────┴───────────────┴───────────────┘     sees even the rejections
```

Bands are half-open: a band's lower bound is inclusive, its upper bound exclusive. `C` exactly at
a threshold falls in the higher band. This matters for the totality property test (I4).

Hard overrides applied **after** the matrix (obligations compose, they never relax):

```
1. injection_detected            → QUARANTINE (never AUTO_WRITE)
2. source_tier == RETRIEVED_WEB ∧ impact ≥ HIGH        → HITL_REVIEW
3. requires_corroboration ∧ n_sources < 2              → HITL_REVIEW
4. conflict.resolution_hint == "escalate"              → ESCALATE (or HITL if already escalated)
5. budget cap reached | provider circuit open          → HITL_REVIEW
6. policy obligation "require_review"                  → HITL_REVIEW
7. impact == CRITICAL ∧ mutation == delete             → HITL_REVIEW + step-up auth
```

`ESCALATE` semantics: re-run L3 (and NLI adjudication) once on the FRONTIER tier with K=5 and the
incumbent context included. The escalated result re-enters the matrix but **cannot escalate again** —
it resolves to AUTO_WRITE, HITL_REVIEW, or REJECT. `escalated_from` is recorded.

```python
def decide(
    conf: ConfidenceReport,
    risk: RiskVerdict,
    conflict: ConflictReport,
    thresholds: Thresholds,
    already_escalated: bool,
) -> DecisionRecord:
    """Pure, total, deterministic. See invariant I4 in RULES.md."""
```

### 3.5 Cost of the ladder

| Stage | Model tier | Typical share of candidates | Relative cost |
|---|---|---|---|
| Noise filter | rules + FAST | 100% (≈35% dropped here) | 1× |
| Extraction K=1–3 | FAST | 65% | 6× |
| Extraction K=5 + NLI | BALANCED | 12% | 22× |
| Escalation | FRONTIER | ≤6% | 90× |

Measured against "everything on FRONTIER, K=5" this ladder is the source of the ≥55% token-savings
target in the PRD.

---

## 4. Dynamic Compression & Garbage Collection ("thread rot")

Nightly (and on-demand at namespace token pressure):

```
decay(a) = ω_r·recency(a) + ω_u·usage(a) + ω_s·salience(a) − ω_c·contradiction_pressure(a)

recency(a)  = exp(−Δt / τ_ns)              τ_ns per namespace (clinical 180d, session 7d)
usage(a)    = log(1 + retrieval_hits_30d) / log(1 + 50)
salience(a) = 0.5·impact_norm + 0.3·conf + 0.2·graph_centrality(subject)
contradiction_pressure = fraction of neighbours that contradict a
defaults: ω_r .35, ω_u .25, ω_s .30, ω_c .30
```

| decay | Tier | Behavior |
|---|---|---|
| ≥ 0.55 | HOT | in primary index, eligible for context |
| 0.25–0.55 | WARM | indexed, deprioritized in ranking |
| 0.10–0.25 | COLD | moved out of HNSW index into rollup; retrievable on explicit lookup |
| < 0.10 | ARCHIVE | episodic digest only; original retained for audit |

**Rollup:** cold assertions in a namespace are summarized into a single dated `EpisodicDigest`
(BALANCED tier, with the constituent `assertion_id`s attached), so the semantics survive at ~5% of
the tokens and the lineage still resolves.

**GC never touches** an assertion that is: referenced by an open review task, under legal hold, part
of an in-flight audit export, or `impact == CRITICAL` (critical facts move tiers but are never
archived out of retrieval).

**Context packing** at read time enforces the caller's token budget with a greedy
`score/token` knapsack over `relevance × confidence × recency`, dedupes near-identical assertions,
and emits the inclusion/exclusion list into the trace — so "why wasn't that in context?" is an
answerable question.

---

## 5. Metrics the engine must emit

| Metric | Why it exists |
|---|---|
| `write_precision` | is AUTO_WRITE trustworthy |
| `contradiction_escape_rate` | did L2 do its job |
| `stale_fact_rate` | is GC keeping up with thread rot |
| `hitl_ratio` + `hitl_agreement_kappa` | are thresholds calibrated |
| `escalation_rate` | is the ambiguous band too wide (cost) |
| `noise_drop_rate` | is L1 eating real signal |
| `entropy_distribution` per namespace | early warning of extraction degradation |
| `mean_tokens_per_governed_candidate` | the cost story |
