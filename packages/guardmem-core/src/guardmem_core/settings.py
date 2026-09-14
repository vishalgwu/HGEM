"""The single typed configuration object.  BUILD_NOTEBOOK.md S1.4

`RULES.md` §2.4: "No global mutable state. Settings come from a single
`pydantic-settings` object, injected." Nothing in `guardmem_core` calls
`os.getenv`; if a value is configurable, it is a field here.

Every field is read from a `GM_`-prefixed environment variable, or from `.env`
in development. **`.env.example` is the inventory** - every variable, grouped,
each annotated with the step that turns it on - and the thresholds are owned by
`MEMORY_ENGINE.md` §3.4, which is the spec of record for what they mean.

That inventory used to be cited as `BUILD_NOTEBOOK.md` Appendix B, which does
not exist and never did; the notebook has no appendices. `.env.example` is where
the fact actually lives, it is checked against this class by
`tests/unit/test_settings.py`, and a reader following the old pointer found
nothing.

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

from pydantic import AnyUrl, Field, PostgresDsn, RedisDsn, model_validator
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
    database_url: PostgresDsn
    redis_url: RedisDsn
    neo4j_uri: AnyUrl
    neo4j_user: str
    neo4j_password: str

    # --- model providers ----------------------------------------------------
    # Optional: the pipeline only needs these from S2.2 and S9.1 respectively,
    # and an empty string is the honest representation of "not configured yet".
    anthropic_api_key: str = ""
    openai_api_key: str = ""

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
