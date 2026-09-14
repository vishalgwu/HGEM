"""The three-layer decision engine.  MEMORY_ENGINE.md

`ARCHITECTURE.md` §2.2: this runs identically in the Gateway (strict mode), the
Worker (async mode) and the eval harness, which is why it has no framework
dependencies. Side effects are confined to the injected `LLMClient`,
`VectorStore` and `GraphStore` protocols.

Layer 1 (`l1_extract`) arrived at S2.1-S2.3 and Layer 2 (`l2_validate`) starts
at S4.1 with the schema gate. The rest of Layer 2 is S4.2-S4.4; Layer 3 is
S5.1-S5.4; `orchestrator.py`, the single entrypoint, is S5.6 - until it exists,
nothing joins the stages and each is called directly.
"""
