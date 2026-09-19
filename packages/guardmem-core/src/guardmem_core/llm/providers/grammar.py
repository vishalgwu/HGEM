"""Ollama's grammar compiler cannot take every JSON Schema keyword.

Split out of `ollama_client.py` on 2026-09-19, when distinguishing a timeout
from an unreachable server pushed that module past `RULES.md` §2.4's 400-line
cap. The seam was already named: the client's own docstring calls this function
"that seam", and `test_ollama_grammar.py` had its own file before this did.

Pure schema rewriting - no transport, no client, no settings.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["grammar_safe"]

# Ollama's grammar compiler rejects repetition bounds at or above this, so they
# are dropped rather than compiled. See `grammar_safe` for why dropping is sound.
GRAMMAR_REPETITION_LIMIT: Final = 2000

REPETITION_KEYWORDS: Final = frozenset({"maxItems", "maxLength", "minItems", "minLength"})


def grammar_safe(schema: object) -> Any:
    """Drop the schema keywords Ollama's grammar compiler cannot take.

    Args:
        schema: A JSON Schema, or any fragment of one. Not mutated - pydantic
            caches `model_json_schema()` and hands back the same object every
            time, so editing it in place would corrupt the schema for every
            other caller in the process, including the validation this exists to
            preserve.

    Returns:
        A copy with every `maxLength`, `minLength`, `maxItems` and `minItems`
        of `GRAMMAR_REPETITION_LIMIT` or more removed, at every depth.
        Everything else is carried through unchanged, including the same
        keywords at values the compiler accepts.

    **Why dropping is sound, and why clamping is not.** The grammar constrains
    *generation*; `RULES.md` §3 makes the caller validate the reply against the
    full schema, and it still does - `ExtractionBatch.model_validate_json` will
    reject a 2001-character `verbatim` whether or not the grammar could have
    prevented it. So the constraint is not lost, it moves from prevention to
    detection for the one field the compiler could not express.

    Clamping to 1999 instead was considered and is wrong. It would *forbid* a
    legitimate 2500-character value under a `maxLength: 5000` schema - the model
    could not produce it, and nothing would say why. Over-constraining silently
    is worse than under-constraining loudly.

    Recursive over lists as well as objects because these keywords live inside
    `$defs`, `items`, `anyOf` arms and `properties` - `ExtractedFact.verbatim`
    is two levels down a `$ref`, which is exactly why a top-level pass would
    have found nothing and looked like it worked.
    """
    if isinstance(schema, dict):
        return {
            key: grammar_safe(value)
            for key, value in schema.items()
            if not (
                key in REPETITION_KEYWORDS
                and isinstance(value, int)
                and value >= GRAMMAR_REPETITION_LIMIT
            )
        }
    if isinstance(schema, list):
        return [grammar_safe(item) for item in schema]
    return schema
