"""Shared scaffolding for the conflict-detection tests.  S4.3

Split out when `tests/unit/test_conflict_detection.py` reached the 400-line
module cap `RULES.md` §2.4 sets - the same seam, and the same reason, as
`fixtures/extraction.py` at S2.2: what lives here is the *setup* the probe and
the behavioural tests share, and the test modules keep the assertions.

`CLINICAL` is the shipped pack rather than a hand-written fragment, for the
reason recorded beside `extraction.ONTOLOGY`: the cardinality this fixture reads
has to be the cardinality the schema gate enforces, or the probe measures a
vocabulary nothing ships.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fixtures.assertions import NS, WHEN, stored_assertion
from guardmem_core.memory.vector.base import ScoredAssertion
from guardmem_core.pipeline.l2_validate import IncumbentSet, Judgement, detect
from guardmem_core.schemas import load_ontology
from guardmem_core.schemas.candidate import MemoryCandidate
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import CandidateId, TenantId, TraceId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from fixtures.conflict_pair import ConflictPair
    from guardmem_core.schemas.base import ObjectValue
    from guardmem_core.schemas.verdict import ConflictKind

__all__ = [
    "CLINICAL",
    "PROBE_FLOOR",
    "TENANT",
    "ScriptedJudge",
    "candidate",
    "incumbents",
    "judge_pair",
]

CLINICAL: Final = load_ontology("clinical")
TENANT: Final = TenantId("11111111-1111-1111-1111-111111111111")

# The DONE WHEN's bar. `RULES.md` §5 words the same idea as "contradiction probe
# >= 90%", so the number lives in both places and is stated here once.
PROBE_FLOOR: Final = 0.90


@dataclass
class ScriptedJudge:
    """An `NLIJudge` that returns pre-set numbers and records that it was asked.

    The recording is half the point. Two of the three checks are supposed to
    answer *without* a model, and a result set cannot show whether one was
    consulted - only `calls` can.
    """

    judgements: list[Judgement] = field(default_factory=list)
    calls: int = 0

    async def compare(
        self, candidate: str, incumbents: Sequence[str], *, trace_id: TraceId
    ) -> list[Judgement]:
        """Return the scripted judgements, one per incumbent."""
        self.calls += 1
        if self.judgements:
            return self.judgements[: len(incumbents)]
        return [Judgement(entail_fwd=0.0, entail_rev=0.0, contradiction=0.0)] * len(incumbents)


def candidate(
    *,
    predicate: str,
    obj: ObjectValue,
    verbatim: str = "claim",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> MemoryCandidate:
    """A candidate claiming `obj` under `predicate`.

    The two bounds default to `None`, which is what every candidate carries
    today - `extract_memories/v1.md` does not ask for world time. Pass them to
    reach `_overlaps`' interval arithmetic, which nothing upstream can produce
    yet and S4.5's temporal extraction will.
    """
    return MemoryCandidate(
        candidate_id=CandidateId("c_1"),
        valid_from=valid_from,
        valid_to=valid_to,
        tenant_id=TENANT,
        namespace=NS,
        subject="Joan Ellery",
        predicate=predicate,
        object=obj,
        provenance=Provenance(
            source_hash="sha256:abc",
            source_span=(0, 10),
            source_tier=SourceTier.VERIFIED_USER,
            verbatim=verbatim,
            captured_at=WHEN,
        ),
        extracted_by="claude-haiku-4-5",
        prompt_version="extract_memories@v1",
        trace_id=TraceId("tr_s43"),
    )


def incumbents(*facts: tuple[str, ObjectValue, str], cosine: float = 0.9) -> IncumbentSet:
    """An `IncumbentSet` from `(predicate, object, verbatim)` triples."""
    return IncumbentSet(
        candidate_id=CandidateId("c_1"),
        nearest=[
            ScoredAssertion(
                assertion=stored_assertion(
                    tenant_id=TENANT,
                    predicate=predicate,
                    obj=obj,
                    visible=True,
                    provenance=[
                        Provenance(
                            source_hash="sha256:inc",
                            source_span=(0, 10),
                            source_tier=SourceTier.VERIFIED_USER,
                            verbatim=verbatim,
                            captured_at=WHEN,
                        )
                    ],
                ),
                cosine=cosine,
            )
            for predicate, obj, verbatim in facts
        ],
        neighbours=[],
    )


async def judge_pair(pair: ConflictPair) -> ConflictKind:
    """Run `detect` over one corpus pair and return the kind it assigned."""
    spec = CLINICAL.predicate(pair.predicate)
    assert spec is not None, f"{pair.key}: {pair.predicate} is not in the clinical pack"
    scripted = (
        []
        if pair.contradiction is None
        else [
            Judgement(
                entail_fwd=pair.entail_fwd,
                entail_rev=pair.entail_fwd,
                contradiction=pair.contradiction,
            )
        ]
    )
    report = await detect(
        candidate(
            predicate=pair.predicate, obj=pair.candidate_object, verbatim=pair.candidate_verbatim
        ),
        incumbents((pair.predicate, pair.incumbent_object, pair.incumbent_verbatim)),
        spec,
        ScriptedJudge(judgements=scripted),
    )
    return report.kind
