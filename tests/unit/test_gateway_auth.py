"""Resolving a credential to a principal.  BUILD_NOTEBOOK.md S8.2

`PRD.md`'s AuthN line is "mTLS + scoped API keys for services", and this is the
key half: what `GM_GATEWAY_API_KEYS` is parsed into, and what happens when it is
malformed.

**Split from `test_gateway_middleware.py` on 2026-09-26.** That module passed
`RULES.md` 2.4's 400-line cap when ADR-0012's deadline layer arrived, and this
class was the part that did not belong there anyway: it calls
`SettingsAuthBackend` directly and never builds an app, a client or a chain. The
middleware module keeps the tests that drive HTTP.

No `.env` is read. `fixtures.mcp.settings` passes `_env_file=None`, and these
tests assert on what a *given* key set produces - a populated local environment
supplying a third key would make some of them unreachable.
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from gateway.auth import InvalidCredentialError, SettingsAuthBackend
from guardmem_core.types import TenantId

TENANT_A = TenantId("11111111-1111-4111-8111-111111111111")
KEY_A = "key-a-0000000000"


class TestTheSettingsBackend:
    """Parsing `GM_GATEWAY_API_KEYS`, and refusing to guess."""

    def _backend(self, value: str) -> SettingsAuthBackend:
        """Build a backend over one configured value.

        Args:
            value: What `GM_GATEWAY_API_KEYS` would hold.

        Returns:
            The backend.
        """
        settings = type("_S", (), {"gateway_api_keys": SecretStr(value)})()
        return SettingsAuthBackend(settings)

    def test_an_empty_value_authenticates_nobody_and_does_not_raise(self) -> None:
        """The gateway must still start. `/healthz` needs no principal, and a
        process that refused to boot without keys could not report its own
        liveness - so "no keys" is a valid configuration that 401s everything."""
        backend = self._backend("")

        with pytest.raises(InvalidCredentialError):
            backend.principal_for("anything")

    def test_a_configured_key_resolves_to_its_tenant_and_scopes(self) -> None:
        """The whole point: a credential names a tenant."""
        backend = self._backend(
            json.dumps({KEY_A: {"tenant": str(TENANT_A), "scopes": ["memory:read"]}})
        )

        resolved = backend.principal_for(KEY_A)

        assert resolved.tenant_id == TENANT_A
        assert resolved.permits("memory:read")
        assert not resolved.permits("memory:write")

    def test_the_key_id_is_not_the_key(self) -> None:
        """`key_id` reaches logs and the audit trail, so it must not be usable.

        Eight characters identifies which credential was used among a handful
        without being one.
        """
        backend = self._backend(json.dumps({KEY_A: {"tenant": str(TENANT_A)}}))

        resolved = backend.principal_for(KEY_A)

        assert resolved.key_id == KEY_A[:8]
        assert resolved.key_id != KEY_A

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("{not json", id="malformed-json"),
            pytest.param('["a"]', id="not-an-object"),
            pytest.param('{"k": "just-a-string"}', id="grant-not-an-object"),
            pytest.param('{"k": {"scopes": []}}', id="no-tenant"),
            pytest.param('{"k": {"tenant": "t", "scopes": "read"}}', id="scopes-not-a-list"),
        ],
    )
    def test_a_malformed_key_set_raises_at_construction(self, value: str) -> None:
        """At startup, where an operator is reading the output.

        Deferring it to the first request would turn a configuration mistake into
        a 500 on traffic, which reads as a client problem.
        """
        with pytest.raises(ValueError, match=r"GM_GATEWAY_API_KEYS|tenant|scopes"):
            self._backend(value)

    def test_no_error_message_echoes_the_configured_value(self) -> None:
        """The value is a set of credentials. A `ValueError` at startup goes to a
        log, and a log that contains the keys is the leak `SecretStr` exists to
        prevent one spelling of."""
        # The `secret =` assignment trips the keyword detector whatever the
        # value is, so it is marked rather than disguised - the same call
        # `fixtures/mcp.py` makes for its unused Neo4j password. Renaming the
        # variable to dodge the scanner would be hiding the pattern the
        # scanner exists to find.
        secret = "super-secret-key-material"  # pragma: allowlist secret

        with pytest.raises(ValueError) as caught:
            self._backend(json.dumps({secret: {"scopes": []}}))

        assert secret not in str(caught.value)
