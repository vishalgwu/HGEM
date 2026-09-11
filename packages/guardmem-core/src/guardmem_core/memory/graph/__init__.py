"""Graph storage.  BUILD_NOTEBOOK.md S1.7

The `GraphStore` protocol. `networkx_store.py` lands at S3.4 so L2 and the risk
scorer can call `degree()` without Docker; `neo4j_store.py` swaps in at S7.1.
"""
