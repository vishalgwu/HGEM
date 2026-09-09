# GuardMem AI - Project Documentation

GuardMem AI is a planned memory governance gateway for long-running AI agents. It
extracts sourced facts, checks conflicts, scores confidence and impact, and chooses
automatic storage, human review, rejection, or one additional model evaluation.
Every durable decision must have an auditable receipt.

**Status: documentation only, reset on September 9, 2026.** The previous application
scaffold, configuration, workflows, and local assistant settings have been removed.
Implementation has not restarted. The repository contains only `docs/`; the local
checkout also retains `.git/` for version control.

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
| [Architecture decisions](adr/) | Rationale for dual stores, bitemporal history, async evaluation, entropy, and MCP |
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
