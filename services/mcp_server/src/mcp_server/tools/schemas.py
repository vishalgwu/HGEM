"""The four core tools, as `MCP_INTEGRATION.md` publishes them.  S6.2

S6.2's instruction is explicit: "JSON schemas come from MCP_INTEGRATION.md
sections 2.1-2.4 — copy them exactly, including the descriptions. **The
descriptions are prompt engineering, not documentation.**"

That last sentence is why this module exists as data rather than as strings
scattered through four handlers. A tool description is the only thing a model
reads before deciding whether to call it, so its wording is a product decision
owned by `MCP_INTEGRATION.md` and changing it is a behavioural change to every
agent using the server - closer to editing a prompt under `prompts/` than to
editing a docstring. Keeping all four in one file next to their source citation
makes a drift from that document visible in one diff.

**Two schemas needed a decision the document does not make, and both are
recorded rather than quietly resolved.**

§2.4 (`memory.get_entity`) is published as prose - "returns the graph view: an
entity, its live assertions grouped by predicate, and 1-hop neighbors with edge
confidences. Optional `as_of`" - with no JSON block. The schema below is that
sentence transcribed, with nothing added: `entity_id`, `namespace`, `as_of`.

§2.3 (`memory.commit`) types its `assertions` items as
`{"type": "object", "required": [...]}` with no `properties`, so the four
required keys are named and unconstrained. Copied as published; the *values* are
validated by the pipeline's own schema gate, which is where `MEMORY_ENGINE.md`
§2.1 puts that decision, and duplicating the ontology into a JSON Schema here
would put the predicate vocabulary in two places.

**`outputSchema` arrived at S7.3, for three of the four.** S6.2 left them out
because §2.1-§2.4 publish *example* results rather than schemas, so writing one
here would have meant inventing a contract the spec of record does not state -
in the file whose whole job is to copy it faithfully. S7.3 owns tool contract
tests, and `tools/outputs.py` resolves it the only honest way: each schema is
read off the handler that produces it, and the contract suite validates real
handler output against it, so it transcribes a contract rather than inventing
one. It is not decoration - the MCP *client* compiles a validator per tool and
checks every `structuredContent` it receives.

`memory.commit` still has none, and for S6.2's reason unchanged: it declines in
this build, so it returns no `structuredContent`, and a schema for a payload
nothing produces is a contract nobody can check.
"""

from __future__ import annotations

from typing import Final

import mcp_types as types

from mcp_server.tools.outputs import (
    GET_ENTITY_OUTPUT,
    PROPOSE_OUTPUT,
    SEARCH_OUTPUT,
)

__all__ = ["COMMIT", "GET_ENTITY", "PROPOSE", "SEARCH", "TOOLS", "TOOLS_BY_NAME"]

SEARCH: Final = types.Tool(
    name="memory.search",
    description=(
        "Search governed long-term memory. Returns only currently-believed, "
        "non-tombstoned assertions with provenance. Use this before answering "
        "anything that depends on facts about this subject."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language query."},
            "namespace": {
                "type": "string",
                "description": "Defaults to server-configured namespace.",
            },
            "subject": {"type": "string", "description": "Optional entity filter."},
            "predicates": {"type": "array", "items": {"type": "string"}},
            "as_of": {
                "type": "string",
                "format": "date-time",
                "description": "Point-in-time query: what was believed at this instant.",
            },
            "min_confidence": {"type": "number", "default": 0.6, "minimum": 0, "maximum": 1},
            "limit": {"type": "integer", "default": 10, "maximum": 50},
            "token_budget": {
                "type": "integer",
                "default": 1500,
                "description": "Context packer trims to fit.",
            },
        },
        "required": ["query"],
    },
    output_schema=SEARCH_OUTPUT,
)

PROPOSE: Final = types.Tool(
    name="memory.propose",
    description=(
        "Submit candidate facts for governance. Facts are NOT immediately "
        "stored — they are extracted, validated, scored, and either "
        "auto-written, queued for human review, or rejected. Returns a trace_id "
        "and per-candidate decisions. Prefer passing raw conversation text over "
        "pre-structured facts: the extractor needs source spans for provenance."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Raw turn(s), tool output, or document text.",
            },
            "namespace": {"type": "string"},
            "source_tier": {
                "enum": [
                    "trusted_system",
                    "verified_user",
                    "unverified_user",
                    "tool_output",
                    "retrieved_web",
                ],
                "default": "unverified_user",
            },
            "risk_hint": {"enum": ["low", "default", "high"], "default": "default"},
            "mode": {
                "enum": ["async", "strict"],
                "default": "async",
                "description": (
                    "strict blocks until decided (~450ms p95); async returns immediately."
                ),
            },
            "hints": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicates_of_interest": {"type": "array", "items": {"type": "string"}},
                },
            },
            "idempotency_key": {"type": "string"},
        },
        "required": ["content"],
    },
    output_schema=PROPOSE_OUTPUT,
)

COMMIT: Final = types.Tool(
    name="memory.commit",
    # §2.3 publishes no `description` field - it is described in prose above the
    # JSON block, and that prose is what an agent needs, so it is the
    # description. The last sentence is the load-bearing one: `commit` reads
    # like a bypass and is not.
    description=(
        "For agents that already have structured, high-trust facts (e.g. a "
        "signed EHR payload). Still passes the full pipeline — commit is not a "
        "bypass, it just skips L1 extraction and requires the caller to supply "
        "provenance explicitly. Missing or unverifiable provenance is rejected: "
        "there is no unsourced write path in this API."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "assertions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["subject", "predicate", "object", "provenance"],
                },
            },
            "namespace": {"type": "string"},
            "idempotency_key": {"type": "string"},
        },
        "required": ["assertions"],
    },
)

GET_ENTITY: Final = types.Tool(
    name="memory.get_entity",
    description=(
        "Returns the graph view: an entity, its live assertions grouped by "
        "predicate, and 1-hop neighbors with edge confidences. Optional as_of "
        "for point-in-time reconstruction."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string", "description": "The resolved entity to expand."},
            "namespace": {
                "type": "string",
                "description": "Defaults to server-configured namespace.",
            },
            "as_of": {
                "type": "string",
                "format": "date-time",
                "description": "Point-in-time reconstruction.",
            },
        },
        "required": ["entity_id"],
    },
    output_schema=GET_ENTITY_OUTPUT,
)

# Declaration order is S6.2's implementation order - search, propose, commit,
# get_entity - and it is the order a client lists them in. Not alphabetical:
# the step orders them by what depends on what, and a reader comparing this list
# against the notebook should see the same sequence.
TOOLS: Final = (SEARCH, PROPOSE, COMMIT, GET_ENTITY)

TOOLS_BY_NAME: Final = {tool.name: tool for tool in TOOLS}
