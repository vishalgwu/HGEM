"""The single exception hierarchy.  BUILD_NOTEBOOK.md S1.5

`RULES.md` §2.3: "Single exception hierarchy; each maps to an HTTP status **and
an MCP error code** exactly once, in one table. Handlers never invent status
codes."

This module is that table. Each class declares its stable `code`, the
`http_status` the REST gateway returns, the `mcp_code` the MCP server returns,
and whether a caller may retry. A handler reads those off the exception; it
never decides them, because a status invented at the boundary is a status that
drifts between the two surfaces.

**`mcp_code` is here, and the step that specified this module left it out.**
S1.5 lists only `code`, `http_status` and `retryable`, which leaves the MCP half
of the mapping living solely as a Markdown table in `MCP_INTEGRATION.md` §6 -
so `services/mcp_server` would have to re-derive it, and RULES §2.3 would have
two tables instead of the one it asks for. The values below are that table.

Usage, per `RULES.md` §2.3: catch narrowly and re-raise as a domain error with
context; never catch `Exception` outside the outermost boundary; every raise
inside the pipeline attaches `trace_id` and `candidate_id`. Those two are
explicit keyword arguments rather than free-form context precisely because the
rule names them - a misspelled key in a `**kwargs` bag is not a rule anyone is
actually following.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "BudgetExceeded",
    "ConcurrencyConflict",
    "GuardMemError",
    "InjectionDetected",
    "PolicyDenied",
    "ProviderUnavailable",
    "StoreUnavailable",
    "ValidationRejected",
]

# JSON-RPC 2.0 reserves -32768..-32000. Within that, -32700..-32600 are the
# protocol's own codes (parse error, invalid request, method not found, invalid
# params, internal error), and -32099..-32000 is the range an implementation may
# define. GuardMem uses `-32602` and `-32603` from the first group where the
# meaning matches exactly, and -32001..-32004 from the second for conditions
# JSON-RPC has no opinion about.
_JSONRPC_INVALID_PARAMS = -32602
_JSONRPC_INTERNAL_ERROR = -32603


class GuardMemError(Exception):
    """Base for every domain error, and the catch-all for an unclassified one.

    Subclasses override the class variables below; nothing else about them
    varies, which is what keeps the mapping honest.

    Attributes:
        code: Stable machine-readable identifier, surfaced to API callers. Never
            renamed - it is part of the published contract.
        http_status: What the REST gateway returns for this error.
        mcp_code: The JSON-RPC error code the MCP server returns.
        retryable: Whether a caller may retry the same request unchanged.
            `RULES.md` §2.3 permits retries only where this is true, with
            jittered backoff and a hard attempt cap.
    """

    code: ClassVar[str] = "GM_UNKNOWN"
    http_status: ClassVar[int] = 500
    mcp_code: ClassVar[int] = _JSONRPC_INTERNAL_ERROR
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        msg: str,
        *,
        trace_id: str | None = None,
        candidate_id: str | None = None,
        **context: object,
    ) -> None:
        """Create a domain error carrying the identifiers needed to trace it.

        Args:
            msg: Human-readable description. Must not contain PII or raw
                untrusted content - `RULES.md` §1.5 forbids it in logs and
                exceptions alike, and this string reaches both.
            trace_id: The proposal this failure belongs to. Supply it wherever
                one exists; it is what turns an error in an agent log into a
                lookup of the full decision record.
            candidate_id: The specific candidate, where the failure is about one
                rather than the whole proposal.
            **context: Additional structured detail for logs. Values should be
                small and safe to serialise.
        """
        super().__init__(msg)
        self.msg = msg
        self.trace_id = trace_id
        self.candidate_id = candidate_id
        self.context = context


class ValidationRejected(GuardMemError):
    """A candidate failed schema, ontology or provenance validation.

    Also the error for a `memory.commit` call whose provenance is missing or
    unverifiable - `MCP_INTEGRATION.md` §2.3: "There is no unsourced write path
    in this API."
    """

    code = "GM_VALIDATION"
    http_status = 422
    mcp_code = _JSONRPC_INVALID_PARAMS


class PolicyDenied(GuardMemError):
    """A policy pack refused the operation. The write is not permitted."""

    code = "GM_POLICY"
    http_status = 403
    mcp_code = -32001


class InjectionDetected(GuardMemError):
    """Prompt injection was found in untrusted content.

    Raised pre-flight, before any model sees the text, and also when a canary
    token appears in model output - which `RULES.md` §3 treats as a confirmed
    injection rather than a heuristic. The proposal is quarantined and an alert
    is raised; it is never "sanitised" and retried.
    """

    code = "GM_INJECTION"
    http_status = 422
    mcp_code = -32002


class BudgetExceeded(GuardMemError):
    """The tenant's token or spend cap is reached.

    Not retryable: the cap does not clear because a caller asked again. Per
    `ARCHITECTURE.md` §4 this degrades the pipeline to HITL rather than
    permitting an unlogged write.
    """

    code = "GM_BUDGET"
    http_status = 429
    mcp_code = -32003


class ProviderUnavailable(GuardMemError):
    """A model provider is unreachable or its circuit breaker is open."""

    code = "GM_PROVIDER"
    http_status = 503
    mcp_code = _JSONRPC_INTERNAL_ERROR
    retryable = True


class StoreUnavailable(GuardMemError):
    """A datastore is unreachable.

    Shares `mcp_code` with `ProviderUnavailable` by design: `MCP_INTEGRATION.md`
    §6 maps both to internal error, because the agent's correct response is the
    same either way. `code` still distinguishes them, which is what operators
    and the audit log need.
    """

    code = "GM_STORE"
    http_status = 503
    mcp_code = _JSONRPC_INTERNAL_ERROR
    retryable = True


class ConcurrencyConflict(GuardMemError):
    """A concurrent write changed the state this operation was based on.

    Retryable, but only after re-reading: `MCP_INTEGRATION.md` §6 tells the
    agent to re-read with `memory.search` and re-propose, not to repeat the
    same call.
    """

    code = "GM_CONFLICT"
    http_status = 409
    mcp_code = -32004
    retryable = True
