"""Vector storage.  BUILD_NOTEBOOK.md S1.7

The `VectorStore` and `Embedder` protocols, and from S3.2 the pgvector
implementation of the first: `pool.py` opens the process-wide connection pool
and owns the transaction helpers everything on it runs in, `rowmap.py` holds the
shape of the `assertion` and `provenance` tables in both directions, and
`pgvector_store.py` is the store itself. A Qdrant backend only if `PRD.md`
FR-4.1's 10M-vector threshold is ever reached.

`hash_embedder.py` is the only `Embedder` the package ships until S9.1 wires a
provider. It is deterministic and offline by design - and explicitly not a
model, which its docstring is at pains about, because the seed, the dev stack
and the unit suite all run on it and a number measured against its vectors
would be a number about SHA-256.
"""
