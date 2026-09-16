"""`guardmem://ontology/{namespace}`.  MCP_INTEGRATION.md §3, BUILD_NOTEBOOK.md S6.4

§3: "predicate schema (so agents extract the right shapes)". That parenthesis is
the whole purpose. `schema_gate.py` quarantines a candidate whose predicate is
not in the pack, and an agent that cannot see the vocabulary is guessing at it -
so this resource is the cheapest way to turn a quarantine into a correct
proposal, and it is the one an agent should attach *before* calling
`memory.propose` rather than after being refused.

**The URI is keyed by namespace and the server has one pack.** That is a real
gap and it is stated rather than papered over. `lifespan.DEFAULT_ONTOLOGY` binds
`clinical` for the whole process, because `PROJECT_TREE.md` names legal and
fintech packs that arrive at the step that needs them and a setting with one
legal value is a setting nobody can get wrong. §3 keys the URI by namespace
because in the finished system a tenant's namespaces may not share a pack. Until
that is true, every namespace answers with the installed pack and the body says
which one it is - so a reader who expected `legal` sees `clinical` at the top
rather than inferring it from the predicates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from mcp_server.resources.uris import SCHEME, uri_for

if TYPE_CHECKING:
    from mcp_server.tools.context import ToolContext

__all__ = ["ONTOLOGY_TEMPLATE", "read_ontology"]

ONTOLOGY_TEMPLATE: Final = types.ResourceTemplate(
    uri_template=f"{SCHEME}://ontology/{{namespace}}",
    name="Predicate schema",
    description=(
        "The predicates a namespace accepts, with object types, cardinality, "
        "impact, PII class and irreversibility. Read this before proposing "
        "facts: a predicate outside this vocabulary is quarantined by the "
        "schema gate, not stored."
    ),
    mime_type="text/yaml",
)


async def read_ontology(context: ToolContext, namespace: str) -> types.TextResourceContents:
    """Render the installed pack as the YAML the extraction prompt sees.

    Args:
        context: Tenant, namespace and a bound store. Only the loaded ontology
            is used.
        namespace: From the URI. Echoed in the header and otherwise unused
            today - see the module docstring.

    Returns:
        `text/yaml`, prefixed by a comment naming the pack and its version.

    `as_prompt_yaml()` rather than a bespoke rendering, and that is the point of
    the resource: it is **byte-for-byte what `extract_memories/v1.md` is given**
    when the pipeline runs. An agent that formats a candidate against this is
    formatting against the same description the extractor was, so a shape that
    reads as correct here is one the schema gate accepts. A second rendering
    would be free to drift, and the drift would show up as quarantines nobody
    could explain.

    Not `async` by necessity - `load_ontology` is cached and this touches no
    store - but declared so because every reader shares one signature, and a
    dispatch table of mixed sync and async callables is a `TypeError` waiting
    for the one entry nobody exercised.
    """
    ontology = context.state.ontology
    header = (
        f"# GuardMem predicate schema for namespace {namespace}\n"
        f"# pack: {ontology.name} v{ontology.version}\n"
        "#\n"
        "# This server binds one pack for every namespace it serves. If you "
        "expected a\n"
        "# different one, that is configuration rather than this namespace - see "
        "MCP_INTEGRATION.md §3.\n"
        "#\n"
        "# A predicate that is not below is quarantined by the schema gate, not "
        "stored.\n\n"
    )
    return types.TextResourceContents(
        uri=uri_for("ontology", namespace),
        mime_type="text/yaml",
        text=header + ontology.as_prompt_yaml(),
    )
