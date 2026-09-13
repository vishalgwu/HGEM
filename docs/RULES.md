# RULES.md — Engineering Standards & System Instructions

These are enforced by CI, not by convention. If a rule below isn't checkable by a linter, a test, or
a CODEOWNERS review gate, it's a suggestion and should be deleted from this file.

---

## 1. Non-Negotiables

1. **No unsourced write.** Any code path that persists an assertion without `source_hash` +
   `source_span` is a P0 bug. Enforced by a DB `NOT NULL` constraint *and* a property test.
2. **No destructive mutation.** `DELETE` on `assertion` is revoked at the role level. Retirement is
   `valid_to = now()` + `superseded_by`. Hard deletion happens only via the GDPR crypto-shred path.
3. **Fail closed.** Every `except` in the decision path resolves to `HITL_REVIEW` or `REJECT`.
   `except: pass` around a guardrail fails CI (custom ruff rule `GM001`).
4. **The audit write is in the same transaction as the state change.** Not after. Not best-effort.
5. **No PII in logs, traces, exceptions, or LLM prompts to non-vault-scoped providers.** Tokenize
   first. Span attributes are allow-listed, not deny-listed.
6. **Determinism where it's claimed.** `DecisionMatrix.decide()` is a pure function. Given the same
   `ConfidenceReport`, `RiskVerdict`, and `PolicyPack@version` it returns the same `Decision` — this
   is asserted by a hypothesis property test, and it's what makes replay meaningful.

---

## 2. Python Standards

**Baseline:** Python 3.12+, `uv` for env/lock, `ruff` (lint + format), `mypy --strict`, Pydantic v2.

### 2.1 Typing
- `mypy --strict` on `packages/guardmem-core`. `Any` requires an inline justification comment; a
  bare `Any` in a public signature fails review.
- Domain IDs are `NewType`, not `str`: `TraceId`, `TenantId`, `CandidateId`, `AssertionId`,
  `EntityId`. Passing a raw `str` where an `AssertionId` is expected must be a type error.
- Public boundaries take and return Pydantic models — never loose `dict[str, Any]`.
- Model config is strict everywhere:

```python
from pydantic import BaseModel, ConfigDict

class GMModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",          # unknown keys are a contract violation, not a shrug
        frozen=True,             # domain objects are immutable; transform via .model_copy()
        strict=True,             # no "1" → 1 coercion
        validate_default=True,
    )
```
  (`extra="forbid"` matters most on LLM structured output: a hallucinated field should raise, not
  vanish silently.)
- Protocols over ABCs for pluggable infra (`VectorStore`, `GraphStore`, `LLMClient`). Structural
  typing keeps `guardmem-core` free of driver imports.

### 2.2 Async-First
- All I/O is `async`. Sync I/O inside an async function fails review; CPU-bound work (embedding
  math, NLI on CPU) goes through `anyio.to_thread.run_sync` or a dedicated process pool.
- One `httpx.AsyncClient` per provider, created in lifespan, never per-request.
- Concurrency uses `asyncio.TaskGroup` — no bare `create_task` without a supervising group
  (orphaned tasks swallow exceptions, which is exactly the failure mode we can't afford).
- Every outbound call has an explicit timeout. No timeout = CI failure (`GM002`).
- Fan-out over candidates is bounded by a semaphore sized from settings, not unbounded `gather`.

```python
async def score_batch(cands: Sequence[MemoryCandidate]) -> list[ScoredCandidate]:
    sem = asyncio.Semaphore(settings.max_concurrent_scores)
    async def one(c: MemoryCandidate) -> ScoredCandidate:
        async with sem:
            return await score(c)
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(one(c)) for c in cands]
    return [t.result() for t in tasks]
```

### 2.3 Error Boundaries
Single exception hierarchy; each maps to an HTTP status and an MCP error code exactly once, in one
table. Handlers never invent status codes.

```python
class GuardMemError(Exception):
    code: ClassVar[str]
    http_status: ClassVar[int]
    retryable: ClassVar[bool] = False

class ValidationRejected(GuardMemError):   code="GM_VALIDATION";  http_status=422
class PolicyDenied(GuardMemError):         code="GM_POLICY";      http_status=403
class InjectionDetected(GuardMemError):    code="GM_INJECTION";   http_status=422
class BudgetExceeded(GuardMemError):       code="GM_BUDGET";      http_status=429
class ProviderUnavailable(GuardMemError):  code="GM_PROVIDER";    http_status=503; retryable=True
class StoreUnavailable(GuardMemError):     code="GM_STORE";       http_status=503; retryable=True
class ConcurrencyConflict(GuardMemError):  code="GM_CONFLICT";    http_status=409; retryable=True
```

Rules: catch narrowly and re-raise as a domain error with context; never catch `Exception` outside
the outermost boundary; every raise inside the pipeline attaches `trace_id` and `candidate_id`.
Retries only on `retryable=True`, with jittered exponential backoff and a hard attempt cap.

### 2.4 Structure & Naming
- Module ≤ 400 lines; function ≤ 50 lines; cyclomatic complexity ≤ 10 (ruff `C901`).
  **The function cap counts the body, not the docstring.** Both readings were in
  use until S2.2 made them contradict each other: §8 requires every public
  function to document what it returns *and what it raises*, and a function with
  seven parameters and five raise conditions cannot satisfy that inside 50 lines
  measured from `def`. The proof that the body reading is the intended one is in
  this repository — `llm/base.py::complete` sits at exactly 50 lines measured
  from `def` while its body is the single token `...`, so the strict reading
  makes the rule a docstring-length limit rather than a complexity signal, which
  is what it sits beside `C901` to be. Module length is measured plainly, in
  lines, because a long file is a navigation cost whatever is in it.
  Both are enforced by `tests/unit/test_source_limits.py`, not by convention —
  §0 of this document says a rule nothing checks should be deleted from it.
- Dependency direction is enforced by `import-linter` contracts (see `PROJECT_TREE.md` §Ownership).
- No global mutable state. Settings come from a single `pydantic-settings` object, injected.
- No business logic in routers. A router validates, calls one core function, and shapes the response.
- Feature flags are typed settings fields with a documented removal date, not `os.getenv` strings.

---

## 3. Prompt & Model Engineering

- Prompts live in versioned files (`prompts/<name>/v<N>.md`) with frontmatter: model tier, expected
  schema, changelog. Prompts are never f-string-assembled inline in business logic.
- A prompt change is a semver-minor change to the package and triggers the nightly eval gate.
- All extraction uses **structured output / tool-calling**, never regex over free text.
- Untrusted content is delimited and spotlighted; the system prompt states explicitly that content
  inside the delimiters is data, never instructions. Canary tokens are inserted and checked on
  output — a canary appearing in the model's output is a confirmed injection.
- Model identifiers are pinned in settings (`extraction.fast = "claude-haiku-4-5"`), never floating
  aliases, so replay is honest. Pin the exact published id and nothing more: current Claude ids are
  complete as written (`claude-haiku-4-5`, `claude-sonnet-5`, `claude-opus-5`) and a date suffix
  appended to one is not a valid model id.
- Every LLM call records: model, prompt version, temperature, seed (if supported), token counts,
  cache hit, latency, and cost estimate.

---

## 4. Security Protocols

- Threat model reviewed per release; memory poisoning and indirect prompt injection are the two
  named primary threats.
- Untrusted source hierarchy: `TRUSTED_SYSTEM > VERIFIED_USER > UNVERIFIED_USER > TOOL_OUTPUT >
  RETRIEVED_WEB`. Trust tier caps the maximum auto-writable impact level — retrieved web content can
  never auto-write a HIGH-impact predicate regardless of confidence.
- Secrets: Secret Manager/Vault only. `gitleaks` + `detect-secrets` in pre-commit and CI.
- SQL is parameterized; Cypher is parameterized. String-built queries fail `semgrep`.
- Dependencies: `pip-audit` + `npm audit` in CI; Dependabot weekly; images scanned with `trivy`;
  releases signed with `cosign` and ship an SBOM.
- Tenant isolation is defense-in-depth: Postgres RLS + namespace prefixing + app-layer check. A test
  suite (`tests/security/test_tenant_isolation.py`) attempts cross-tenant reads through every public
  surface (REST, MCP, SDK) and must fail all of them.
- Reviewer actions require re-authentication for `HIGH` impact approvals (step-up auth).

---

## 5. Testing Standards

| Suite | Scope | Gate |
|---|---|---|
| `unit` | pure logic, no I/O, fakes only | **≥ 90%** on `guardmem-core`, ≥ 85% repo-wide |
| `integration` | testcontainers: PG+pgvector, Neo4j, Redis | all green, no skips on main |
| `contract` | schemathesis over OpenAPI + MCP tool JSON-Schemas | no schema drift |
| `property` | hypothesis on pipeline invariants | must hold for 500 examples |
| `security` | injection/poisoning corpus | attack success rate **0%** into primary ns |
| `e2e` | Playwright on dashboard + review flow | critical paths green |
| `eval` (nightly) | quality suites vs pinned baseline | no metric regresses > 2% absolute |

**Coverage gate (release):** repo ≥ 85%, `guardmem-core` ≥ 90%. During Week 1 the interim floor for
`guardmem-core` is 85% (`fail_under = 85` in the root `pyproject.toml`); it rises to 90% before the
Phase 1 exit gate is signed off. Where the notebook or roadmap says 85% for a Week-1 step, that is
the interim floor, not a relaxation of this gate. More important than either number —
`pipeline/`, `guardrails/`, and `memory/router.py` require 100% branch coverage on decision
branches. Coverage is a floor, not a goal; a PR that raises coverage while lowering mutation score
(`mutmut` sample on core) gets rejected.

**Invariants that must always hold** (property-tested):
```
I1  every AUTO_WRITE assertion has a non-null source_span
I2  no two visible assertions share (subject, predicate) when cardinality == ONE
I3  supersession is acyclic
I4  decide() is deterministic and total over the (C, R, policy) domain
I5  audit chain verifies: digest_n == sha256(payload_n ‖ digest_{n-1})
I6  a tombstoned assertion never appears in retrieval results
I7  tokenized PII never appears in any span attribute or log line
```

**Test hygiene:** no network in unit tests (blocked by fixture); no `sleep` (use fake clocks);
LLM calls mocked with recorded fixtures in unit, live only in the nightly eval job; every bug fix
ships with the regression test in the same PR.

---

## 6. Observability Requirements (code-level)

- Structured logging only (`structlog`, JSON). Every log line carries `trace_id`, `tenant_id`,
  `namespace`. No f-string message interpolation of user content.
- Span names follow `guardmem.<layer>.<op>`. New spans need an entry in `observability/SPANS.md`.
- Every decision emits `guardmem_decisions_total{decision,namespace,tier}` and
  `guardmem_confidence_bucket`. Dashboards depend on these names — renaming a metric is a breaking
  change and needs a deprecation window.
- SLO burn alerts wired to the latency targets in `PRD.md` §6.1, not to arbitrary thresholds.

---

## 7. Git, Review & Release

- Trunk-based. Short-lived branches. Conventional Commits (`feat:`, `fix:`, `perf:`, `sec:`).
- PRs ≤ 400 changed lines where humanly possible; large mechanical refactors separated from behavior
  changes.
- CODEOWNERS gates: `guardrails/`, `pipeline/l3_score/`, `migrations/` require a second reviewer.
- Migrations are forward-only, backward-compatible for one release (expand → migrate → contract).
- Every PR touching decision logic must state the expected effect on HITL volume and attach the eval
  delta from CI. "Shouldn't change anything" is an answer that requires the eval run to back it.
- Release checklist: eval suite green, security scan clean, migration dry-run on staging clone,
  rollback plan in the PR description, SBOM attached.

---

## 8. Documentation Rules

- Every public function in `guardmem-core` has a docstring stating what it returns *and what it
  raises*.
- ADRs for anything that would be expensive to reverse: store choice, scoring formula changes,
  API surface changes, licensing.
- `MEMORY_ENGINE.md` is the spec of record for scoring. If the code and that doc disagree, the code
  is wrong until an ADR says otherwise.
- Runbooks are tested: each one is walked through during a quarterly game day.
