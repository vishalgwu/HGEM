"""What a fact is embedded from.  BUILD_NOTEBOOK.md S3.2, moved at S4.2

`embed_text` is the single most consequential pure function in the storage
layer, because both sides of every cosine comparison go through it. A stored
assertion's vector is computed from its output at write time, and from S4.2 a
*candidate* is embedded by the same function before being matched against those
vectors. If the two sides ever rendered differently the distances would still be
numbers, would still order the results, and would mean nothing - so the rendering
is pinned here rather than left to be inferred from the store's behaviour.

It lives in `memory/vector/base.py` rather than `rowmap.py` as of S4.2. The
import contract is what moved it: `rowmap` imports `asyncpg` for one annotation,
so anything under `pipeline/` that reached for the renderer pulled a database
driver in behind it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fixtures.assertions import citation, stored_assertion
from guardmem_core.memory.vector.base import embed_text

if TYPE_CHECKING:
    from guardmem_core.schemas.base import ObjectValue


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
        assert embed_text(stored_assertion(obj=obj)) == expected

    def test_the_subject_is_absent(self) -> None:
        """`subject_id` is a resolved `EntityId` - a UUID, semantically empty.

        Stated as a test because the alternative is tempting and wrong: joining
        the entity's canonical name in would make the vector depend on a row
        this store does not own and cannot re-embed when it changes.
        """
        assert "e-1" not in embed_text(stored_assertion())

    def test_corroboration_does_not_change_the_text(self) -> None:
        """The same fact embeds the same however many sources it has.

        `provenance` is a list, so embedding `verbatim` would make the vector a
        function of how often a fact happened to be said. That is exactly the
        axis `MEMORY_ENGINE.md` §3.2 gives to `S_cor`, and retrieval must not
        double-count it.
        """
        once = stored_assertion(provenance=[citation()])
        thrice = stored_assertion(provenance=[citation(), citation(span=(40, 61)), citation()])

        assert embed_text(once) == embed_text(thrice)
