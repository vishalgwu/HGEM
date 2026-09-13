-- The least-privilege application role.  BUILD_NOTEBOOK.md S3.1
--
-- `RULES.md` non-negotiable #2: "`DELETE` on `assertion` is revoked at the role
-- level." That sentence needs a role to revoke from, and it has to be a role
-- the application actually connects as - revoking from the table owner would do
-- nothing, because an owner is not subject to its own grants, and revoking from
-- a role nobody uses protects nothing.
--
-- **Roles are cluster state, not schema state, so this is not in the migration.**
-- `0001_initial` grants and revokes against this role, which is a property of
-- the `assertion` table and belongs with it; creating the role is a property of
-- the deployment. In production it is provisioned by Terraform with a password
-- from Secret Manager (`PRD.md` §6.3), and the migration refuses to run if it
-- is missing rather than inventing one.
--
-- This file is the *development* half of that: it runs once, on an empty data
-- directory, from the Postgres entrypoint. `make dev-reset` is what re-runs it.
--
-- The password here is a development credential for a container that listens on
-- localhost only. It is not a secret and is deliberately in the repository; the
-- compose file's `POSTGRES_PASSWORD` is the same kind of value.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'guardmem_app') THEN
        CREATE ROLE guardmem_app LOGIN PASSWORD 'guardmem_app';  -- pragma: allowlist secret
    END IF;
END
$$;

-- Connect and resolve names. Table privileges are the migration's business,
-- because they are the part that expresses an invariant.
GRANT CONNECT ON DATABASE guardmem TO guardmem_app;
GRANT USAGE ON SCHEMA public TO guardmem_app;
