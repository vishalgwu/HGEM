"""Graph storage.  BUILD_NOTEBOOK.md S1.7

The `GraphStore` protocol, and from S3.4 the in-process implementation of it:
`networkx_store.py` is a `MultiDiGraph` keyed by `assertion_id`, so L2 and the
risk scorer can call `degree()` without Docker. `PROJECT_TREE.md` calls it the
dev / single-tenant backend and that module enforces the second half of that
description. `neo4j_store.py` swaps in at S7.1 behind the same three methods.
"""
