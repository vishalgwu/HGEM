# ADR-0001: Dual store — vector + graph

**Status:** Accepted
**Date:** 2026-01-15

## Context

Agent memory has two access patterns that pull in opposite directions:

- **Semantic recall** — "what have we ever heard about penicillin allergies for
  this patient?" — is a nearest-neighbour problem over embeddings.
- **Structured reasoning** — "which of these facts contradict entity X's current
  1:1 `primary_dx` predicate?" — is a graph traversal problem.

A single-store design forces one of these to be slow or brittle.

## Decision

`guardmem-core` writes to **both a vector store and a graph store** for every
durable assertion. The router (`memory/router.py`) decides which store(s) a
given candidate targets based on predicate shape; retrieval
(`memory/retrieval.py`) does hybrid BM25 + dense + graph-expand.

The `VectorStore` and `GraphStore` protocols keep concrete backends
(pgvector/qdrant, neo4j/networkx) swappable.

## Consequences

- Two write paths → need an outbox pattern to keep them consistent.
- Contradiction detection uses graph structure; entity resolution uses vector
  similarity. Both are first-class.
- Backend choice becomes an operator decision, not an architecture decision.
