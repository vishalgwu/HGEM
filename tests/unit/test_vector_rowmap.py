"""The pure half of the pgvector store.  BUILD_NOTEBOOK.md S3.2

`rowmap.py` decides two things that are worth pinning down without a database
in the way: what text an assertion is embedded from, and what id each of its
citations gets. Both are choices rather than mechanics - the first shapes
retrieval, the second is what makes a replayed write idempotent - and both are
ordinary functions, so `RULES.md` §5 puts them here rather than in the
integration suite.

The round trip in the other direction (`assertion_from_row`,
`provenance_from_row`) is deliberately NOT tested here. Those take an
`asyncpg.Record`, and a hand-built stand-in for one would only prove that the
test's idea of a row matches the code's. `tests/integration/test_pgvector_store.py`
reads them back from Postgres, which is the only place the question is real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from guardmem_core.memory.vector.rowmap import (
    ASSERTION_COLUMNS,
    EMBEDDING_DIM,
    INSERT_ASSERTION,
    assertion_params,
    embed_text,
    provenance_params,
)
from guardmem_core.schemas.base import ObjectValue
from guardmem_core.schemas.entity import StoredAssertion
from guardmem_core.schemas.receipt import Provenance, SourceTier
from guardmem_core.types import AssertionId, EntityId, Namespace, TenantId, TraceId

_WHEN: Final = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
_TENANT: Final = TenantId("t_acme")


def _citation(*, source_hash: str = "sha256:abc", span: tuple[int, int] = (13, 35)) -> Provenance:
    """One citation, varying only in the two fields its id is derived from."""
    return Provenance(
        source_hash=source_hash,
        source_span=span,
        source_tier=SourceTier.VERIFIED_USER,
        verbatim="allergic to penicillin",
        captured_at=_WHEN,
    )


def _assertion(
    *,
    assertion_id: str = "a_7f21",
    predicate: str = "allergy",
    obj: ObjectValue = "penicillin",
    provenance: list[Provenance] | None = None,
) -> StoredAssertion:
    """A minimal well-formed assertion."""
    return StoredAssertion(
        assertion_id=AssertionId(assertion_id),
        tenant_id=TenantId("t_other"),
        namespace=Namespace("patient:8812"),
        subject_id=EntityId("e_8812"),
        predicate=predicate,
        object=obj,
        confidence=0.9,
        risk=0.5,
        valid_from=_WHEN,
        recorded_at=_WHEN,
        provenance=provenance or [_citation()],
        trace_id=TraceId("tr_1"),
    )


class TestWhatGetsEmbedded:
    @pytest.mark.parametrize(
        ("obj", "expected"),
        [
            ("penicillin", "allergy: penicillin"),
            (0.5, "allergy: 0.5"),
            (True, "allergy: True"),
            ({"street": "12 Elm", "city": "Leeds"}, "allergy: 12 Elm Leeds"),
        ],
        ids=["string", "number", "boolean", "structured"],
    )
    def test_the_object_is_rendered_for_a_model_not_for_a_parser(
        self, obj: ObjectValue, expected: str
    ) -> None:
        """A dict renders as its values, not as JSON.

        Braces, quotes and keys are tokens an embedding model spends attention
        on and gets nothing back for; the values are what carry the meaning.
        """
        assert embed_text(_assertion(obj=obj)) == expected

    def test_the_subject_is_absent(self) -> None:
        """`subject_id` is a resolved `EntityId` - a UUID, semantically empty.

        Stated as a test because the alternative is tempting and wrong: joining
        the entity's canonical name in would make the vector depend on a row
        this store does not own and cannot re-embed when it changes.
        """
        assert "e_8812" not in embed_text(_assertion())

    def test_corroboration_does_not_change_the_text(self) -> None:
        """The same fact embeds the same however many sources it has.

        `provenance` is a list, so embedding `verbatim` would make the vector a
        function of how often a fact happened to be said. That is exactly the
        axis `MEMORY_ENGINE.md` §3.2 gives to `S_cor`, and retrieval must not
        double-count it.
        """
        once = _assertion(provenance=[_citation()])
        thrice = _assertion(provenance=[_citation(), _citation(span=(40, 61)), _citation()])

        assert embed_text(once) == embed_text(thrice)


class TestCitationIdsAreDerivedFromContent:
    def test_the_same_citation_yields_the_same_id(self) -> None:
        """What makes `ON CONFLICT DO NOTHING` work on `provenance` too.

        `Provenance` carries no id of its own - it is a value, not an entity -
        so a `uuid4` would make every replayed write insert a duplicate
        citation, and `corroboration_count` would start disagreeing with the
        evidence it exists to summarise.
        """
        first = provenance_params(_assertion())
        second = provenance_params(_assertion())

        assert first == second

    @pytest.mark.parametrize(
        "other",
        [_citation(source_hash="sha256:def"), _citation(span=(40, 61))],
        ids=["different-document", "different-span"],
    )
    def test_a_different_source_or_span_is_a_different_citation(self, other: Provenance) -> None:
        """Two spans in one document are two citations, and must stay two rows."""
        original = provenance_params(_assertion())[0][0]

        assert provenance_params(_assertion(provenance=[other]))[0][0] != original

    def test_the_span_is_split_into_the_bounds_int4range_takes(self) -> None:
        """The half-open pair goes to the database as two integers, not a tuple."""
        row = provenance_params(_assertion())[0]

        assert row[3] == 13
        assert row[4] == 35


class TestTheInsertParameters:
    def test_the_stores_tenant_wins_over_the_models(self) -> None:
        """They should agree; when they do not, the authenticated one is right.

        The store's tenant came from a request that was authenticated. The
        model's came from whatever constructed it, which on the write path is a
        pipeline several layers from any such check.
        """
        row = assertion_params(_assertion(), _TENANT, [0.0] * EMBEDDING_DIM)

        assert row[1] == _TENANT

    def test_visible_is_not_a_parameter_at_all(self) -> None:
        """There is no value a caller could pass that makes a row retrievable.

        `ASSERTION_COLUMNS` names sixteen columns; this tuple carries fifteen of
        them plus the embedding, because `visible` is a literal `false` in the
        statement. Making a row visible stays the outbox relay's decision, and
        not by convention - there is no placeholder to bind it through.

        The bool check is the load-bearing half. A `visible` parameter that
        crept back in would be the only boolean in the tuple, and
        `assert True not in row` would NOT catch it: `corroboration_count` is
        `1`, and `1 == True` in Python.
        """
        row = assertion_params(_assertion(), _TENANT, [0.0] * EMBEDDING_DIM)

        assert len(ASSERTION_COLUMNS.split(",")) == 16
        assert len(row) == 16
        assert row[-1] == [0.0] * EMBEDDING_DIM
        assert not any(isinstance(value, bool) for value in row)
        assert ", false," in " ".join(INSERT_ASSERTION.split())

    def test_the_object_is_json_encoded_for_jsonb(self) -> None:
        """`object_json` is JSONB; a bare Python string is not valid JSON."""
        row = assertion_params(_assertion(obj="penicillin"), _TENANT, [0.0] * EMBEDDING_DIM)

        assert row[5] == '"penicillin"'
