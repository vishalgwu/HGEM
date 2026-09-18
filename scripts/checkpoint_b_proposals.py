"""Reading a proposals file into `Proposal` objects.  CHECKPOINT B

Split out of `checkpoint_b_generate.py` on 2026-09-18, when wiring K to
`Settings.default_k` pushed that module past `RULES.md` §2.4's 400-line cap. The
seam is real rather than convenient: everything here turns a JSONL file into
`Proposal` objects and touches no store, no provider and no pipeline, which is
why it needs none of the generator's dozen adapter imports.
"""

from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.orchestrator import Proposal
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn
from guardmem_core.types import Namespace, TenantId, TraceId

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = ["read_proposals"]


def read_proposals(path: pathlib.Path | None, tenant: TenantId, k: int) -> Iterator[Proposal]:
    """Read the proposals to govern, one conversation per line.

    Args:
        path: The JSONL file, or `None` for the shipped seed transcript.
        tenant: The tenant to generate under.

    Yields:
        One `Proposal` per line.

    Raises:
        ValueError: a line is not an object with `namespace` and `turns`.

    **The default is a starting point and the checkpoint's own instruction is
    wrong about it.** Step 1 says "take 200 candidates from the seed
    transcript"; that transcript is forty turns for one patient supporting 28
    seeded facts, so it cannot produce 200 of anything. One conversation is one
    subject under one namespace, so reaching 200 means more conversations - and
    that is corpus work rather than code, which is why this reads a file.

    Each line is `{"namespace": ..., "turns": [...], "source_tier": ...}`.
    `source_tier` defaults to `verified_user`, matching `MCP_INTEGRATION.md`
    §2.2's own default being the weaker `unverified_user` only because a gateway
    cannot vouch for a caller; a transcript assembled for an eval can.
    """
    if path is None:
        from scripts.demo_tenant_data import NAMESPACE, TRANSCRIPT

        print("no --proposals given; using the shipped seed transcript (one subject)")
        yield _proposal(tenant, NAMESPACE, list(TRANSCRIPT), SourceTier.VERIFIED_USER, 1, k)
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if not isinstance(raw, dict) or "namespace" not in raw or "turns" not in raw:
            raise ValueError(f"{path}:{number}: needs an object with `namespace` and `turns`")
        yield _proposal(
            tenant,
            Namespace(str(raw["namespace"])),
            # `model_validate_json`, NOT `model_validate`. `GMModel` is strict,
            # so a dict carrying `"user"` for a `TurnRole` and an ISO string
            # for a `datetime` is refused - the same trap S5.6 hit with audit
            # payloads. Round-tripping through JSON is what accepts the
            # identical data, and a JSONL file is JSON to begin with.
            [Turn.model_validate_json(json.dumps(turn)) for turn in raw["turns"]],
            SourceTier(raw.get("source_tier", "verified_user")),
            number,
            k,
        )


def _proposal(
    tenant: TenantId,
    namespace: Namespace,
    turns: Sequence[Turn],
    tier: SourceTier,
    number: int,
    k: int,
) -> Proposal:
    """Build one proposal.

    The trace id is derived from the line number rather than random, so a
    re-run over the same file produces the same traces and `replay_trace.py`
    has something stable to reproduce.
    """
    return Proposal(
        trace_id=TraceId(f"tr_ckb_{number:04d}"),
        tenant_id=tenant,
        namespace=namespace,
        turns=list(turns),
        source_tier=tier,
        k=k,
        tier=Tier.FAST,
    )
