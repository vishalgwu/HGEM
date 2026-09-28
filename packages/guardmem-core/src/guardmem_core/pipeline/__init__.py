"""The three-layer decision engine.  MEMORY_ENGINE.md

`ARCHITECTURE.md` §2.2: this runs identically in the Gateway (strict mode), the
Worker (async mode) and the eval harness, which is why it has no framework
dependencies. Side effects are confined to the injected protocols - the
`LLMClient`, `VectorStore` and `GraphStore`, and the `EntityResolver`, which
writes the entity row an assertion's foreign key needs.

Layer 1 (`l1_extract`) is S2.1-S2.3, Layer 2 (`l2_validate`) S4.1-S4.4 and
Layer 3 (`l3_score`) S5.1-S5.4. `orchestrator.run`, the single entrypoint, is
S5.6: it decides and writes nothing, and `per_candidate.py` holds Layers 2 and
3 for one candidate. `guardmem_core.governance.govern` is run-then-apply, and
it is what the services call.
"""
