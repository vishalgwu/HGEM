"""The three-layer decision engine.  MEMORY_ENGINE.md

`ARCHITECTURE.md` §2.2: this runs identically in the Gateway (strict mode), the
Worker (async mode) and the eval harness, which is why it has no framework
dependencies. Side effects are confined to the injected `LLMClient`,
`VectorStore` and `GraphStore` protocols.

Layer 1 (`l1_extract`) arrives at S2.1-S2.3; Layer 2 at S4.1-S4.4; Layer 3 at
S5.1-S5.4; `orchestrator.py`, the single entrypoint, at S5.6.
"""
