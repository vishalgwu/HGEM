"""Operational scripts.  `make seed`, `scripts/replay_trace.py`

A package as of S5.6, and it was a bare directory until then. `PROJECT_TREE.md`
had it as loose files and mypy read each one as a top-level module, which was
harmless while nothing imported them - the open item said so, and said it was
"the same trap as the duplicate `conftest` if either tree grows a matching
basename".

What grew was a test. `tests/integration/test_replay_trace.py` drives
`scripts/replay_trace.py`'s `main` the way the command line does, and importing
it as `scripts.replay_trace` put the same file under two module names in mypy's
roots - `replay_trace` and `scripts.replay_trace` - which it refuses outright.
This `__init__.py` is mypy's own first suggested resolution. It has one cost,
paid in the Makefile: a *sibling* import now has to be qualified
(`scripts.demo_tenant_data`), which only resolves when the repo root is on
`sys.path` - so `make seed` runs `python -m scripts.seed_demo_tenant` rather
than the file by path. A script importing nothing from its siblings is
unaffected, which is why S5.6's own DONE WHEN command still reads
`python scripts/replay_trace.py <trace_id>` exactly as the notebook writes it.
"""
