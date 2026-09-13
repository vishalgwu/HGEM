# GuardMem AI - Project Documentation

GuardMem AI is a planned memory governance gateway for long-running AI agents. It
extracts sourced facts, checks conflicts, scores confidence and impact, and chooses
automatic storage, human review, rejection, or one additional model evaluation.
Every durable decision must have an auditable receipt.

**Status: the build has started.** The repository was reset to a documentation
baseline on September 9, 2026. `BUILD_NOTEBOOK.md` Day 1 is complete
(S1.1 - S1.7) and Layer 1 is built (S2.1 - S2.2). The toolchain and the gates are
real - `make lint`, `make typecheck`, `make test`, the pre-commit hooks and
GitHub Actions all run today - and so is the typed foundation of `guardmem_core`:
settings, domain ids, the error hierarchy, the Pydantic schema layer, the store
and LLM protocols with in-memory fakes, the versioned prompt loader, and Layer
1's noise filter and K-sample extractor. Nothing downstream exists: nothing
validates, scores or decides. The next step is S2.3, the span linker's fuzzy
fallback.

Everything in this directory remains specification. Nothing here is evidence of
an implemented feature; see "How to use this baseline" below.

## Start here

1. Read [PRD](PRD.md) for the problem, scope, and acceptance targets.
2. Read [Architecture](ARCHITECTURE.md) and [Memory Engine](MEMORY_ENGINE.md) for the
   data flow, schemas, scoring, persistence, and retrieval requirements.
3. Read [Engineering Rules](RULES.md) for the seven invariants and testing gates.
4. Use [Build Notebook](BUILD_NOTEBOOK.md) for the build steps and
   [Phases and Roadmap](PHASES_AND_ROADMAP.md) for the milestone overview. The Day 5 section
   ends at Checkpoint B, the discrimination gate that decides whether the project is viable.

## Protected master notebook

[GuardMem_AI_Master_Build_Notebook.pdf](GuardMem_AI_Master_Build_Notebook.pdf) is the
original 30-page master notebook. **Never delete, rename, replace, or modify it.**
The cleanup preserved it byte for byte. Make future corrections in the Markdown
companion, [BUILD_NOTEBOOK.md](BUILD_NOTEBOOK.md).

Original SHA-256:

```text
D4E49FEFFB9FA1A5325D1E9AE9AF530604F871E9EA8327998EFE4902161357CD
```

## Retained project references

| Document | Why it is needed |
|---|---|
| [PRD.md](PRD.md) | Product requirements, intended users, scope, and measurable targets |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Component responsibilities, write/read paths, data model, and failure handling |
| [MEMORY_ENGINE.md](MEMORY_ENGINE.md) | Specification of record for extraction, validation, scoring, decisions, and compaction |
| [RULES.md](RULES.md) | Engineering standards, security requirements, and invariant tests |
| [BUILD_NOTEBOOK.md](BUILD_NOTEBOOK.md) | Detailed implementation sequence, including the consolidated Day 5 scoring checkpoint |
| [PHASES_AND_ROADMAP.md](PHASES_AND_ROADMAP.md) | Four-phase delivery plan and exit gates |
| [PROJECT_TREE.md](PROJECT_TREE.md) | Planned repository layout to build incrementally |
| [MCP_INTEGRATION.md](MCP_INTEGRATION.md) | Planned agent tools, resources, prompts, and authentication contracts |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Dashboard and human review interface requirements |
| [Architecture decisions](adr/) | Rationale for dual stores, bitemporal history, async evaluation, entropy, MCP, and the S2.2 amendment to `ExtractionResult` |
| [Incident runbooks](runbooks/) | Draft response procedures for poisoning, review backlogs, and provider outages |

The diagrams illustrate distinct parts of this design:

| Diagram | Related specification |
|---|---|
| [System overview](diagrams/guardmem_system_overview_layers.png) | Architecture |
| [Memory write path](diagrams/guardmem_memory_write_path_decision_flow.png) | Architecture and Memory Engine |
| [Three-layer pipeline](diagrams/guardmem_three_layer_pipeline_internals.png) | Memory Engine |
| [Confidence and risk matrix](diagrams/guardmem_confidence_risk_decision_matrix.png) | Memory Engine section 3.4 |
| [Outbox coordination](diagrams/guardmem_dual_store_outbox_write_coordination.png) | Architecture section 2.4 |
| [Retrieval and context packing](diagrams/guardmem_read_path_retrieval_and_packing.png) | Architecture read path and Memory Engine section 4 |
| [Memory decay and compaction](diagrams/guardmem_memory_decay_tiers_compaction.png) | Memory Engine section 4 |
| [Review and tuning loop](diagrams/guardmem_hitl_review_tuning_feedback_loop.png) | Design System and Build Notebook Day 20 |
| [Build roadmap](diagrams/guardmem_build_roadmap_stages_and_checkpoints.png) | Phases and Roadmap and Build Notebook |

## Which document owns what

Every fact below has exactly **one** home. Change it there; everywhere else cites it. This table
exists because the first review of this suite found the same number stated three different ways in
three documents — that is how a spec rots.

| Decision | Owner | Everyone else |
|---|---|---|
| Scoring math, schemas, thresholds, the decision matrix | [MEMORY_ENGINE.md](MEMORY_ENGINE.md) | cites it; code that disagrees is wrong until an ADR moves it |
| Product scope, personas, SLAs, quality targets | [PRD.md](PRD.md) | cites §6.1 for latency and §6.2 for quality |
| Component boundaries, write/read paths, failure behavior | [ARCHITECTURE.md](ARCHITECTURE.md) | §1 diagrams simplify; MEMORY_ENGINE is normative for the decision box |
| Coding standards, invariants, testing gates, coverage | [RULES.md](RULES.md) | the notebook's Appendix D is a copy, not a second source |
| Agent-facing tool, resource and prompt contracts | [MCP_INTEGRATION.md](MCP_INTEGRATION.md) | the notebook copies schemas from it verbatim |
| Dashboard and review-queue interface requirements | [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | design targets live here; *acceptance gates* live in the PRD |
| Repository layout and dependency direction | [PROJECT_TREE.md](PROJECT_TREE.md) | a blueprint to build into, not files to pre-create |
| Phase goals, exit gates, deferred scope, risk review | [PHASES_AND_ROADMAP.md](PHASES_AND_ROADMAP.md) | the notebook's week gates point here |
| Step-by-step build work, per-step "done when", troubleshooting | [BUILD_NOTEBOOK.md](BUILD_NOTEBOOK.md) | the roadmap points here for steps |
| Rationale for a hard-to-reverse choice | [adr/](adr/) | an ADR is how any of the above legitimately changes |

Two numbers people reach for that are deliberately *not* duplicated: **median review time** is owned
by `PRD.md` §6.2 (≤ 25 s acceptance gate; `DESIGN_SYSTEM.md` §3.1 sets a tighter 18 s design
target, which is not a gate), and **coverage** is owned by `RULES.md` §5 (90% on `guardmem-core` at
release; 85% is the Week-1 interim floor).

## How to use this baseline

All runtime paths, setup commands, service URLs, package names, deployment examples,
CI gates, and runbook actions in these documents describe the intended build. They
are not evidence of implemented or deployed features. Performance and quality
numbers are targets until measured. Version examples are inherited from the design
and must be checked when their implementation step begins.

Keep using this HGEM repository. Verify the existing checkout and docs in notebook
steps S0.3-S0.4, then build the foundation in Day 1 when implementation begins.
Create only the files needed for the current step. The project tree is a blueprint,
not a requirement to recreate every empty file at once. Runbook commands require
implemented, tested endpoints before they can be used operationally.

`MEMORY_ENGINE.md` governs scoring and `RULES.md` governs engineering gates. Update
the relevant specification and record an ADR for substantive design changes.

## Cleanup record

- Removed all repository content outside `docs/`: the empty source scaffolds under
  `apps/`, `services/`, `packages/`, `tests/`, `evals/`, `bench/`, `infra/`, and
  `scripts/`; the GitHub workflows; every root configuration file, including
  `.gitignore`, `LICENSE`, `Makefile`, `pyproject.toml`, and the root `README.md`;
  and the local `.claude/` settings. No virtual environment or dependency cache was
  present in the inspected project directory.
- Replaced the old docs README, which described a missing `claude/` build kit.
- Removed `CLAUDE.md`. Its seven invariants are already stated verbatim in
  [RULES.md](RULES.md) section on invariants, its specification index duplicates the
  table above, and its remaining pointers, `BUILD_STATE.md` and `claude/stages/`,
  referred to files that never existed.
- Consolidated `STAGE_05_SCORING.md` and `CHECKPOINT_B_PIPELINE.md` into notebook Day 5
  rather than deleting them outright. Checkpoint B is now a full notebook section with
  its AUROC bands, the ordered failure diagnosis, the B1-B8 verification table, and the
  sign-off block. The minority-cluster drop moved into S5.1 and the corroboration ladder
  check values into S5.2. Both source files were then removed as duplicates.
- Removed `diagrams/.gitkeep` because the directory contains the nine project diagrams.
- Preserved the master PDF byte for byte, verified by SHA-256 before and after the
  cleanup, along with the editable notebook, project specifications, five ADRs, three
  draft runbooks, and all nine diagrams.

Nothing is lost. The cleanup does not rewrite history, so every removed file remains in
Git history and any one of them can be restored:

```bash
git checkout 74aa059 -- .gitignore LICENSE
```

## Consistency pass

A second pass read all nine documents, the five ADRs, the three runbooks and the master PDF against
each other and reconciled what they disagreed about. The PDF stays frozen and the Markdown is where
corrections go.

> **Correction (2026-09-10).** This section used to say the PDF and
> `BUILD_NOTEBOOK.md` "hold the same content". That is false, and was already
> false when written. The PDF is intact - its SHA-256 still matches byte for
> byte - but its *content* predates the reconciliation below. Measured against
> the extracted text: it contains zero occurrences of "Checkpoint" and zero of
> `tau_mid`, carries the invalid model id `claude-haiku-4-5-20251001` and the
> previous-generation `claude-sonnet-4-5` / `claude-opus-4-1`, and still says
> `gh repo create guardmem-ai`. Step coverage is otherwise identical - the same
> 96 steps S0.1 to S28.4 and the same appendices A-G - so the Markdown is a
> strict superset. **Build from the Markdown.**

Contradictions resolved:

- **The decision matrix had a threshold that did not exist.** `MEMORY_ENGINE.md` §3.4 splits
  confidence into four bands but named only `τ_lo` and `τ_hi`, leaving the 0.60 boundary as a magic
  number that `decide()` could not read from settings. Added `τ_mid = 0.60`, relabelled the matrix
  in threshold terms, stated the half-open band convention, and added `GM_TAU_MID` to the settings
  object and the notebook's environment-variable appendix.
- **Median review time was three different numbers** — 18 s in the design system, 20 s in the PRD,
  25 s in the roadmap and notebook. The PRD now owns it as a ≤ 25 s acceptance gate; the design
  system keeps 18 s explicitly as a design target rather than a gate.
- **Coverage read as 85% in four places and 90% in RULES.** RULES now states 90% as the release
  gate and 85% as the Week-1 interim floor, and the notebook and roadmap say which one they mean.
- **ADR-0004 contradicted the spec of record three ways:** it gave the default K as 5 (it is 3),
  described `ρ` as "the reject threshold" (`τ` gates confidence, `ρ` gates blast radius), and listed
  confidence terms that do not exist in the composite. All three corrected, and its dangling
  "(ADR pending)" reference now points at `ARCHITECTURE.md` §2.8.
- **Pinned model ids were stale and malformed.** `claude-sonnet-4-5` and `claude-opus-4-1` are
  previous-generation; the FAST tier carried a date suffix that is not part of a valid id. Now
  `claude-haiku-4-5`, `claude-sonnet-5`, `claude-opus-5`, with a RULES note that current ids are
  complete as written.
- **The notebook created a second repository.** S0.3 said `gh repo create guardmem-ai`; this project
  is HGEM and already exists. S0.3 now restores the `.gitignore` this cleanup removed and verifies
  the existing checkout, and S0.4 verifies the docs already in place instead of copying them in.
- **`PROJECT_TREE.md` had no home for things other documents require:** the versioned `prompts/`
  directory RULES §3 mandates, the ontology loader and starter packs, `observability/SPANS.md`,
  the outbox relay task, `evals/reports/`, `DAILY_LOG.md`, and the MCP tools for `timeline` and
  `policy.evaluate`. All added; its `docs/` listing, which named seven files, now matches reality.
- **ADR-0005 listed a subset of the MCP tool surface.** Aligned with `MCP_INTEGRATION.md` §2.
- **`PRD.md` FR-1.4 gave the default K as 5**, the same error as ADR-0004; the default is 3, and
  the full risk-hint ladder now appears there. A `DESIGN_SYSTEM.md` trace mock also labelled a
  K=5 extraction as FAST tier; K=5 runs on BALANCED.

Duplication removed:

- `PHASES_AND_ROADMAP.md` restated the notebook's day-by-day plan, so a schedule change needed two
  edits. It now owns phase goals, exit gates, deferred scope and the risk review, and points at the
  notebook for steps. The two criteria it held that the notebook lacked — a Lighthouse budget and
  the gateway contract suite — moved into S15.1 and S8.1 before the tables came out.
- The notebook's four week-gate checklists duplicated the phase exit gates. They now point at the
  roadmap, and the Week 4 gate, which was missing entirely, was added.
- The "Which document owns what" table above was added so the next contributor can tell where a
  change belongs without diffing nine files.

No document was deleted in this pass. The seven markdown files named on the master PDF's cover are
load-bearing references from a frozen artifact and are kept for that reason alone.
