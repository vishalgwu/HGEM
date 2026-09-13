"""Storage: vector, graph, retrieval and compaction.  BUILD_NOTEBOOK.md S1.7

The two store protocols, and from S3.3 the write coordination between them:
`router.py` is the entry point a write path calls, `outbox.py` holds the shape
of the `outbox` table in both directions, and `relay.py` drains it - applying
the graph side and flipping `visible`, which nothing else in the system may do.

`retrieval.py` (S4.2) and `compaction/` (S14.1) arrive at their own steps.
"""
