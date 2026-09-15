"""`memory.propose` and `memory.commit`.  MCP_INTEGRATION.md §2.2/§2.3, S6.2

Both tools are one module because they are one thing with two front doors. §2.3
says so in as many words: `commit` "still passes the full pipeline — commit is
not a bypass, it just skips L1 extraction and requires the caller to supply
provenance explicitly." The argument reading differs; everything after it is the
same call into `guardmem_core.pipeline.run`.

**Neither can run today, and the reason is not in this file.** `run()` takes a
`Deps`, and it cannot be built yet. Two members have no implementation anywhere
in this repository and a third is implemented but not wired:

- `EntityResolver` - turning `"Joan Ellery"` into an `EntityId`. Specified in no
  document: not the notebook, not `MEMORY_ENGINE.md`, not `ARCHITECTURE.md`.
  `pipeline/deps.py` explains at length why a default would be worse than none.
- `CandidateClassifier` - §3.3's `pii_class` and `irreversibility`, which are
  policy decisions a deploying organisation makes.
- `EntailFn` now has a producer - `llm/entailment.py`'s `LLMEntailer` - and is
  still not injectable here, because the callable is sync and the producer is
  async. `entropy.py` names the resolution: the caller precomputes the pairs and
  passes a lookup, which is a change to the orchestrator's `_score_and_decide`.

`LLMClient` was on this list until **S9.1** and is not any more; three provider
adapters implement it.

So these handlers validate their arguments completely and then refuse, naming
every missing piece and the step that builds it. **That is deliberately not a
stub that returns a plausible decision.** A `memory.propose` that answered
`auto_write` with an invented confidence would be the single most harmful thing
this repository could ship: the product's entire claim is that a fact was
governed before it was believed, and a tool that says so without having done it
is worse than no tool. `_require_pipeline` is the one function to delete when
the last of the three above is closed.

**Two more fields of §2.2 need steps that do not exist**, recorded here so they
are not mistaken for oversights when the rest is wired:

- `mode: "async"` is §2.2's default and needs the Redis stream and arq worker
  from **S8.4**. With no queue, "returns immediately" would mean "returns and
  never decides".
- `review_task_id` and `eta_minutes` on a `hitl_review` candidate need the HITL
  queue from **S18.1**. A decision can be *reached* without them; what cannot be
  produced is the ticket a human would clear.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from mcp_server.tools.context import ToolRefusedError

if TYPE_CHECKING:
    from mcp_server.tools.context import ToolContext

__all__ = ["MISSING_DEPENDENCIES", "run_commit", "run_propose"]

# §2.2's `source_tier` enum, exactly as published, and `SourceTier`'s members.
# Checked here so a bad tier is a tool error naming the vocabulary rather than a
# pydantic `ValidationError` from four layers down.
_SOURCE_TIERS: Final = frozenset(
    {"trusted_system", "verified_user", "unverified_user", "tool_output", "retrieved_web"}
)

# §1.2's risk-hint ladder: K is 1, 3 or 5. The mapping is `MEMORY_ENGINE.md`'s,
# not this module's - `low` draws once because a low-risk fact does not justify
# five completions, and `high` draws five because entropy over three samples is
# a coarse measurement to bet a clinical write on.
_K_BY_RISK_HINT: Final = {"low": 1, "default": 3, "high": 5}

_MODES: Final = frozenset({"async", "strict"})

# What a caller is told is missing, in the order a reader should think about it:
# the two that need a decision first, then the one that needs only wiring.
MISSING_DEPENDENCIES: Final = (
    "EntityResolver (surface form to EntityId; specified in no document, needs an ADR)",
    "CandidateClassifier (§3.3's pii_class and irreversibility; deployment policy)",
    "the orchestrator's entailment wiring (LLMEntailer exists; `_score_and_decide` "
    "has to assemble the pairs and await one lookup before scoring)",
)


def _require_pipeline(what: str) -> None:
    """Refuse a call that would need `pipeline.run`.

    Args:
        what: The tool being called, for the message.

    Raises:
        ToolRefusedError: always, today. This is the whole function.

    **Delete this when the last missing dependency is closed** - it is the
    single place the two write tools are gated, so wiring a real `Deps` is a
    change to one call site rather than a hunt through two handlers.

    The message lists every missing dependency rather than the first one,
    because they are not sequential: an operator who closed `EntityResolver`
    alone would hit the next refusal and reasonably conclude the work was
    open-ended. S9.1 is the proof - it closed `LLMClient`, which was first on
    this list, and three entries remained.
    """
    raise ToolRefusedError(
        f"{what} cannot run: the decision pipeline is not wired in this build. "
        "Missing - " + "; ".join(MISSING_DEPENDENCIES) + ". "
        "This tool deliberately does not return an invented decision: the "
        "product's claim is that a fact was governed before it was believed, and "
        "a tool that says so without having done it is worse than no tool. "
        "`memory.search` and `memory.get_entity` read governed memory and do work."
    )


async def run_propose(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer §2.2: submit candidate facts for governance.

    Args:
        context: Tenant, namespace and a bound store.
        arguments: The tool call's arguments.

    Returns:
        §2.2's result object - once the pipeline is wired.

    Raises:
        ToolRefusedError: an argument is invalid, or the pipeline is not wired.

    Arguments are validated **before** the refusal, deliberately. A caller
    getting "the pipeline is not wired" for a call that was also malformed
    learns one problem and ships the other; and when the pipeline is wired, every
    one of these checks is already the right check rather than something written
    in a hurry against a tool that had never run.
    """
    _require_content(arguments)
    _require_source_tier(arguments)
    _require_risk_hint(arguments)
    _require_mode(arguments)
    _require_hints(arguments)
    _require_idempotency_key(arguments)
    _require_pipeline("memory.propose")
    raise AssertionError("unreachable until the pipeline is wired")  # pragma: no cover


async def run_commit(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer §2.3: commit structured facts that already carry provenance.

    Args:
        context: Tenant, namespace and a bound store.
        arguments: The tool call's arguments.

    Returns:
        §2.3's result object - once the pipeline is wired.

    Raises:
        ToolRefusedError: an assertion is malformed or unsourced, or the
            pipeline is not wired.

    **The provenance check runs first and is not deferred with the rest.** §2.3
    is unambiguous - "missing or unverifiable provenance → `GM_VALIDATION`. There
    is no unsourced write path in this API" - and `RULES.md` non-negotiable #1
    makes an unsourced write a P0. That rule is enforceable today, with no
    pipeline at all, so it is enforced today: a caller sending an unsourced
    assertion is told *that*, rather than being told the pipeline is missing and
    left to discover the real objection months later.
    """
    _require_assertions(arguments)
    _require_idempotency_key(arguments)
    _require_pipeline("memory.commit")
    raise AssertionError("unreachable until the pipeline is wired")  # pragma: no cover


# --- §2.2 argument reading --------------------------------------------------


def _require_content(arguments: dict[str, Any]) -> str:
    content = arguments.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ToolRefusedError("`content` is required and must be a non-empty string")
    return content


def _require_source_tier(arguments: dict[str, Any]) -> str:
    tier = arguments.get("source_tier", "unverified_user")
    if tier not in _SOURCE_TIERS:
        raise ToolRefusedError(
            f"`source_tier` must be one of {sorted(_SOURCE_TIERS)}, got {tier!r}"
        )
    return str(tier)


def _require_risk_hint(arguments: dict[str, Any]) -> int:
    """Read §2.2's `risk_hint` and return the K it selects.

    Raises:
        ToolRefusedError: the hint is outside the published enum.

    Returns K rather than the hint because that is the only thing downstream
    uses it for - `MEMORY_ENGINE.md` §1.2 ties the two - and converting at the
    edge means the ladder is written once.
    """
    hint = arguments.get("risk_hint", "default")
    if hint not in _K_BY_RISK_HINT:
        raise ToolRefusedError(
            f"`risk_hint` must be one of {sorted(_K_BY_RISK_HINT)}, got {hint!r}"
        )
    return _K_BY_RISK_HINT[str(hint)]


def _require_mode(arguments: dict[str, Any]) -> str:
    """Read §2.2's `mode`.

    Raises:
        ToolRefusedError: the mode is outside the enum, or is `async`.

    **`async` is refused rather than silently served synchronously**, even
    though it is §2.2's *default*. It means "returns immediately, decides
    later", and deciding later needs the Redis stream and arq worker from S8.4;
    with no queue behind it, answering immediately would mean answering and never
    deciding - a fact the caller believes is pending that nothing will ever pick
    up. Refusing names the gap; serving it synchronously would hide it behind
    latency that happens to be acceptable in a demo.
    """
    mode = arguments.get("mode", "async")
    if mode not in _MODES:
        raise ToolRefusedError(f"`mode` must be one of {sorted(_MODES)}, got {mode!r}")
    if mode == "async":
        raise ToolRefusedError(
            "`mode: async` needs the worker queue from BUILD_NOTEBOOK.md S8.4, which "
            "is not built. It means 'return now, decide later', and with no queue "
            "the second half never happens. Pass `mode: strict` to be told the "
            "decision on the call. Note that async is §2.2's default, so this has "
            "to be passed explicitly until S8.4 lands."
        )
    return str(mode)


def _require_hints(arguments: dict[str, Any]) -> dict[str, Any]:
    """Read §2.2's `hints` object.

    Raises:
        ToolRefusedError: `hints` is not an object, or a member has the wrong
            type.

    `hints.subject` is more load-bearing than it looks. `pipeline/deps.py` calls
    entity resolution "the largest gap in the build", and `Proposal.subject_hint`
    exists so "a caller that already knows the answer says so" - which makes this
    field the one route by which a proposal could be governed at all before an
    `EntityResolver` is specified.
    """
    hints = arguments.get("hints")
    if hints is None:
        return {}
    if not isinstance(hints, dict):
        raise ToolRefusedError(f"`hints` must be an object, got {type(hints).__name__}")
    subject = hints.get("subject")
    if subject is not None and not isinstance(subject, str):
        raise ToolRefusedError("`hints.subject` must be a string")
    interesting = hints.get("predicates_of_interest")
    if interesting is not None and (
        not isinstance(interesting, list) or not all(isinstance(p, str) for p in interesting)
    ):
        raise ToolRefusedError("`hints.predicates_of_interest` must be an array of strings")
    return hints


def _require_idempotency_key(arguments: dict[str, Any]) -> str | None:
    key = arguments.get("idempotency_key")
    if key is not None and not isinstance(key, str):
        raise ToolRefusedError(f"`idempotency_key` must be a string, got {type(key).__name__}")
    return key


# --- §2.3 argument reading --------------------------------------------------


def _require_assertions(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Read §2.3's `assertions`, enforcing its four required keys.

    Raises:
        ToolRefusedError: the array is missing, empty, or an item is malformed or
            unsourced.

    The four keys are §2.3's own `required` list, copied exactly:
    `subject`, `predicate`, `object`, `provenance`. Their *values* are not
    validated here beyond provenance being present and non-empty - the ontology
    decides what an `object` may be for a given predicate, and
    `MEMORY_ENGINE.md` §2.1 puts that decision in the schema gate. Re-stating the
    predicate vocabulary in this file would put it in two places.
    """
    assertions = arguments.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        raise ToolRefusedError("`assertions` is required and must be a non-empty array")
    for index, item in enumerate(assertions):
        if not isinstance(item, dict):
            raise ToolRefusedError(f"assertions[{index}] must be an object")
        missing = [
            key for key in ("subject", "predicate", "object", "provenance") if key not in item
        ]
        if missing:
            raise ToolRefusedError(f"assertions[{index}] is missing {missing}")
        _require_provenance(index, item["provenance"])
    return assertions


def _require_provenance(index: int, provenance: object) -> None:
    """Enforce §2.3's one hard rule, which needs no pipeline to enforce.

    Raises:
        ToolRefusedError: provenance is absent, empty, or carries no verbatim
            span.

    "There is no unsourced write path in this API", and `RULES.md`
    non-negotiable #1 makes a write without a source span a P0 bug. A `verbatim`
    is required because that is what makes the citation checkable: an assertion
    claiming a source it cannot quote is exactly what the span rule exists to
    catch, and accepting one here would mean the check happened nowhere.
    """
    entries = provenance if isinstance(provenance, list) else [provenance]
    if not entries:
        raise ToolRefusedError(
            f"assertions[{index}].provenance is empty. There is no unsourced write "
            "path in this API (MCP_INTEGRATION.md §2.3)."
        )
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("verbatim"):
            raise ToolRefusedError(
                f"assertions[{index}].provenance needs a `verbatim` span quoting the "
                "source. A citation that cannot be quoted cannot be checked, which "
                "is what RULES.md non-negotiable #1 forbids."
            )
