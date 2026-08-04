# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repository scaffold matching `docs/PROJECT_TREE.md`:
  - `packages/guardmem-core` with `schemas/`, `pipeline/{l1_extract,l2_validate,l3_score}`,
    `guardrails/`, `memory/{vector,graph,compaction}`, `llm/providers`, `observability/`,
    `hitl/` module layout.
  - `packages/guardmem-sdk-python` and `packages/guardmem-sdk-ts` package skeletons.
  - `services/{gateway,worker,mcp_server}` service skeletons.
  - `apps/dashboard` Next.js layout with control and review route groups.
  - `infra/{docker,terraform,k8s,migrations}` layout.
  - `evals/`, `bench/`, `tests/`, `scripts/`, and `.github/workflows/` scaffolding.
- Design docs moved into `docs/`: PRD, ARCHITECTURE, MEMORY_ENGINE, RULES,
  MCP_INTEGRATION, DESIGN_SYSTEM, PHASES_AND_ROADMAP, PROJECT_TREE, BUILD_NOTEBOOK,
  and the Master Build Notebook PDF.
- Root files: `README.md`, `LICENSE` (Apache-2.0 with dual-licensing note),
  `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.gitignore`, `.env.example`,
  `Makefile`, `pyproject.toml`, `pnpm-workspace.yaml`, `turbo.json`,
  `.pre-commit-config.yaml`, `.dockerignore`.
