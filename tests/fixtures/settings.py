"""A validated `Settings` for tests, isolated from the developer's `.env`.

`Settings` has nine required fields and most tests care about none of them.
Three modules carried their own copy - `test_settings.py`, `fixtures/mcp.py` and
`test_llm_providers.py` - and a dozen modules with nothing to do with MCP
imported the builder from `fixtures/mcp.py`, because that is where the second
copy happened to live.

A plain helper module, not a pytest plugin: nothing here is a fixture.
"""

from __future__ import annotations

from typing import Any

from guardmem_core.settings import Settings

__all__ = ["REQUIRED", "settings"]

# The nine fields with no default, as *field names* rather than `GM_`-prefixed
# variables: the prefix applies to the environment, and a constructor takes the
# fields.
#
# These deliberately are NOT the dev credentials from `.env.example`. Test
# fixtures do not need credential-shaped strings, and putting them here would
# mean either suppressing a secret-scanner finding or baselining a line number
# inside a file that changes often - and a baseline entry that moves on every
# edit is what makes people stop reading baseline diffs. The Postgres DSN
# carries no userinfo for the same reason; it validates identically without it.
REQUIRED: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://localhost:5432/guardmem_test",
    "redis_url": "redis://localhost:6379/0",
    "neo4j_uri": "bolt://localhost:7687",
    "neo4j_user": "neo4j",
    # The `*password*` key trips the keyword detector whatever the value is, so
    # this one is marked rather than disguised.
    "neo4j_password": "unused-by-these-tests",  # pragma: allowlist secret
    "model_fast": "claude-haiku-4-5",
    "model_balanced": "claude-sonnet-5",
    "model_frontier": "claude-opus-5",
    "embed_model": "text-embedding-3-large",
}


def settings(**overrides: Any) -> Settings:
    """A validated `Settings` from `REQUIRED` and `overrides`, ignoring any `.env`.

    `_env_file=None` is not ceremony: tests assert on what happens when a field
    is *unset* - `mcp_tenant_id`, a tier's model id - and a populated local
    `.env` would make that case unreachable, passing locally for the wrong
    reason. `overrides` is `Any` because the DSN fields are typed
    `PostgresDsn`/`RedisDsn`/`AnyUrl`, which pydantic coerces from a string at
    runtime and `mypy --strict` rejects at the keyword.
    """
    return Settings(_env_file=None, **{**REQUIRED, **overrides})
