"""Graph storage.  BUILD_NOTEBOOK.md S1.7, S3.4, S7.1

The `GraphStore` protocol (`base.py`) and, since S7.1, both of its backends.
`GM_GRAPH_BACKEND` chooses; `selection.build_graph` is the only thing that reads
the flag, and nothing above it names a concrete class.

| Module | |
|---|---|
| `base.py` | the protocol - three methods, two of which serve scoring rather than storage |
| `networkx_store.py` | S3.4. In-process, volatile, **single-tenant and enforced** |
| `neo4j_store.py` | S7.1. Durable, **multi-tenant and scoped** |
| `cypher.py` | the statements the Neo4j backend sends, and the schema it needs |
| `keys.py` | how an object becomes a node key - the one thing both must compute alike |
| `selection.py` | the flag, as an async context manager over the driver's lifetime |

**The two backends answer the tenancy question from opposite directions, and
that is the thing to understand before reading either.** The protocol's reads
take an `EntityId` and no tenant, so a store holding two tenants' subgraphs has
nothing to filter `degree()` on. NetworkX refuses to hold a second tenant.
Neo4j - which exists because that refusal is not an answer for a deployment -
filters instead, resolving the tenant from the node a read starts at.
`tests/fixtures/graph_contract.py` holds the sixteen behaviours they share; each
backend's own module holds the ones only it can be asked about.

**Nothing in `pipeline/` may import a concrete store from here.** S3.4's DONE
WHEN says so and the `import-linter` contract in `pyproject.toml` enforces it,
which is what makes `ARCHITECTURE.md` §2.4's "the backend is an operator
decision" true rather than aspirational.
"""
