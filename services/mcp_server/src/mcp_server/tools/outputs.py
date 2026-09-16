"""What the tools promise to return.  BUILD_NOTEBOOK.md S7.3

S6.2 left these out and said why, and named this step as the place they arrive:
"§2.1-§2.4 publish *example* results rather than schemas, so writing them here
means inventing a contract the spec of record does not state [...] **S7.3 is the
step that owns tool contract tests** [...] and it is the right place to add
them - alongside the check that they match what the handlers actually return."

That last clause is what makes these honest rather than invented. Every schema
below was read off the handler that produces it, and
`tests/contract/test_tool_schemas.py` validates real handler output against
each one - so a schema cannot drift from its handler without a test failing.
They are a transcription of an existing contract, not a new one.

**Declaring an `outputSchema` is not decoration: the MCP client validates
against it.** `mcp/client/session.py` compiles one validator per tool from the
`tools/list` response and checks every `structuredContent` it receives. So a
handler that starts returning a different shape does not quietly hand a client
something unexpected - the client raises. That is the guarantee worth having,
and it is also the reason these must describe reality exactly.

**`additionalProperties` is left open, deliberately, and the tests close it
instead.** A published schema that forbade unknown keys would turn *adding* a
field into a client-side failure for every existing client - which is the
opposite of how a wire format should evolve, and would make a additive change a
breaking one. Drift still has to fail, so the contract tests assert the exact
key set rather than the schema doing it. Loose on the wire, strict in CI.

**`memory.commit` has no schema here and that is the same argument S6.2 made.**
It declines in this build - `run_commit` raises `ToolRefusedError` before it
reaches a pipeline - so it returns no `structuredContent` at all, and a schema
for a payload nothing produces is a contract nobody can check. It arrives with
the implementation. `test_tool_schemas.py` pins which tools are expected to
decline, so adding a payload without a schema fails there rather than at a
client.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["GET_ENTITY_OUTPUT", "OUTPUT_SCHEMAS", "PROPOSE_OUTPUT", "SEARCH_OUTPUT"]

# One believed assertion, as `search.assertion_view` renders it. Shared because
# `memory.get_entity` renders the same object - §2.4 returns "its live
# assertions grouped by predicate", and an agent that learned to read a search
# hit should not have to learn a second shape for the same thing.
_ASSERTION: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "assertion_id",
        "subject",
        "predicate",
        "object",
        "confidence",
        "valid_from",
        "valid_to",
        "corroboration",
        "provenance",
    ],
    "properties": {
        "assertion_id": {"type": "string"},
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        # No type constraint: `ObjectValue` is a string, a number, a boolean or
        # a structured object, because a predicate's object type is the
        # ontology's to declare. Naming them here would put the vocabulary in
        # two places, which is the decision §2.3's own schema already makes.
        "object": {},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "valid_from": {"type": "string", "format": "date-time"},
        # Nullable rather than absent: `None` is the statement "still believed",
        # and omitting the key would make "live" and "the server did not say"
        # indistinguishable.
        "valid_to": {"type": ["string", "null"], "format": "date-time"},
        "corroboration": {"type": "integer", "minimum": 1},
        "provenance": {
            "type": "array",
            # `minItems: 1` is `RULES.md` non-negotiable #1 on the wire: no
            # unsourced write exists, so no assertion can be returned without a
            # citation. A client can rely on it rather than defending against it.
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source_hash", "span", "tier", "verbatim"],
                "properties": {
                    "source_hash": {"type": "string"},
                    "span": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "tier": {"type": "string"},
                    "verbatim": {"type": "string"},
                },
            },
        },
    },
}

# What `search` reports as retired. A different shape from `_ASSERTION` on
# purpose: §2.1's `excluded` answers "why is this not here", so it carries the
# reason and the moment rather than the confidence and the citations.
_EXCLUDED: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["assertion_id", "predicate", "object", "reason", "at"],
    "properties": {
        "assertion_id": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {},
        "reason": {"type": "string"},
        "at": {"type": ["string", "null"], "format": "date-time"},
    },
}

SEARCH_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["assertions", "excluded", "tokens_used", "trace_id"],
    "properties": {
        "assertions": {"type": "array", "items": _ASSERTION},
        "excluded": {"type": "array", "items": _EXCLUDED},
        "tokens_used": {"type": "integer", "minimum": 0},
        "trace_id": {"type": "string"},
    },
}

# One `ASSERTS` edge, as `get_entity._edge_view` renders it. Close to
# `_ASSERTION` and not the same: an edge carries no provenance and no
# corroboration, because the graph materialises the *relationship* and the
# citations stay on the assertion row in Postgres.
_EDGE: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "assertion_id",
        "subject",
        "predicate",
        "object",
        "confidence",
        "valid_from",
        "valid_to",
    ],
    "properties": {
        "assertion_id": {"type": "string"},
        "subject": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "valid_from": {"type": "string", "format": "date-time"},
        "valid_to": {"type": ["string", "null"], "format": "date-time"},
    },
}

GET_ENTITY_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "required": ["entity_id", "namespace", "as_of", "assertions", "neighbors", "graph_backed"],
    "properties": {
        "entity_id": {"type": "string"},
        "namespace": {"type": "string"},
        "as_of": {"type": ["string", "null"], "format": "date-time"},
        # Keyed by predicate - §2.4's "live assertions grouped by predicate" -
        # so the property names are the ontology's and cannot be enumerated
        # here without copying the vocabulary.
        "assertions": {"type": "object", "additionalProperties": {"type": "array"}},
        "neighbors": {"type": "array", "items": _EDGE},
        # Whether the graph behind `neighbors` survives this process. Published
        # because an empty list from a volatile graph is a limitation rather
        # than a finding, and a caller has no other way to tell - which matters,
        # since that list feeds `MEMORY_ENGINE.md` §3.3's blast-radius score.
        "graph_backed": {"type": "boolean"},
    },
}

# One governed candidate. `assertion_id` and `not_applied` are mutually
# exclusive and neither is required: ADR-0010's applier writes a row for some
# decisions and not others, so a candidate carries whichever of the two is true
# of it. A client reads "did this become a row?" by asking which key is present.
_CANDIDATE: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "candidate_id",
        "predicate",
        "object",
        "decision",
        "confidence",
        "risk",
        "reason_codes",
    ],
    "properties": {
        "candidate_id": {"type": "string"},
        "predicate": {"type": "string"},
        "object": {},
        "decision": {"enum": ["auto_write", "hitl_review", "reject", "escalate"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "risk": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_codes": {"type": "array", "items": {"type": "string"}},
        "assertion_id": {"type": "string"},
        "not_applied": {"type": "string"},
    },
}

PROPOSE_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "trace_id",
        "status",
        "candidates",
        "dropped_noise",
        "quarantined",
        "applied",
        "failed",
    ],
    "properties": {
        "trace_id": {"type": "string"},
        "status": {"type": "string"},
        "candidates": {"type": "array", "items": _CANDIDATE},
        "dropped_noise": {"type": "integer", "minimum": 0},
        "quarantined": {"type": "integer", "minimum": 0},
        "applied": {"type": "boolean"},
        # Carried beside the decisions rather than discarded: `run()` returns a
        # failed candidate alongside the successful ones so one bad provider
        # response does not lose nineteen good results, and §2.2 has no field
        # for them - omitting them would mean a fact the caller submitted
        # vanished from the answer. The stable `code` travels, never the
        # message, per `RULES.md` §1.5.
        "failed": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["candidate_id", "code"],
                "properties": {
                    "candidate_id": {"type": "string"},
                    "code": {"type": "string"},
                },
            },
        },
    },
}

# Tool name to output schema. `memory.commit` is absent - see the module
# docstring. Keyed off this by `test_tool_schemas.py`, so a tool that grows a
# payload without a schema, or a schema without a tool, fails there.
OUTPUT_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    "memory.search": SEARCH_OUTPUT,
    "memory.propose": PROPOSE_OUTPUT,
    "memory.get_entity": GET_ENTITY_OUTPUT,
}
