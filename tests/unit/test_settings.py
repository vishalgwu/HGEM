"""Tests for the typed settings object.  BUILD_NOTEBOOK.md S1.4

Every test builds `Settings` with ``_env_file=None`` and explicit values. That
isolation is not ceremony: without it these tests would read whatever `.env` the
developer happens to have, pass locally for the wrong reason, and fail in CI
where no `.env` exists at all.

The defaults asserted here are the ones `MEMORY_ENGINE.md` §3.4 owns. If that
document changes them, this file is supposed to fail - the spec is the source of
record and the code follows it, not the other way round.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from conftest import REPO_ROOT
from guardmem_core.settings import Settings, get_settings

ENV_EXAMPLE = REPO_ROOT / ".env.example"

# The fields with no default. Supplied by every test that expects success.
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


def build(**overrides: Any) -> Settings:
    """Construct Settings from explicit values, ignoring any local `.env`."""
    return Settings(_env_file=None, **{**REQUIRED, **overrides})


def test_builds_from_explicit_values() -> None:
    """The happy path, and the shape every other test depends on."""
    settings = build()
    assert settings.env == "dev"
    assert str(settings.database_url) == REQUIRED["database_url"]


def test_defaults_match_the_spec_of_record() -> None:
    """Threshold defaults are owned by MEMORY_ENGINE.md 3.4.

    A silent edit here would move every decision boundary in the system, so the
    numbers are pinned against the document rather than against themselves.
    """
    settings = build()
    assert (settings.tau_lo, settings.tau_mid, settings.tau_hi) == (0.45, 0.60, 0.78)
    assert (settings.rho_lo, settings.rho_hi) == (0.35, 0.70)
    assert settings.default_k == 3
    assert settings.max_concurrent_scores == 8
    assert (settings.llm_timeout_s, settings.store_timeout_s) == (20.0, 5.0)


def test_missing_required_variables_fail_loudly() -> None:
    """S1.4's DONE WHEN: removing a required variable must not be survivable."""
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    missing = {entry["loc"][0] for entry in error.value.errors() if entry["type"] == "missing"}
    assert missing == set(REQUIRED), f"unexpected required set: {sorted(missing)}"


def test_undeclared_key_is_rejected() -> None:
    """`extra="forbid"` is what makes a typo'd variable loud instead of ignored.

    It is also the trap that makes `.env` unforgiving, which is why
    `.env.example` keeps later-step variables commented out.
    """
    # Raw string: the `|` is a deliberate alternation, because the wording of
    # this error differs between pydantic versions while the type code does not.
    with pytest.raises(ValidationError, match=r"extra_forbidden|Extra inputs"):
        build(budget_daily_usd=25)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"tau_lo": 0.9}, "tau_lo < tau_mid < tau_hi"),
        ({"tau_mid": 0.9}, "tau_lo < tau_mid < tau_hi"),
        ({"tau_hi": 0.1}, "tau_lo < tau_mid < tau_hi"),
        ({"rho_lo": 0.9}, "rho_lo < rho_hi"),
    ],
)
def test_thresholds_must_be_strictly_ordered(overrides: dict[str, float], expected: str) -> None:
    """Out-of-order thresholds produce an empty band, not an error, downstream.

    That makes a whole class of candidate unreachable while everything still
    looks healthy - so it has to be caught at construction.
    """
    with pytest.raises(ValidationError, match=re.escape(expected)):
        build(**overrides)


def test_thresholds_outside_the_unit_interval_are_rejected() -> None:
    """C and R are both defined on [0, 1]; a threshold outside it is meaningless."""
    with pytest.raises(ValidationError):
        build(tau_hi=1.5)


def test_neo4j_uri_must_use_a_driver_scheme() -> None:
    """The browser port answers HTTP, so this mistake looks like a working URL."""
    with pytest.raises(ValidationError, match="not a Neo4j scheme"):
        build(neo4j_uri="http://localhost:7474")


def test_malformed_store_urls_are_rejected() -> None:
    """DSN types turn a typo into a startup failure instead of a runtime one."""
    with pytest.raises(ValidationError):
        build(database_url="not-a-url")


def test_settings_are_immutable() -> None:
    """Configuration is read once. A mutated threshold mid-run would make the
    audit record of a decision unreproducible."""
    settings = build()
    with pytest.raises(ValidationError):
        settings.tau_hi = 0.5  # type: ignore[misc]


def test_environment_variables_are_read_with_the_gm_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefix is the contract between `.env.example` and this class."""
    for key, value in REQUIRED.items():
        monkeypatch.setenv(f"GM_{key.upper()}", str(value))
    monkeypatch.setenv("GM_TAU_HI", "0.81")

    settings = Settings(_env_file=None)

    assert settings.tau_hi == 0.81, "GM_-prefixed variables should win over defaults"


def test_get_settings_returns_one_shared_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers must observe the same object, or 'injected' means nothing."""
    for key, value in REQUIRED.items():
        monkeypatch.setenv(f"GM_{key.upper()}", str(value))
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_module_level_settings_name_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """`from guardmem_core.settings import settings` is S1.4's DONE WHEN.

    It resolves through the PEP 562 hook, so it constructs on access - which is
    what keeps the "fails loudly on bad configuration" behaviour the step asks
    for, without making a bare import of this module require an environment.
    """
    import guardmem_core.settings as module

    for key, value in REQUIRED.items():
        monkeypatch.setenv(f"GM_{key.upper()}", str(value))
    get_settings.cache_clear()
    try:
        assert module.settings.tau_hi == 0.78
    finally:
        get_settings.cache_clear()


def test_module_attribute_access_is_limited_to_settings() -> None:
    """PEP 562 `__getattr__` must not answer for names that do not exist."""
    import guardmem_core.settings as module

    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        _ = module.nope


def test_env_example_declares_exactly_the_settings_fields() -> None:
    """`.env.example` and this class must not drift apart.

    A field added here but not there leaves the template incomplete; a key left
    active there but not declared here makes `Settings()` raise on every boot,
    because `extra="forbid"` rejects it. Later-step variables are expected to be
    present but commented out.
    """
    active = {
        match.group(1).removeprefix("GM_").lower()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^(GM_[A-Z0-9_]+)=", line.strip()))
    }
    declared = set(Settings.model_fields)

    assert active == declared, (
        "`.env.example` active keys and Settings fields disagree:\n"
        f"  only in .env.example : {sorted(active - declared)}\n"
        f"  only in Settings     : {sorted(declared - active)}\n"
        "An active key that is not a declared field makes Settings() raise on "
        "every boot - extra='forbid'."
    )


# Every field that carries a credential, and what would appear in a log if it
# stopped being protected. The DSNs embed a password in their userinfo; the
# others are the credential. `gateway_api_keys` joined at S8.2 and is the worst of
# them to leak: one value carries every service key the deployment accepts, and
# each one resolves to a tenant.
_SECRET_BEARING = (
    "database_url",
    "redis_url",
    "neo4j_password",
    "anthropic_api_key",
    "gateway_api_keys",
)


def test_no_credential_survives_the_repr() -> None:
    """`repr(settings)` printed the Postgres, Redis and Neo4j passwords and both
    API keys in clear.

    Nothing in this repository logs `Settings` today, which is exactly why this
    is a test rather than a comment: the leak is one `logger.debug("%s", s)`, one
    crash reporter that captures frame locals, or one `pytest --showlocals` away,
    and none of those would look like a mistake at the call site. Two mechanisms
    protect it - `SecretStr` on the three credentials and `repr=False` on the two
    DSNs - and this asserts the outcome rather than either mechanism, so
    replacing one with the other stays a free choice.
    """
    settings = build(
        database_url="postgresql+asyncpg://u:pgsecret@h:5432/d",  # pragma: allowlist secret
        redis_url="redis://:redissecret@localhost:6379/0",  # pragma: allowlist secret
        neo4j_password="neosecret",  # pragma: allowlist secret
        anthropic_api_key="anthropicsecret",  # pragma: allowlist secret
        openai_api_key="openaisecret",  # pragma: allowlist secret
    )
    rendered = repr(settings)

    leaked = [
        value
        for value in ("pgsecret", "redissecret", "neosecret", "anthropicsecret", "openaisecret")
        if value in rendered
    ]
    assert not leaked, f"repr(Settings) exposes {leaked}: {rendered}"


def test_every_secret_bearing_field_is_still_readable() -> None:
    """Masking the repr must not mask the value.

    `libpq_dsn` needs the real DSN and `build_llm` needs the real key, so a
    field that redacted itself on access would fail at the first connection
    instead of in this file.
    """
    settings = build(anthropic_api_key="anthropicsecret")  # pragma: allowlist secret

    assert (
        settings.anthropic_api_key.get_secret_value() == "anthropicsecret"
    )  # pragma: allowlist secret
    assert str(settings.database_url) == REQUIRED["database_url"]


def test_an_unset_key_is_falsy_so_the_provider_guard_still_refuses() -> None:
    """`providers/selection.py` reads `if not settings.anthropic_api_key`.

    `SecretStr` is an object, and an object without `__bool__` is always truthy -
    which would have turned that refusal into a silent pass and let a blank key
    reach the SDK as a `401` four layers down. pydantic defines `__bool__` over
    the wrapped value; this pins that, because the failure is invisible.
    """
    assert not build().anthropic_api_key
    assert build(anthropic_api_key="x").anthropic_api_key


def test_the_secret_bearing_fields_are_the_ones_this_file_knows_about() -> None:
    """A new credential field should arrive with its masking, not after it.

    This is the reminder: adding a field whose name says credential without
    adding it to `_SECRET_BEARING` above fails here, and the fix is to give it
    `SecretStr` (or `repr=False`, for a type that cannot take one) and list it.
    """
    suspicious = {
        name
        for name in Settings.model_fields
        if any(word in name for word in ("password", "secret", "api_key", "token", "_url"))
    }
    # `ollama_url` and `neo4j_uri` carry no credential: Ollama is unauthenticated
    # and the Neo4j password is its own field.
    unclassified = suspicious - set(_SECRET_BEARING) - {"ollama_url", "openai_api_key"}

    assert not unclassified, (
        f"these fields look like credentials and are not listed as secret-bearing: "
        f"{sorted(unclassified)}. Give each one SecretStr (or repr=False where the "
        "type cannot take one) and add it to _SECRET_BEARING."
    )
