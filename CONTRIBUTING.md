# Contributing to GuardMem AI

Thanks for your interest in contributing. This repo is pre-alpha and moving
fast — the guidance below is what we ask for now.

## Ground rules

- **Read the docs first.** Non-trivial changes should be consistent with
  `docs/ARCHITECTURE.md`, `docs/MEMORY_ENGINE.md`, and `docs/RULES.md`. If a
  change would violate them, propose an ADR under `docs/adr/` first.
- **Import direction is enforced.** `apps → services → packages → stores`.
  Never import a service from `guardmem-core`, and never let one service reach
  into another service's internals.
- **The audit log is the product.** Any change to the decision or write path
  must preserve the ability to replay a trace deterministically.
- **Fail closed.** Degradations may narrow the auto-write path; they may never
  widen it.

## Dev setup

```bash
cp .env.example .env
make dev        # boot local stack
make migrate
make seed
make test
```

## Style and tooling

- Python: `ruff`, `ruff-format`, `mypy --strict` on `guardmem-core`.
- TypeScript: strict mode; ESLint + Prettier; React Server Components by default.
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) — e.g.
  `feat(core): add semantic entropy scorer`.
- Every change adds or updates tests. Coverage floor for `guardmem-core` is
  the CI gate; we push it up over time, not down.

## PR expectations

- One logical change per PR. Refactors separate from behavior changes.
- If you change public API (SDKs, MCP tools, REST), update the corresponding
  contract tests and the docs in the same PR.
- Security-sensitive changes get a note in the PR body pointing at the threat
  model line they affect.

## Code of conduct

Be kind. Assume good intent. Debate the design, not the person.
