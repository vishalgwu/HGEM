"""Who is calling, and which tenant they speak for.  BUILD_NOTEBOOK.md S8.2

`PRD.md`'s AuthN line is "OIDC (SSO) for humans, mTLS + scoped API keys for
services". This is the service half and only the service half: OIDC belongs with
the dashboard's human sessions, and mTLS is deployment configuration rather than
application code.

**`AuthBackend` is a Protocol with one settings-backed implementation, and the
Protocol is the point.** Keys live in `GM_GATEWAY_API_KEYS` today because that
needs no migration and the isolation tests are what S8.2 is actually measured on.
An `api_key` table is where this goes - hashed keys, a tenant foreign key, and
**deliberately not RLS-protected**, because the lookup has to run *before* a
tenant is known and a tenant-scoped policy on the credential table is a
chicken-and-egg deadlock. Swapping to it should touch this file and nothing else,
which is only true if callers depend on the Protocol.

**A failed credential yields no detail.** `RULES.md` §1.5 keeps untrusted source
text and internal state out of anything a caller sees, and a credential error is
the same class: "no such key" and "that key is for another tenant" are different
sentences, and telling them apart is how a caller enumerates keys. Both are 401
with one message.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from guardmem_core.types import TenantId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from guardmem_core.settings import Settings

__all__ = ["AuthBackend", "InvalidCredentialError", "Principal", "SettingsAuthBackend"]

_LOGGER: Final = logging.getLogger(__name__)

# The scheme the `Authorization` header must use. Bearer rather than a bespoke
# `X-Api-Key`, because every HTTP client, proxy and log scrubber already knows to
# treat `Authorization` as sensitive and none of them know that about a custom
# header. `MCP_INTEGRATION.md` §1's `GUARDMEM_API_KEY` is the value, not the
# transport.
_SCHEME: Final = "bearer"


class InvalidCredentialError(Exception):
    """The credential is absent, malformed, or unknown.

    **Deliberately not a `GuardMemError`.** That hierarchy is the domain one and
    every member is serialised to a caller with a `code`, an `http_status` and an
    `mcp_code`. This never reaches a caller as itself: the middleware converts it
    to one fixed 401 body, because the distinctions this exception *could* draw -
    missing versus malformed versus unknown - are exactly the ones that let a
    caller enumerate valid keys.

    Carries a message for the server log only.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    Attributes:
        tenant_id: The tenant every statement made for this request speaks for.
            This is what reaches `set_config('app.tenant_id', ...)`, so it is the
            single value RLS enforces against - which makes it the most
            security-critical field in the gateway.
        key_id: A short, non-secret label for the credential used, for the audit
            trail and the log. **Never the key itself.** It is the first eight
            characters, which identifies which key was used among a handful
            without being usable as one.
        scopes: What this credential may do, from `PRD.md`'s "scoped API keys".
            Checked by route dependencies rather than here: authentication says
            who you are, authorization says what you may do, and a backend that
            did both would have to know every route.
    """

    tenant_id: TenantId
    key_id: str
    scopes: frozenset[str]

    def permits(self, scope: str) -> bool:
        """Whether this principal carries a scope.

        Args:
            scope: The scope a route requires, e.g. `memory:read`.

        Returns:
            True when the credential was issued with it.

        No wildcard handling and no implication rules - `memory:write` does not
        imply `memory:read`. A scope lattice is a thing to get wrong quietly, and
        an issuer that wants both grants both.
        """
        return scope in self.scopes


class AuthBackend(Protocol):
    """Resolves a credential to a principal.

    One method, so the settings implementation below and the `api_key` table that
    replaces it are interchangeable without a caller noticing.
    """

    def principal_for(self, credential: str) -> Principal:
        """Resolve one credential.

        Args:
            credential: The bearer token, already stripped of its scheme.

        Returns:
            The `Principal` it belongs to.

        Raises:
            InvalidCredentialError: it is unknown or malformed.
        """
        ...


class SettingsAuthBackend:
    """Keys from `GM_GATEWAY_API_KEYS`, parsed once.

    **Parsed at construction, not per request.** A per-request `json.loads` of
    the whole key set would put every credential through a parser on every call,
    and the failure mode of a malformed value would be a 500 on traffic rather
    than a process that would not start.
    """

    def __init__(self, settings: Settings) -> None:
        """Parse and validate the configured key set.

        Args:
            settings: Read for `gateway_api_keys`.

        Raises:
            ValueError: the value is not a JSON object of the documented shape.
                Raised at startup, where an operator is reading the output, rather
                than converted into a 401 that would look like a client problem.

        An empty value is valid and yields no keys - see the field's own comment
        in `settings.py`. The gateway still starts and still serves `/healthz`; it
        simply authenticates nobody.
        """
        raw = settings.gateway_api_keys.get_secret_value().strip()
        self._keys: Mapping[str, Principal] = _parse(raw) if raw else {}
        # The count, never the keys. Knowing "zero configured" is what makes a
        # deployment answering 401 to everything diagnosable in one log line.
        _LOGGER.info("gateway auth ready", extra={"configured_keys": len(self._keys)})

    def principal_for(self, credential: str) -> Principal:
        """Resolve one credential against the configured set.

        Args:
            credential: The bearer token.

        Returns:
            The `Principal` it maps to.

        Raises:
            InvalidCredentialError: no configured key matches.

        A plain dict lookup, which is not constant-time across keys. That is
        acceptable here and would not be for a password: these are
        high-entropy machine credentials, not guessable secrets, and the timing
        signal distinguishes "present in a dict of five" rather than narrowing a
        search space. The `api_key` table that replaces this compares a hash,
        where the comparison should be constant-time.
        """
        principal = self._keys.get(credential)
        if principal is None:
            raise InvalidCredentialError("no configured key matches the presented credential")
        return principal


def _parse(raw: str) -> dict[str, Principal]:
    """Turn the configured JSON into principals.

    Args:
        raw: The value of `GM_GATEWAY_API_KEYS`.

    Returns:
        Credential to `Principal`.

    Raises:
        ValueError: the JSON is malformed, or an entry is missing `tenant`.

    Every error message names the *shape* that was wrong and never echoes the
    value, because the value is a set of credentials and this runs at startup
    where the output is a log.
    """
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GM_GATEWAY_API_KEYS is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("GM_GATEWAY_API_KEYS must be a JSON object mapping key to grant")
    keys: dict[str, Principal] = {}
    for credential, grant in parsed.items():
        if not isinstance(grant, dict) or "tenant" not in grant:
            raise ValueError(
                "each GM_GATEWAY_API_KEYS entry must be an object with a `tenant` field"
            )
        scopes = grant.get("scopes", [])
        if not isinstance(scopes, list):
            raise ValueError("`scopes` must be a list of strings")
        keys[credential] = Principal(
            tenant_id=TenantId(str(grant["tenant"])),
            key_id=credential[:8],
            scopes=frozenset(str(scope) for scope in scopes),
        )
    return keys


def credential_from(header: str | None) -> str:
    """Extract a bearer token from an `Authorization` header.

    Args:
        header: The raw header value, or None when absent.

    Returns:
        The token, with its scheme removed.

    Raises:
        InvalidCredentialError: the header is absent or is not a bearer token.

    The scheme comparison is case-insensitive because RFC 7235 says it is, and a
    gateway that rejected `Bearer` while accepting `bearer` would be a support
    ticket rather than a security control.
    """
    if not header:
        raise InvalidCredentialError("no Authorization header")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != _SCHEME or not token.strip():
        raise InvalidCredentialError("Authorization header is not a non-empty bearer token")
    return token.strip()
