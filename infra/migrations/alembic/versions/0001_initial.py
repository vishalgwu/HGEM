"""Initial schema: bitemporal assertions, provenance, audit chain, outbox, RLS.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-13

`BUILD_NOTEBOOK.md` S3.1, built from `ARCHITECTURE.md` §5. Its WHY is the whole
point of doing this now rather than later: "bitemporal columns and RLS are
painful to retrofit."

Written as SQL rather than as `op.create_table` calls. Three of the things this
migration has to express - row-level security policies, role grants, and a
deferred constraint trigger - have no SQLAlchemy DDL representation, and a file
that was half schema objects and half `op.execute` would be harder to read
against `ARCHITECTURE.md` §5 than one that is uniformly the SQL that section
already contains.

**Provenance is its own table, and `ARCHITECTURE.md` §5 is updated to match.**
That section sketches `source_hash` and `source_span` as columns on `assertion`,
but `schemas/entity.py` carries `provenance: list[Provenance]` with `min_length=1`
and says in as many words that S3.1 decides how it is stored. It has to be a
list: `MEMORY_ENGINE.md` §2.4 resolves a duplicate by appending the new
`Provenance` and bumping `corroboration_count`, and §3.2's `S_cor` term is a
function of how many independent sources a fact has. A single pair of columns
cannot represent a corroborated fact at all, so S3.2 would have had to split the
table one step later - which is exactly the retrofit this step exists to avoid.
ADR-0007 added `alignment` to that record, so it is a column here too.

**Splitting it costs the `NOT NULL` that `RULES.md` §1.1 relies on**, and that
is paid back rather than dropped. "Any code path that persists an assertion
without `source_hash` + `source_span` is a P0 bug. Enforced by a DB `NOT NULL`
constraint *and* a property test." With provenance in its own table there is no
column to mark `NOT NULL`, so the invariant becomes "every assertion has at
least one provenance row", enforced by a **deferred** constraint trigger that
fires at COMMIT. Deferred because the assertion and its provenance are inserted
in the same transaction, which the outbox pattern (§2.4) already requires.

Forward-only in production (`RULES.md` §7), but `downgrade` is implemented: a
migration you cannot reverse locally is a migration you cannot test, and
`upgrade -> downgrade -> upgrade` is the cheapest proof that the DDL is
internally consistent.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The role the application connects as. Created by the deployment, never by a
# migration - see `infra/docker/initdb/02-app-role.sql`.
APP_ROLE = "guardmem_app"

# Tenant-scoped tables get row-level security. `ARCHITECTURE.md` §5 shows it on
# `assertion` alone; `RULES.md` §4 calls tenant isolation "defense-in-depth",
# and a policy on one table out of five is not depth.
TENANT_SCOPED = ("entity", "assertion", "audit_event", "review_task", "policy_version")


def upgrade() -> None:
    """Create the initial schema."""
    _extensions()
    _tables()
    _indexes()
    _provenance_invariant()
    _row_level_security()
    _grants()


def downgrade() -> None:
    """Drop everything this migration created, children first."""
    op.execute(f"DROP TRIGGER IF EXISTS assertion_requires_provenance ON {'assertion'}")
    op.execute("DROP FUNCTION IF EXISTS assert_provenance_exists()")
    for table in ("outbox", "provenance", "review_task", "policy_version", "audit_event"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TABLE IF EXISTS assertion CASCADE")
    op.execute("DROP TABLE IF EXISTS entity CASCADE")
    op.execute("DROP TABLE IF EXISTS tenant CASCADE")


def _extensions() -> None:
    """Extensions the schema depends on.

    Also created by `infra/docker/initdb/01-extensions.sql` on a fresh dev
    volume. Repeated here because a managed Postgres (Cloud SQL, S26.2) has no
    initdb hook, and `IF NOT EXISTS` makes running both harmless.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def _tables() -> None:
    """The seven tables S3.1 names, plus `provenance` - see the module docstring.

    Grouped by what they are for rather than written as one sequence: who the
    rows belong to, what the system believes, and what it is doing about it.
    """
    _tenancy_tables()
    _belief_tables()
    _operational_tables()


def _tenancy_tables() -> None:
    """Who a row belongs to, and what it is about."""
    op.execute("""
        CREATE TABLE tenant (
            id          UUID PRIMARY KEY,
            slug        TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE entity (
            id              UUID PRIMARY KEY,
            tenant_id       UUID NOT NULL REFERENCES tenant(id),
            type            TEXT NOT NULL,
            canonical_name  TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def _belief_tables() -> None:
    """What the system believes, and the citations it believes it from."""
    # ARCHITECTURE.md 5, with the bitemporal pair the ADR-0002 tombstone model
    # rests on: valid_from/valid_to is world time, recorded_at/retracted_at is
    # system time. `visible` is set true only after the dual write lands (2.4),
    # so a partial write is never retrievable rather than briefly wrong.
    op.execute("""
        CREATE TABLE assertion (
            id                   UUID PRIMARY KEY,
            tenant_id            UUID NOT NULL REFERENCES tenant(id),
            namespace            TEXT NOT NULL,
            subject_id           UUID NOT NULL REFERENCES entity(id),
            predicate            TEXT NOT NULL,
            object_json          JSONB NOT NULL,
            confidence           REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            risk                 REAL NOT NULL CHECK (risk BETWEEN 0 AND 1),
            valid_from           TIMESTAMPTZ NOT NULL,
            valid_to             TIMESTAMPTZ,
            recorded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            retracted_at         TIMESTAMPTZ,
            superseded_by        UUID REFERENCES assertion(id),
            corroboration_count  INTEGER NOT NULL DEFAULT 1
                                 CHECK (corroboration_count >= 1),
            trace_id             TEXT NOT NULL,
            visible              BOOLEAN NOT NULL DEFAULT false,
            embedding            VECTOR(1024),
            CONSTRAINT assertion_validity_not_inverted
                CHECK (valid_to IS NULL OR valid_to >= valid_from),
            CONSTRAINT assertion_not_self_superseding
                CHECK (superseded_by IS NULL OR superseded_by <> id)
        )
    """)

    # One row per citation. `source_span` is INT4RANGE because the spans are
    # half-open [start, end) - which is what `Provenance` validates and what
    # `span_linker` returns. `alignment` is ADR-0007's: 1.0 on an exact match,
    # below it when the model paraphrased its own citation.
    op.execute("""
        CREATE TABLE provenance (
            id            UUID PRIMARY KEY,
            assertion_id  UUID NOT NULL REFERENCES assertion(id) ON DELETE CASCADE,
            source_hash   TEXT NOT NULL,
            source_span   INT4RANGE NOT NULL,
            source_tier   TEXT NOT NULL,
            verbatim      TEXT NOT NULL CHECK (length(verbatim) <= 2000),
            alignment     REAL NOT NULL DEFAULT 1.0 CHECK (alignment BETWEEN 0 AND 1),
            captured_at   TIMESTAMPTZ NOT NULL,
            -- `NOT isempty(...)` is load-bearing and was missing in the first
            -- draft of this migration. `int4range(5, 5)` is an EMPTY range, and
            -- `lower()`/`upper()` return NULL on one - so the comparisons below
            -- evaluate to NULL, and a CHECK that evaluates to NULL *passes*.
            -- A zero-width span, which `Provenance` refuses in Python and
            -- `RULES.md` §1.1 treats as no span at all, was being stored.
            CONSTRAINT provenance_span_non_empty
                CHECK (
                    NOT isempty(source_span)
                    AND lower(source_span) >= 0
                    AND upper(source_span) > lower(source_span)
                )
        )
    """)


def _operational_tables() -> None:
    """The audit chain, the dual-write outbox, the review queue, the policy registry."""
    # Append-only, hash-chained. `prev_digest`/`digest` are BYTEA per
    # ARCHITECTURE.md 5; invariant I5 is digest_n == sha256(payload_n || digest_{n-1}).
    op.execute("""
        CREATE TABLE audit_event (
            seq          BIGSERIAL PRIMARY KEY,
            tenant_id    UUID NOT NULL REFERENCES tenant(id),
            trace_id     TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK (kind IN (
                             'DECISION', 'WRITE', 'REVIEW',
                             'POLICY_CHANGE', 'QUARANTINE', 'SUPERSEDE')),
            payload      JSONB NOT NULL,
            prev_digest  BYTEA NOT NULL,
            digest       BYTEA NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # S3.3's dual-write coordination. The assertion row and the outbox row
    # commit together; the relay applies the graph side and flips `visible`.
    op.execute("""
        CREATE TABLE outbox (
            id             UUID PRIMARY KEY,
            assertion_id   UUID NOT NULL REFERENCES assertion(id),
            event          JSONB NOT NULL,
            attempts       INTEGER NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            dispatched_at  TIMESTAMPTZ
        )
    """)

    # Shaped by `schemas/review.py`. The candidate is referenced by id rather
    # than embedded, as that module explains: (trace_id, candidate_id) already
    # identifies the decision.
    op.execute("""
        CREATE TABLE review_task (
            id            UUID PRIMARY KEY,
            tenant_id     UUID NOT NULL REFERENCES tenant(id),
            trace_id      TEXT NOT NULL,
            candidate_id  TEXT NOT NULL,
            namespace     TEXT NOT NULL,
            impact_level  TEXT NOT NULL
                          CHECK (impact_level IN ('low', 'medium', 'high', 'critical')),
            priority      DOUBLE PRECISION NOT NULL CHECK (priority >= 0),
            status        TEXT NOT NULL
                          CHECK (status IN ('pending', 'claimed', 'decided', 'overdue')),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            sla_due_at    TIMESTAMPTZ NOT NULL,
            assigned_to   TEXT,
            leased_until  TIMESTAMPTZ,
            CONSTRAINT review_task_trace_candidate_unique UNIQUE (trace_id, candidate_id)
        )
    """)

    # Deliberately minimal. `PolicyPack` and `Rule` have no specified fields
    # until S12.2 decides them against a working engine (`schemas/policy.py`),
    # so this records only what `DecisionRecord.policy_version` needs today: a
    # registry of versions a decision can cite. S12.2 adds the body.
    op.execute("""
        CREATE TABLE policy_version (
            id          UUID PRIMARY KEY,
            tenant_id   UUID NOT NULL REFERENCES tenant(id),
            name        TEXT NOT NULL,
            version     TEXT NOT NULL,
            active      BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT policy_version_unique UNIQUE (tenant_id, name, version)
        )
    """)


def _indexes() -> None:
    """The two S3.1 names, plus the ones the read and relay paths need."""
    # Incumbent lookup for MEMORY_ENGINE.md 2.2: top-k within
    # (namespace, subject, predicate), live rows only.
    op.execute("""
        CREATE INDEX assertion_live_idx
            ON assertion (tenant_id, namespace, subject_id, predicate)
            WHERE valid_to IS NULL AND visible
    """)
    # Dense half of hybrid retrieval. Partial for the same reason: a superseded
    # or not-yet-visible row must never be a nearest neighbour (invariant I6).
    op.execute("""
        CREATE INDEX assertion_hnsw
            ON assertion USING hnsw (embedding vector_cosine_ops)
            WHERE valid_to IS NULL AND visible
    """)
    op.execute("CREATE INDEX provenance_assertion_idx ON provenance (assertion_id)")
    # "Which assertions cite this document?" - the first query a poisoning
    # incident asks (`runbooks/incident-memory-poisoning.md`).
    op.execute("CREATE INDEX provenance_source_hash_idx ON provenance (source_hash)")
    op.execute("CREATE INDEX audit_event_trace_idx ON audit_event (tenant_id, trace_id)")
    # The relay claims undispatched rows; a partial index keeps it O(pending).
    op.execute("CREATE INDEX outbox_pending_idx ON outbox (created_at) WHERE dispatched_at IS NULL")
    op.execute("""
        CREATE INDEX review_task_queue_idx
            ON review_task (tenant_id, status, priority DESC)
            WHERE status IN ('pending', 'overdue')
    """)


def _provenance_invariant() -> None:
    """Every assertion has at least one provenance row, checked at COMMIT.

    This is `RULES.md` §1.1 - "no unsourced write" - kept at the database level
    after provenance moved to its own table. A plain trigger would fire on
    INSERT, before the provenance rows exist; a DEFERRABLE INITIALLY DEFERRED
    constraint trigger fires at COMMIT, by which time the transaction that wrote
    the assertion has written its citations too.
    """
    op.execute("""
        CREATE FUNCTION assert_provenance_exists() RETURNS TRIGGER AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM provenance WHERE assertion_id = NEW.id) THEN
                RAISE EXCEPTION
                    'assertion % has no provenance; RULES.md 1.1 forbids an unsourced write',
                    NEW.id;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER assertion_requires_provenance
            AFTER INSERT ON assertion
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_provenance_exists()
    """)


def _row_level_security() -> None:
    """Tenant isolation, failing closed when no tenant is set.

    `NULLIF(current_setting('app.tenant_id', true), '')::uuid`, and all three
    parts earn their place. `missing_ok` (the `true`) stops an unset variable
    raising, which would turn a missing `SET LOCAL` into a 500 rather than into
    zero rows. `NULLIF` handles the case `missing_ok` does not: once a session
    has set the variable and then `RESET` it, `current_setting` returns the
    **empty string** rather than NULL, and `''::uuid` raises `invalid input
    syntax`. Measured, not assumed - the first version of this policy did
    exactly that. Both now collapse to NULL, `tenant_id = NULL` is never true,
    and the query returns nothing. Fail closed, per `ARCHITECTURE.md` §0.

    **A malformed tenant id still raises, and that is deliberate.**
    `SET app.tenant_id = 'not-a-uuid'` is a bug in tenant propagation, not a
    session that has not set one yet, and the two deserve different answers.
    Widening the `NULLIF` to swallow anything uncastable would turn that bug
    into an empty result set - which, in a product whose whole subject is
    remembered facts, reads as "this patient has no memories" rather than as
    "something is broken". Unset is silence; malformed is an error.

    `FORCE` matters: a table's owner is exempt from its own policies, and the
    migration runs as the owner. Without `FORCE` the owner would keep seeing
    every tenant's rows, which is precisely the check somebody would run to
    convince themselves isolation works.
    """
    for table in TENANT_SCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (
                    tenant_id
                    = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                )
        """)
    # Provenance has no tenant column of its own; it inherits isolation from the
    # assertion it belongs to, which RLS already protects.
    op.execute("ALTER TABLE provenance ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE provenance FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON provenance
            USING (EXISTS (SELECT 1 FROM assertion a WHERE a.id = provenance.assertion_id))
    """)


def _grants() -> None:
    """What the application role may do - and the two things it may not.

    `RULES.md` non-negotiable #2: "`DELETE` on `assertion` is revoked at the role
    level. Retirement is `valid_to = now()` + `superseded_by`." And #4 makes the
    audit log append-only, which means no UPDATE and no DELETE either: a chain
    whose links can be edited verifies nothing.

    Raises loudly if the role is absent rather than skipping the revoke. A
    migration that silently does not apply a P0 safety property is worse than
    one that fails, because the failure is the only signal anybody gets.
    """
    # ruff S608 flags this as string-built SQL, and `RULES.md` §4 does require
    # parameterised SQL - but that rule is about *values*, and Postgres cannot
    # bind an identifier. A role name in GRANT, REVOKE or a catalogue lookup has
    # to be interpolated by something. What makes it safe here is the source:
    # `APP_ROLE` is a module constant in a migration, never a request, a setting
    # or a column. Suppressed once, deliberately, rather than repeating the
    # literal a dozen times below - which would silence the rule while making
    # the name harder to change.
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                RAISE EXCEPTION
                    'role {APP_ROLE} does not exist. RULES.md non-negotiable #2 revokes '
                    'DELETE from it, so the schema cannot be created without it. '
                    'Dev: make dev-reset (runs infra/docker/initdb). '
                    'Prod: provision the role before migrating.';
            END IF;
        END
        $$
    """)  # noqa: S608

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON assertion TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON provenance TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_event TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE audit_event_seq_seq TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON outbox TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON review_task TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON entity TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON tenant TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON policy_version TO {APP_ROLE}")

    # The two refusals, stated explicitly rather than left implicit in the
    # grants above - a future GRANT ALL would otherwise undo them silently.
    op.execute(f"REVOKE DELETE ON assertion FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON audit_event FROM {APP_ROLE}")
    # Provenance is the evidence for an assertion; retiring the assertion keeps
    # it, so there is no legitimate delete path here either.
    op.execute(f"REVOKE UPDATE, DELETE ON provenance FROM {APP_ROLE}")
