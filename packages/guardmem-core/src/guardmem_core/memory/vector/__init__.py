"""Vector storage.  BUILD_NOTEBOOK.md S1.7

The `VectorStore` protocol, and from S3.2 the pgvector implementation of it:
`pool.py` opens the process-wide connection pool, `rowmap.py` holds the shape of
the `assertion` and `provenance` tables in both directions, and
`pgvector_store.py` is the store itself. A Qdrant backend only if `PRD.md`
FR-4.1's 10M-vector threshold is ever reached.
"""
