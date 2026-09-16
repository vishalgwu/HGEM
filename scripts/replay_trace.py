"""Re-run an audited decision and diff it.  BUILD_NOTEBOOK.md S5.6

    uv run python scripts/replay_trace.py <trace_id> [--tenant <uuid>]

S5.6's DONE WHEN is that this prints "identical" for a fresh trace. What it
compares, and what it cannot, is the whole point of the script:

**It replays `decide()`, not the model calls.** `MEMORY_ENGINE.md` §3.4's
`decide` is the only part of the pipeline `RULES.md` §1.6 calls pure and
deterministic, and invariant I4 is stated about it alone. The extractor draws K
samples at temperature 0.7 and the judge is a network call; re-running those
would produce different numbers on a healthy system, so a "replay" that included
them would report a diff every time and mean nothing. What is replayable is the
step from three reports to one decision - and that is the step that decided
whether a fact entered memory, which is the one a regulator, a reviewer or an
incident asks about.

**The audit record carries its own inputs, which is why this works at all.**
`DecisionRecord` holds the confidence report, the risk verdict, the conflict
report and both version strings. So the replay does not re-derive anything: it
reads what the decision was taken from, feeds it back through the same function,
and compares. A diff therefore means exactly one thing - the code or the
thresholds changed - which is the signal `PRD.md` FR-3.3 makes a threshold
change an audited event for.

**A diff is a finding, not a failure.** Exit 1 when the decision changed, 0 when
it did not, so this composes into a check. The output names the reason codes on
both sides, because "hitl_review became auto_write" is a sentence a reviewer can
act on and a boolean is not.

**What is not covered, stated rather than implied.** The thresholds are the ones
in force *now*, read from settings, and the record carries only a version
string - so a replay under changed thresholds correctly reports a diff but
cannot re-run the old ones, because nothing stores a threshold set by version.
`RiskVerdict` has the same gap for §3.3's betas, recorded at S5.3. Neither
affects this DONE WHEN: `R` is an input to `decide()`, not something it
recomputes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from typing import TYPE_CHECKING

from guardmem_core.memory.vector.pool import create_pool, libpq_dsn, tenant_transaction
from guardmem_core.observability.audit_store import read_chain
from guardmem_core.pipeline.l3_score import MutationType, OverrideSignals, decide
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.verdict import DecisionRecord
from guardmem_core.settings import get_settings
from guardmem_core.types import TenantId, TraceId

if TYPE_CHECKING:
    from guardmem_core.schemas.receipt import AuditEvent

# The audit `kind` a decision is recorded under. Only these are replayable -
# a WRITE or a REVIEW event records something that happened, not something
# recomputable.
DECISION_KIND = "DECISION"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the trace and tenant off the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_id", help="the trace to replay")
    parser.add_argument(
        "--tenant",
        required=True,
        help=(
            "the tenant whose chain holds it. Required because `audit_event` is "
            "RLS-scoped: a connection without one reads zero rows and this would "
            "print 'identical' over an empty set."
        ),
    )
    return parser.parse_args(argv)


def replay(recorded: DecisionRecord, signals: OverrideSignals) -> DecisionRecord:
    """Re-run `decide()` on the inputs the record carries.

    Args:
        recorded: What was decided, carrying its own three reports.
        signals: The override context. Reconstructed by `signals_for`, which is
            where the one lossy part of this lives.

    Returns:
        The decision the current code reaches from the same inputs.
    """
    settings = get_settings()
    return decide(
        recorded.confidence,
        recorded.risk,
        recorded.conflict,
        settings.thresholds(),
        recorded.escalated_from is not None,
        signals,
    )


def signals_for(recorded: DecisionRecord) -> OverrideSignals:
    """Rebuild the override context from what the record kept.

    Args:
        recorded: The audited decision.

    Returns:
        An `OverrideSignals` reconstructed from `RiskVerdict.features` and the
        record's own policy version.

    **Two of the six are recoverable exactly, and three are not.** §3.3
    persists all eight risk features verbatim on the verdict, so
    `source_tier_risk` and `mutation_type` invert back to their enums - the five
    tier multipliers are distinct, and so are the four mutation values. That is
    not a trick: §3.3 requires the features to be stored so the review UI can
    show why something was flagged and the tuner can refit from them, and the
    same persistence makes the replay exact.

    `injection_detected`, `requires_corroboration`, `budget_exhausted` and
    `circuit_open` are runtime facts on no model. They are reconstructed benign,
    which is sound for this comparison because every override only ever
    *tightens*: `overrides.tighten` keeps the less permissive of the two. So a
    decision an override tightened replays as the untightened matrix result and
    shows up as a diff whose recorded reason codes name the override that did
    it. The output prints both sides for exactly that reason.

    Recording the signals on the decision would close the gap, and that is a
    field on a spec-of-record model - an ADR, not a change to a script.
    """
    features = recorded.risk.features
    return OverrideSignals(
        injection_detected=False,
        source_tier=_tier_from(features.get("source_tier_risk")),
        mutation=_mutation_from(features.get("mutation_type")),
        requires_corroboration=False,
        budget_exhausted=False,
        circuit_open=False,
        policy_version=recorded.policy_version,
    )


def _tier_from(risk: float | None) -> SourceTier:
    """Invert §3.3's `source_tier_risk` back to the tier that produced it.

    Args:
        risk: `1 - grounding_multiplier`, off the persisted features.

    Returns:
        The matching tier, or `RETRIEVED_WEB` when the feature is missing or
        matches none - the *lowest* trust, because the override it feeds fires
        on web content and guessing the benign end would silently drop it.
    """
    if risk is not None:
        for tier in SourceTier:
            if math.isclose(1.0 - tier.grounding_multiplier, risk, abs_tol=1e-9):
                return tier
    return SourceTier.RETRIEVED_WEB


def _mutation_from(feature: float | None) -> MutationType:
    """Invert §3.3's `mutation_type` back to its vocabulary.

    Returns:
        The matching mutation, or `RETRACT` when the feature is missing or
        matches none - the strictest, for the same reason `_tier_from` picks the
        least trusted: the override reading it fires on a critical retraction.
    """
    if feature is not None:
        for mutation in MutationType:
            if math.isclose(mutation.risk_feature, feature, abs_tol=1e-9):
                return mutation
    return MutationType.RETRACT


async def main(argv: list[str] | None = None) -> int:
    """Replay every decision in a trace and report.

    Returns:
        0 when every decision is identical, 1 when any differs, 2 when the trace
        holds no decision at all - which is a different answer from "identical"
        and should not be mistaken for one.
    """
    args = parse_args(argv)
    settings = get_settings()
    # `libpq_dsn`, not `str(...)`. `GM_DATABASE_URL` is specified to carry
    # SQLAlchemy's `postgresql+asyncpg://` marker for Alembic, and asyncpg
    # rejects it outright with `invalid DSN: scheme is expected to be either
    # "postgresql" or "postgres"`. This line omitted the conversion, so this
    # script could not open a connection against a correctly configured
    # environment at all - and its integration test could not see that,
    # because the fixture sets `GM_DATABASE_URL` to the testcontainer's
    # *libpq* DSN, which asyncpg accepts. The fixture now uses the SQLAlchemy
    # form, which is what a real deployment has.
    pool = await create_pool(libpq_dsn(str(settings.database_url)))
    try:
        async with tenant_transaction(
            pool, TenantId(args.tenant), timeout_s=settings.store_timeout_s
        ) as connection:
            links = await read_chain(
                connection, TenantId(args.tenant), timeout_s=settings.store_timeout_s
            )
    finally:
        await pool.close()

    decisions = _decisions_in(links, TraceId(args.trace_id))
    if not decisions:
        print(f"no DECISION events for trace {args.trace_id}")
        return 2

    diffs = 0
    for seq, recorded in decisions:
        again = replay(recorded, signals_for(recorded))
        if again.decision is recorded.decision:
            continue
        diffs += 1
        print(f"seq {seq}: {recorded.decision.value} -> {again.decision.value}")
        print(f"  recorded reasons: {recorded.reason_codes}")
        print(f"  replayed reasons: {again.reason_codes}")
        print(
            f"  thresholds: recorded {recorded.thresholds_version}, "
            f"now {settings.thresholds_version}"
        )

    if diffs:
        print(f"{diffs} of {len(decisions)} decisions differ")
        return 1
    print(f"identical ({len(decisions)} decisions)")
    return 0


def _decisions_in(
    links: list[AuditEvent], trace_id: TraceId
) -> list[tuple[int | None, DecisionRecord]]:
    """The replayable events in one trace, with their positions.

    **Revalidated as JSON, not as a dict, and that is not interchangeable
    here.** `GMModel` sets `strict=True`, so `model_validate` on a `dict`
    refuses the string forms of `Decision`, `ConflictKind` and `ImpactLevel` -
    which are exactly what `model_dump(mode="json")` wrote and what `JSONB`
    hands back. In JSON mode a string *is* how an enum is spelled, so the same
    strictness accepts it and the record round-trips identically.

    Strictness is doing its job in both directions: a payload that had been
    tampered into a wrong shape fails here rather than replaying as something
    plausible.
    """
    return [
        (link.seq, DecisionRecord.model_validate_json(json.dumps(link.payload)))
        for link in links
        if link.trace_id == trace_id and link.kind == DECISION_KIND
    ]


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
