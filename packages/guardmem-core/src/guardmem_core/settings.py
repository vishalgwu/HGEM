"""The single typed configuration object.  BUILD_NOTEBOOK.md S1.4

`RULES.md` §2.4: "No global mutable state. Settings come from a single
`pydantic-settings` object, injected." Nothing in `guardmem_core` calls
`os.getenv`; if a value is configurable, it is a field here.

Every field is read from a `GM_`-prefixed environment variable, or from `.env`
in development. **`.env.example` is the inventory** - every variable, grouped,
each annotated with the step that turns it on - and the thresholds are owned by
`MEMORY_ENGINE.md` §3.4, which is the spec of record for what they mean.

`BUILD_NOTEBOOK.md` Appendix B is the *other* list, and a correction here once
claimed it "does not exist and never did; the notebook has no appendices". That
was wrong on both counts: the notebook carries appendices A-G, B is "Environment
variables (complete list)", and `tests/unit/test_docs_integrity.py` asserts all
seven are present precisely because that claim had been made and needed to be
checkable rather than arguable. The pointer was correct and was repointed away
from a section that was there all along.

`.env.example` is still the inventory of record, for a reason that survives the
correction: it is checked against this class by `tests/unit/test_settings.py`, so
a field added here and not there fails CI. Appendix B is a plan-time table that
nothing checks - it lists `GM_GRAPH_BACKEND` and `GM_BUDGET_DAILY_USD`, which are
not fields yet - so read it as the roadmap it is and `.env.example` as the state
of the world.

**`extra="forbid"` is the sharp edge.** Any key in `.env` that is not declared
below raises at construction - including keys with no `GM_` prefix. That is
deliberate (a typo'd variable should fail loudly, not be silently ignored), but
it means `.env` may only contain the fields declared here. Every later-step
variable in `.env.example` is commented out and labelled with the step that
turns it on; uncomment one only in the commit that adds its field.

**Construction is lazy, on purpose.** The notebook writes
`settings = Settings()` at module scope. That makes importing this module a
side effect: with no `.env` and no environment, the required fields raise, so
the module cannot be imported at all - which would make CI unable to run a test
that merely wants the `Settings` *class*. Instead `get_settings()` builds it
once on first use and `__getattr__` below keeps the documented
`from guardmem_core.settings import settings` import working and still failing
loudly, while `from guardmem_core.settings import Settings` stays free of
side effects.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from guardmem_core.schemas.verdict import Thresholds

# The schemes the Neo4j driver actually speaks. `GM_NEO4J_URI` pointing at the
# HTTP browser port instead of bolt is an easy mistake to make, because
# infra/docker/docker-compose.dev.yml publishes both 7474 and 7687.
_NEO4J_SCHEMES = frozenset({"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"})


class Settings(BaseSettings):
    """Typed application configuration, read once from the environment.

    Construct through :func:`get_settings` rather than directly, so the whole
    process shares one instance. Direct construction is still supported and is
    what the tests use, passing ``_env_file=None`` to isolate from a developer's
    local `.env`.

    Raises:
        pydantic.ValidationError: if a required variable is missing, a value is
            out of range, `.env` carries an undeclared key, the thresholds are
            not strictly ordered, or a URL is malformed for its store.
    """

    model_config = SettingsConfigDict(
        env_prefix="GM_",
        env_file=".env",
        # Explicit, because the default is the interpreter's locale encoding -
        # cp1252 on Windows. A `.env` containing any non-ASCII byte would then
        # parse differently on Windows and Linux. That exact class of bug cost
        # two CI runs to find in the detect-secrets baseline; it is not being
        # left to chance a second time.
        env_file_encoding="utf-8",
        extra="forbid",
        # Configuration is read once and never mutated. RULES.md 2.1 wants
        # domain objects immutable, and this is the most load-bearing object in
        # the process.
        frozen=True,
        # Values arriving from the environment are still coerced from strings -
        # pydantic-settings applies that before strict validation - so this only
        # rejects the programmatic mistake of passing a str where a float is
        # declared. Verified against pydantic 2.13 / pydantic-settings 2.15.
        strict=True,
    )

    # --- runtime ------------------------------------------------------------
    env: Literal["dev", "staging", "prod"] = "dev"

    # --- datastores ---------------------------------------------------------
    # DSN types rather than `str`: a malformed URL then fails at startup with a
    # precise message instead of at the first connection attempt, halfway
    # through a request. Verified that each round-trips to the exact input
    # string, so nothing downstream sees a normalised variant.
    #
    # `repr=False` on the two that carry credentials. A DSN embeds its password
    # in its userinfo, between the scheme and the host, and `repr(settings)`
    # printed the whole DSN in clear - which reaches a log the moment anything
    # renders this object: a debugger, a crash reporter that captures frame
    # locals, `--showlocals`.
    # `SecretStr` is the usual answer and is not available here without giving
    # up `PostgresDsn`'s validation, which is the thing that makes a typo fail at
    # startup instead of mid-request; excluding the field from `__repr__` keeps
    # both. It does not redact `model_dump()`, and it must not - Alembic and
    # `libpq_dsn` need the real string - so the rule this leaves behind is that
    # these two are never interpolated into a message.
    database_url: PostgresDsn = Field(repr=False)
    redis_url: RedisDsn = Field(repr=False)
    neo4j_uri: AnyUrl
    neo4j_user: str
    # `SecretStr`, so it is `**********` in any repr or f-string and has to be
    # asked for by name. Nothing in this repository reads it yet - S7.1's Neo4j
    # backend is the first - which is exactly when to get the type right.
    neo4j_password: SecretStr

    # Which `GraphStore` the composition roots build. S7.1's flag, verbatim
    # from the step: `GM_GRAPH_BACKEND=neo4j|networkx`.
    #
    # `networkx` is the default and stays the default. It needs no server, so a
    # clone with only Postgres running still passes the unit suite and still
    # serves `memory.get_entity` - and `ServerState.graph_durable` reports which
    # one is bound, so nothing downstream has to guess. Neo4j is what makes the
    # graph survive a restart; until a deployment needs that, the in-process one
    # is the honest default rather than the lesser one.
    graph_backend: Literal["networkx", "neo4j"] = "networkx"

    # --- model providers ----------------------------------------------------
    # Optional: the pipeline only needs these from S2.2 and S9.1 respectively,
    # and an empty secret is the honest representation of "not configured yet".
    #
    # `SecretStr` rather than `str`, for the reason above: `repr(settings)`
    # printed both keys in clear. Note that the emptiness checks in
    # `providers/selection.py` still read naturally - pydantic gives `Secret` a
    # `__bool__` over the wrapped value, so `if not settings.anthropic_api_key`
    # remains false-for-unset rather than silently always-true, which would have
    # disabled the refusal that keeps a blank key from falling back to a local
    # model. Verified against pydantic 2.13.
    anthropic_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")

    # Which adapter the composition roots build. S9.2 replaces this with a
    # *router* that picks per tier and falls back across providers; until then a
    # process talks to one provider and this is the honest way to say which.
    #
    # `anthropic` is the default because `model_fast` and its siblings are Claude
    # ids. `ollama` exists because it needs no credential, which is what makes
    # CHECKPOINT B runnable at all on a machine with no key - and because a
    # provider that costs nothing is the one a developer can leave running.
    llm_provider: Literal["anthropic", "openai", "ollama"] = "anthropic"

    # Only read when `llm_provider` is `ollama`. Not a `HttpUrl`: the adapter
    # joins paths onto it and `httpx` wants a string base URL, so validating it
    # into a different type here would only mean converting it back.
    ollama_url: str = "http://localhost:11434"
    # A tag, and RULES.md 3 wants a pinned id - `_reject_floating_tag` refuses
    # `:latest` and the adapter's docstring says why a tag is still weaker
    # evidence than a Claude id. The three `model_*` fields above are Claude ids
    # and cannot serve a local run, which is why this is separate rather than
    # another spelling of `model_fast`.
    ollama_model: str = "llama3.1:8b"
    # Separate from `llm_timeout_s`, and much larger, because it is a different
    # kind of wait. A hosted API that has not answered in 20 seconds is in
    # trouble; a local 7B model answering a cold prompt took over a minute on
    # the machine this was written on, and a 40-turn transcript exceeded ten.
    # One shared ceiling would either fire on a healthy local run or hide a sick
    # hosted one.
    ollama_timeout_s: float = Field(600.0, gt=0)

    # RULES.md 3: pinned ids, never floating aliases, or replay is dishonest.
    # Current Claude ids are complete as written - appending a date suffix to
    # one does not make it more specific, it makes it invalid.
    model_fast: str
    model_balanced: str
    model_frontier: str
    embed_model: str

    # --- decision thresholds (MEMORY_ENGINE.md 3.4) -------------------------
    # Four confidence bands need three confidence thresholds. Without tau_mid
    # the 0.60 boundary is hard-coded inside decide() and the matrix cannot be
    # tuned per namespace.
    tau_lo: float = Field(0.45, ge=0, le=1)
    tau_mid: float = Field(0.60, ge=0, le=1)
    tau_hi: float = Field(0.78, ge=0, le=1)
    rho_lo: float = Field(0.35, ge=0, le=1)
    rho_hi: float = Field(0.70, ge=0, le=1)
    # `PRD.md` FR-3.3 makes a threshold change an audited event, so every
    # `DecisionRecord` names the set that produced it. Bump this whenever any
    # of the five above moves, or the audit cannot tell two runs apart.
    thresholds_version: str = "v1"

    # --- mcp server (S6.2) --------------------------------------------------
    # Which tenant and namespace the MCP server acts for.
    #
    # **These exist because authentication does not.** `MCP_INTEGRATION.md` §1
    # carries a `GUARDMEM_API_KEY`, and in the finished system that key is what a
    # request's tenant is resolved from - by the gateway (S8.1) and its auth and
    # RLS-context middleware (S8.2). Neither exists, and this server talks
    # straight to Postgres, so there is nothing to resolve a tenant *from*. A
    # server that guessed would be a cross-tenant read, which is the isolation
    # failure the product exists to prevent, so it is stated in configuration
    # instead and the tools refuse to run without it.
    #
    # Empty by default, and deliberately not required: `Settings` has nine
    # required fields and every one of them is needed by *every* entry point,
    # while these two are needed by one service. A blank value is the honest
    # representation of "this process is not configured to serve memory tools",
    # and `mcp_server/context.py` turns it into a refusal that names the
    # variable rather than a query that returns somebody else's rows.
    #
    # `mcp_default_namespace` is §2.1's "defaults to server-configured
    # namespace" and §1's `GUARDMEM_DEFAULT_NAMESPACE`. A tool call may always
    # name its own.
    mcp_tenant_id: str = ""
    mcp_default_namespace: str = ""

    # --- gateway auth (S8.2) ------------------------------------------------
    # The scoped service API keys `PRD.md` §"AuthN" calls for, as a JSON object:
    #
    #     {"<key>": {"tenant": "<uuid>", "scopes": ["memory:read"]}}
    #
    # This is what the block above was waiting for. `mcp_tenant_id` exists
    # "because authentication does not"; for the gateway it now does, and a
    # request's tenant is resolved from its credential rather than from
    # configuration. The MCP server still reads its own field - it speaks stdio to
    # one client and has no request to carry a key.
    #
    # **`SecretStr`, and empty by default.** A key is a credential, so it must not
    # reach a log or a `repr` - `test_settings.py` asserts that for every field
    # that carries one. Empty is the honest representation of "this process has no
    # credentials configured", and the gateway still *starts*: `/healthz` and
    # `/readyz` need no principal, and a process that refused to boot without keys
    # could not report its own liveness. Every authenticated route answers 401
    # instead, which `auth.py` explains.
    #
    # Not a `dict` field, deliberately. pydantic-settings parses a JSON object
    # from the environment into one happily, but it would then be a plain dict in
    # `repr` output with the keys in it, and `SecretStr` is the only spelling that
    # survives the no-credentials-in-logs rule. `gateway.auth` parses it once at
    # startup and the parsed form never leaves that module.
    gateway_api_keys: SecretStr = SecretStr("")

    # --- pipeline tuning ----------------------------------------------------
    # K is 1, 3 or 5 by risk hint (MEMORY_ENGINE 1.2); this is the default arm.
    # The upper bound is a sanity rail, not a spec value - K scales cost
    # linearly and a stray 500 would be an expensive way to find that out.
    default_k: int = Field(3, ge=1, le=10)
    max_concurrent_scores: int = Field(8, ge=1)

    # --- timeouts -----------------------------------------------------------
    # RULES.md 2.2: every outbound call has an explicit timeout. These are the
    # defaults those calls take, so neither may be zero or negative.
    llm_timeout_s: float = Field(20.0, gt=0)
    store_timeout_s: float = Field(5.0, gt=0)

    def thresholds(self) -> Thresholds:
        """The five cut points as the value object `decide()` takes.

        Returns:
            A validated `Thresholds` carrying `thresholds_version`.

        Raises:
            ValueError: if the bands are out of order - `Thresholds` is what
                enforces that, and this is the only construction path.

        `MEMORY_ENGINE.md` §3.4 requires that `decide()` "never read from
        settings inside the function", so this is the seam: the orchestrator
        calls it once and passes the value down. It is a method rather than a
        cached property because `Settings` is frozen and building one is two
        microseconds.
        """
        return Thresholds(
            tau_lo=self.tau_lo,
            tau_mid=self.tau_mid,
            tau_hi=self.tau_hi,
            rho_lo=self.rho_lo,
            rho_hi=self.rho_hi,
            version=self.thresholds_version,
        )

    @model_validator(mode="after")
    def _thresholds_must_be_ordered(self) -> Settings:
        """Reject threshold sets the decision matrix cannot use.

        `MEMORY_ENGINE.md` §3.4 reads the bands as half-open intervals stacked
        in order. Supplying them out of order does not raise anywhere later - it
        silently produces a matrix with an empty band, so a whole class of
        candidate becomes unreachable and nothing looks wrong.

        The rule itself lives on `Thresholds`, which is the type that has to
        hold it - S5.4 moved it there when `decide()` needed the same check and
        a second copy would have been two homes for one invariant. Building one
        here keeps the failure at startup, where a bad `.env` should surface.

        Raises:
            ValueError: if not ``tau_lo < tau_mid < tau_hi`` or not
                ``rho_lo < rho_hi``.
        """
        self.thresholds()
        return self

    @model_validator(mode="after")
    def _neo4j_uri_must_be_a_bolt_scheme(self) -> Settings:
        """Reject a Neo4j URI the driver cannot connect with.

        The dev stack publishes the HTTP browser on 7474 and bolt on 7687, so
        pointing this at the browser is an easy and confusing mistake - the port
        answers, and the driver does not.

        Raises:
            ValueError: if the scheme is not one the Neo4j driver speaks.
        """
        if self.neo4j_uri.scheme not in _NEO4J_SCHEMES:
            raise ValueError(
                f"neo4j_uri scheme {self.neo4j_uri.scheme!r} is not a Neo4j scheme; "
                f"expected one of {sorted(_NEO4J_SCHEMES)}. The browser on 7474 "
                "speaks HTTP; the driver wants bolt on 7687."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, building them on first call.

    Cached so every caller shares one instance, and so the environment is read
    once rather than per injection. Call ``get_settings.cache_clear()`` in a
    test that needs to rebuild under a patched environment.

    Returns:
        The validated :class:`Settings` for this process.

    Raises:
        pydantic.ValidationError: if the environment does not satisfy
            :class:`Settings`.
    """
    return Settings()


def __getattr__(name: str) -> Settings:
    """Resolve the module-level ``settings`` name lazily (PEP 562).

    Keeps the documented ``from guardmem_core.settings import settings`` import
    working - and still failing loudly on bad configuration, since the import
    itself triggers construction - without making *every* import of this module
    require a populated environment.

    Returns:
        The shared :class:`Settings` when ``name`` is ``"settings"``.

    Raises:
        AttributeError: for any other attribute name.
        pydantic.ValidationError: if the environment does not satisfy
            :class:`Settings`.
    """
    if name == "settings":
        return get_settings()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
