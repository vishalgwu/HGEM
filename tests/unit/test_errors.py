"""Tests for the single exception hierarchy.  BUILD_NOTEBOOK.md S1.5

S1.5's DONE WHEN asks for unique `code` and valid `http_status`. The specs
support rather more than that, and the extra assertions are not padding - each
one pins a claim some other document makes about this module:

- `RULES.md` §2.3 wants the HTTP *and* MCP mapping in one table, so the MCP
  codes are checked here rather than trusted to a Markdown table elsewhere.
- `MCP_INTEGRATION.md` §6 publishes the exact code pairs to agent authors. They
  are a contract, so they are pinned literally.
- `RULES.md` §2.3 permits retries only where `retryable` is true, which makes
  that flag a safety property and not documentation.

The subclass walk is recursive on purpose. `__subclasses__()` returns only
direct children, so a hierarchy that grows a second level would quietly stop
being covered by a test that looked like it covered everything.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from guardmem_core.errors import (
    BudgetExceeded,
    ConcurrencyConflict,
    GuardMemError,
    InjectionDetected,
    PolicyDenied,
    ProviderUnavailable,
    StoreUnavailable,
    ValidationRejected,
)

# JSON-RPC 2.0: -32768..-32000 is reserved; -32099..-32000 is the range an
# implementation may define for itself. These two are the protocol's own codes,
# used where the meaning matches exactly.
_JSONRPC_PREDEFINED = {-32700, -32600, -32601, -32602, -32603}
_JSONRPC_SERVER_RANGE = range(-32099, -31999)


def _hierarchy() -> list[type[GuardMemError]]:
    """Every error class, base included, found recursively."""

    def walk(cls: type[GuardMemError]) -> list[type[GuardMemError]]:
        return [cls, *(d for sub in cls.__subclasses__() for d in walk(sub))]

    return walk(GuardMemError)


def test_the_hierarchy_is_the_one_rules_specifies() -> None:
    """RULES.md 2.3 lists seven errors plus the base. Guard against drift.

    A new error class is a change to a published contract, not an
    implementation detail - it should arrive with a RULES update, and this
    failing is how anyone finds out.
    """
    assert set(_hierarchy()) == {
        GuardMemError,
        ValidationRejected,
        PolicyDenied,
        InjectionDetected,
        BudgetExceeded,
        ProviderUnavailable,
        StoreUnavailable,
        ConcurrencyConflict,
    }


def test_every_code_is_unique() -> None:
    """S1.5's DONE WHEN. `code` is the published identifier for the failure.

    Two classes sharing one would make the error indistinguishable to a caller
    and, worse, in the audit log.
    """
    codes = [cls.code for cls in _hierarchy()]
    duplicates = {code for code in codes if codes.count(code) > 1}
    assert not duplicates, f"duplicate error codes: {sorted(duplicates)}"


def test_every_code_uses_the_gm_prefix() -> None:
    """The prefix is what makes a GuardMem error recognisable in a caller's log."""
    offenders = [cls.__name__ for cls in _hierarchy() if not cls.code.startswith("GM_")]
    assert not offenders, f"codes without the GM_ prefix: {offenders}"


def test_every_http_status_is_valid() -> None:
    """S1.5's DONE WHEN. Handlers return these verbatim, so they must be real."""
    for cls in _hierarchy():
        status = HTTPStatus(cls.http_status)
        assert status.is_client_error or status.is_server_error, (
            f"{cls.__name__} maps to {status!r}, which is not an error status"
        )


def test_every_mcp_code_is_a_legal_jsonrpc_error_code() -> None:
    """RULES.md 2.3 puts the MCP mapping in this table too.

    A code outside the reserved ranges is not a JSON-RPC error an agent can
    interpret.
    """
    for cls in _hierarchy():
        assert cls.mcp_code in _JSONRPC_PREDEFINED or cls.mcp_code in _JSONRPC_SERVER_RANGE, (
            f"{cls.__name__} mcp_code {cls.mcp_code} is outside the JSON-RPC "
            "predefined codes and the server-defined range"
        )


@pytest.mark.parametrize(
    ("error", "code", "http_status", "mcp_code", "retryable"),
    [
        (ValidationRejected, "GM_VALIDATION", 422, -32602, False),
        (PolicyDenied, "GM_POLICY", 403, -32001, False),
        (InjectionDetected, "GM_INJECTION", 422, -32002, False),
        (BudgetExceeded, "GM_BUDGET", 429, -32003, False),
        (ProviderUnavailable, "GM_PROVIDER", 503, -32603, True),
        (StoreUnavailable, "GM_STORE", 503, -32603, True),
        (ConcurrencyConflict, "GM_CONFLICT", 409, -32004, True),
    ],
)
def test_mapping_matches_the_published_contract(
    error: type[GuardMemError],
    code: str,
    http_status: int,
    mcp_code: int,
    retryable: bool,
) -> None:
    """Pinned against RULES.md 2.3 and MCP_INTEGRATION.md 6, literally.

    These pairs are published to agent authors. Changing one is a breaking API
    change, so it should require editing this table and noticing why.
    """
    assert (error.code, error.http_status, error.mcp_code, error.retryable) == (
        code,
        http_status,
        mcp_code,
        retryable,
    )


def test_mcp_codes_are_deliberately_not_unique() -> None:
    """Provider and store failures share `-32603`, and that is correct.

    MCP_INTEGRATION.md 6 maps both to internal error because the agent's right
    response is identical. This test exists so nobody "fixes" the duplication
    by inventing a code the contract does not publish - `code` is what
    distinguishes them for operators and the audit log.
    """
    assert ProviderUnavailable.mcp_code == StoreUnavailable.mcp_code == -32603
    assert ProviderUnavailable.code != StoreUnavailable.code


def test_only_transient_failures_are_retryable() -> None:
    """RULES.md 2.3 allows retries only where `retryable` is true.

    A retryable permanent failure is an infinite loop; a non-retryable
    transient one is an outage that never recovers. Both are worth pinning.
    """
    retryable = {cls.code for cls in _hierarchy() if cls.retryable}
    assert retryable == {"GM_PROVIDER", "GM_STORE", "GM_CONFLICT"}


def test_error_carries_the_identifiers_rules_requires() -> None:
    """RULES.md 2.3: every raise in the pipeline attaches trace_id and candidate_id."""
    error = ValidationRejected(
        "no verbatim span for candidate",
        trace_id="tr_9f2a3c",
        candidate_id="c_1",
        predicate="allergy",
    )

    assert str(error) == "no verbatim span for candidate"
    assert error.trace_id == "tr_9f2a3c"
    assert error.candidate_id == "c_1"
    assert error.context == {"predicate": "allergy"}


def test_identifiers_default_to_none_rather_than_being_required() -> None:
    """Not every raise site has a trace - guardrails run before one is minted.

    Requiring them would push callers into inventing placeholder values, which
    is worse than an honest None.
    """
    error = GuardMemError("something went wrong")

    assert (error.trace_id, error.candidate_id, error.context) == (None, None, {})


def test_every_error_is_catchable_as_the_base() -> None:
    """One `except GuardMemError` at a boundary must catch the whole hierarchy.

    That is what lets RULES.md 2.3 forbid catching bare `Exception` anywhere
    except the outermost boundary.
    """
    for cls in _hierarchy():
        with pytest.raises(GuardMemError):
            raise cls("boom")
