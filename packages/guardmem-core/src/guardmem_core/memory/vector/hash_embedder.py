"""A deterministic, offline `Embedder`.  BUILD_NOTEBOOK.md S3.2, S3.6

The only `Embedder` implementation in the package until S9.1 wires a provider,
and the one three separate callers were each about to write for themselves:
`FakeEmbedder` in the unit suite, the S3.6 seed, and eventually the eval
harness. They must agree - a test that proves the store embeds the right text,
and a seed whose vectors are supposed to be reproducible, are the same claim
about the same algorithm - so the algorithm lives once, here.

**It is not a model and must never be mistaken for one.** It hashes text and
expands the digest into a unit vector, which buys exactly two properties:
identical text embeds identically, so a search for a known assertion's own text
finds it; and different text lands somewhere effectively unrelated, so "nearest"
is not accidentally everything. *Semantic* similarity is not modelled at all.

That distinction is load-bearing rather than pedantic. `RULES.md` §5 puts
retrieval quality in the nightly eval suite against a labelled corpus, and any
number measured against these vectors - recall@k, a cosine threshold, a
reranking win - would be a measurement of SHA-256. Shipped in the package rather
than hidden in `tests/` precisely so that warning travels with the code the seed
and the dev stack actually run.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from guardmem_core.memory.vector.rowmap import EMBEDDING_DIM

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["HashEmbedder"]

# One `>i` is four bytes and one digest is thirty-two, so eight dimensions come
# out of each. The `+ 1` covers a `dim` that is not a multiple of eight.
_FLOATS_PER_DIGEST = 8

# `struct.unpack_from(">i", ...)` yields a signed 32-bit int; dividing by 2**31
# maps it into [-1, 1) before normalisation.
_INT32_SCALE = 2**31


@dataclass(slots=True)
class HashEmbedder:
    """Hash text into a unit vector of `dim` floats.

    Attributes:
        dim: Vector length. Defaults to the 1024 `assertion.embedding` declares,
            so `PgVectorStore`'s dimension check passes; a test that wants to
            see that check fire sets it to something else.
        texts: Every text this was asked to embed, flattened, in call order.
            What a test asserts against to prove the store embeds the
            *assertion* and not, say, its id. Recording is free and a caller
            that does not care can ignore it.
    """

    dim: int = EMBEDDING_DIM
    texts: list[str] = field(default_factory=list)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one deterministic unit vector per text, in input order.

        Args:
            texts: What to embed.

        Returns:
            One vector per input, each of length `dim`.

        `async` because the protocol is, not because anything here waits. A real
        provider is a network call; this is a hash, and routing it through
        `anyio.to_thread` as `RULES.md` §2.2 asks for CPU-bound work would cost
        more than the hash does.
        """
        self.texts.extend(texts)
        return [self.vector(text) for text in texts]

    def vector(self, text: str) -> list[float]:
        """Expand a digest of `text` into `dim` normalised floats.

        Args:
            text: The string to embed.

        Returns:
            A unit vector, identical for identical input on every platform and
            every run - the digest is over UTF-8 bytes and the arithmetic is
            fixed-width, so nothing here depends on hash randomisation the way
            `hash()` would.

        Normalised because `assertion_hnsw` indexes `vector_cosine_ops`: with
        unit vectors, cosine distance and ordering by it behave the way a test
        reading `ORDER BY distance` expects, and an all-zero vector - which an
        unnormalised scheme can produce - has no cosine distance at all.
        """
        raw = b"".join(
            hashlib.sha256(f"{index}:{text}".encode()).digest()
            for index in range(self.dim // _FLOATS_PER_DIGEST + 1)
        )
        values = [
            struct.unpack_from(">i", raw, offset * 4)[0] / _INT32_SCALE
            for offset in range(self.dim)
        ]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]
