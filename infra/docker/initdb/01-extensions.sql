-- Postgres first-boot initialisation.  BUILD_NOTEBOOK.md S1.3
--
-- The official Postgres entrypoint runs every .sql in /docker-entrypoint-initdb.d
-- exactly once, when the data directory is empty. `make dev` on a fresh volume
-- therefore lands with the extension already present, and re-running `make dev`
-- against an existing volume is a no-op.
--
-- S1.3 as written has you run this by hand afterwards:
--
--     psql postgresql://... -c "CREATE EXTENSION IF NOT EXISTS vector;"
--
-- A manual step that has to be remembered on every fresh clone is a step that
-- gets forgotten, and the symptom - `type "vector" does not exist` - is the top
-- entry in the notebook's own troubleshooting table. Doing it here means the
-- S1.3 DONE WHEN check passes straight after `make dev`.

-- pgvector: ARCHITECTURE.md 5 stores `assertion.embedding` as a VECTOR column
-- and indexes it with HNSW for the read path.
CREATE EXTENSION IF NOT EXISTS vector;

-- Deterministic UUIDs for seed data and tests (S3.6). gen_random_uuid() is
-- built in from Postgres 13, so pgcrypto is NOT needed for that alone - this is
-- here for the digest() used by the hash-chained audit log in S5.5.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Trigram matching backs the BM25-ish lexical half of hybrid retrieval
-- (ARCHITECTURE.md read path: dense + BM25 + graph expand).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
