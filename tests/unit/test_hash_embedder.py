"""The deterministic `Embedder` two call sites depend on agreeing.

`HashEmbedder` exists because the algorithm had been written twice - once as
`HashEmbedder` in this suite, once inside the S3.6 seed, which could not import
`tests/`. Two implementations of "identical text embeds identically" that are
free to drift make the unit suite and the demo database quietly stop describing
the same system, which is the one thing a deterministic embedder is for.

So the properties below are the contract, not incidental behaviour. Each is
something a caller actually leans on: the store's dimension check, the search
that finds an assertion by its own text, and the `vector_cosine_ops` index that
has no answer for a zero vector.

What is deliberately **not** tested is semantic similarity, because it is
deliberately not implemented. `RULES.md` §5 puts retrieval quality in the
nightly eval suite against a labelled corpus; any assertion here about "related
text lands nearby" would be an assertion about SHA-256.
"""

from __future__ import annotations

import math

import pytest

from guardmem_core.memory.vector.hash_embedder import HashEmbedder
from guardmem_core.memory.vector.rowmap import EMBEDDING_DIM


class TestDeterminism:
    async def test_the_same_text_embeds_identically_across_instances(self) -> None:
        """The property the seed's reproducibility and the store's tests both
        rest on. Two instances, because a cache on one would hide a difference."""
        assert await HashEmbedder().embed(["allergy: penicillin"]) == await HashEmbedder().embed(
            ["allergy: penicillin"]
        )

    def test_it_does_not_depend_on_hash_randomisation(self) -> None:
        """`hash()` is salted per process; this digests UTF-8 bytes instead.

        A vector that changed between runs would make `make seed` produce a
        different database every time and no test would notice, because every
        test embeds inside the same process.
        """
        vector = HashEmbedder().vector("allergy: penicillin")

        assert vector[:3] == pytest.approx([-0.025797, -0.0022, 0.02941], abs=1e-6)

    async def test_different_text_lands_somewhere_unrelated(self) -> None:
        """Not a semantic claim - see the module docstring. It is the weaker
        property that "nearest" is not accidentally everything."""
        first, second = await HashEmbedder().embed(["allergy: penicillin", "employer: Northwind"])

        cosine = sum(a * b for a, b in zip(first, second, strict=True))
        assert abs(cosine) < 0.2


class TestTheShapeTheColumnDeclares:
    async def test_it_defaults_to_the_dimension_the_column_declares(self) -> None:
        """`assertion.embedding` is `VECTOR(1024)`, so the default has to match
        or every store write fails the dimension check."""
        embedded = await HashEmbedder().embed(["anything"])

        assert len(embedded[0]) == EMBEDDING_DIM

    def test_a_caller_can_ask_for_another_dimension(self) -> None:
        """How `test_pgvector_store.py` makes the store's dimension check fire."""
        assert len(HashEmbedder(dim=768).vector("anything")) == 768

    @pytest.mark.parametrize("dim", [1, 7, 8, 9, 1024])
    def test_every_vector_is_a_unit_vector(self, dim: int) -> None:
        """`assertion_hnsw` indexes `vector_cosine_ops`, and an all-zero vector
        has no cosine distance at all. The awkward sizes are the ones where the
        eight-floats-per-digest arithmetic could run short."""
        vector = HashEmbedder(dim=dim).vector("allergy: penicillin")

        assert len(vector) == dim
        assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)

    def test_the_empty_string_still_produces_a_usable_vector(self) -> None:
        """A degenerate input, answered rather than raised on: an embedder that
        raised here would turn an empty predicate into a store outage."""
        vector = HashEmbedder(dim=16).vector("")

        assert math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0)


class TestRecording:
    async def test_it_records_every_text_it_was_asked_for_in_order(self) -> None:
        """What proves the store embeds the *assertion* and not its id."""
        embedder = HashEmbedder()

        await embedder.embed(["first", "second"])
        await embedder.embed(["third"])

        assert embedder.texts == ["first", "second", "third"]

    async def test_two_instances_do_not_share_a_recording(self) -> None:
        """A mutable default on the dataclass field would make every embedder in
        the process append to one list - and the tests that assert on `texts`
        would start passing for the wrong reason."""
        first, second = HashEmbedder(), HashEmbedder()

        await first.embed(["mine"])

        assert second.texts == []
