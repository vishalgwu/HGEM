"""`guardmem://memory/{namespace}`.  MCP_INTEGRATION.md §3, BUILD_NOTEBOOK.md S6.4

S6.4's DONE WHEN: "attaching the namespace resource in Claude Desktop shows the
current believed state." This is that resource, and it is the one a human reads
rather than a model parses - so it is `text/markdown` per §3, and the rendering
choices below are about a person scanning a panel, not about token efficiency.

**Why this is a snapshot and not a listing, which is a real limitation and not a
missing `SELECT`.** `VectorStore` has two read methods: `search`, which returns
the `k` nearest live assertions to a query vector, and `retired`. There is no
"give me everything in this namespace", and adding one is a protocol change -
every backend would owe it, including ones where an unbounded scan is the wrong
thing to offer at all. So the snapshot asks `search` for `_SNAPSHOT_LIMIT` rows
against a **deterministic** vector and reports what comes back.

Two consequences, both stated in the body rather than left for a reader to
discover:

- **When the namespace holds fewer than `_SNAPSHOT_LIMIT` live assertions the
  snapshot is complete**, because `k` is a ceiling. That is the normal case for
  a patient or an account, which is what a namespace is.
- **When it holds more, the snapshot is a subset chosen by cosine distance to a
  vector that means nothing**, because `HashEmbedder` models no semantics. It is
  not the "most relevant" or the "most recent" subset - it is arbitrary but
  stable. The body says so and gives the count, so nobody reads a truncated
  panel as the whole of what is believed.

The query vector is the namespace's own name. Any fixed vector would do; using
the namespace makes the choice legible and keeps two reads of the same resource
identical, which matters for a panel a client may refresh.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import mcp_types as types

from mcp_server.resources.uris import SCHEME, uri_for
from mcp_server.tools.search import assertion_view

if TYPE_CHECKING:
    from guardmem_core.schemas.entity import StoredAssertion
    from mcp_server.tools.context import ToolContext

__all__ = ["MEMORY_TEMPLATE", "read_memory"]

MEMORY_TEMPLATE: Final = types.ResourceTemplate(
    uri_template=f"{SCHEME}://memory/{{namespace}}",
    name="Believed memory",
    description=(
        "Everything GuardMem currently believes about one namespace, with the "
        "confidence and the verbatim source span behind each fact, and what has "
        "been retired. Read it before answering from memory."
    ),
    mime_type="text/markdown",
)

# How many live assertions one snapshot shows. A namespace is one subject - a
# patient, an account - so a hundred is generous for the shape this models, and
# it is a ceiling rather than a page: there is no cursor here, because a
# resource has no way to ask for the next one. `memory.search` is the tool for
# a narrower question.
_SNAPSHOT_LIMIT: Final = 100

# How many retired assertions to show beneath them. Smaller than the live list
# on purpose: §3 calls this a "believed-state snapshot", and the tombstones are
# context for why something is missing rather than the subject of the panel.
_RETIRED_LIMIT: Final = 25


async def read_memory(context: ToolContext, namespace: str) -> types.TextResourceContents:
    """Render the believed state of one namespace as Markdown.

    Args:
        context: Tenant, namespace and a bound store. The namespace on the
            context is the one parsed from the URI - `read_resource` passes it
            through `context_for` - so this argument and `context.namespace`
            agree by construction.
        namespace: From the URI, for the heading and the echoed `uri`.

    Returns:
        `text/markdown`: a heading, the live assertions grouped by predicate,
        then what has been retired.

    Raises:
        StoreUnavailable: Postgres is unreachable.

    An empty namespace renders a sentence saying so rather than an empty
    document. "GuardMem believes nothing about this namespace yet" is a fact a
    reader can act on; a blank panel is one they will read as a broken
    attachment, and then not trust the next one either.
    """
    embedding = (await context.state.embedder.embed([namespace]))[0]
    live = await context.store.search(
        namespace=context.namespace,
        embedding=embedding,
        k=_SNAPSHOT_LIMIT,
        filters={},
        as_of=None,
    )
    retired = await context.store.retired(
        namespace=context.namespace, filters={}, limit=_RETIRED_LIMIT
    )
    text = _render(namespace, [hit.assertion for hit in live], retired)
    return types.TextResourceContents(
        uri=uri_for("memory", namespace), mime_type="text/markdown", text=text
    )


def _render(namespace: str, live: list[StoredAssertion], retired: list[StoredAssertion]) -> str:
    """Lay the two lists out for a person.

    Returns:
        The whole document. Grouped by predicate rather than listed flat,
        because a namespace's facts are read as "what do we know about X" and a
        predicate is the question each answer belongs to - three medications
        under one heading is scannable, and interleaved with an allergy and a
        pharmacy it is not.
    """
    lines = [f"# Believed memory - {namespace}", ""]
    if not live:
        lines += [
            "GuardMem believes nothing about this namespace yet.",
            "",
            "That is different from an error: the namespace is readable and holds "
            "no live assertions. A fact reaches here after `memory.propose` "
            "governs it **and** the outbox relay makes it visible.",
            "",
        ]
    else:
        lines += [_headline(live), ""]
        for predicate in sorted({a.predicate for a in live}):
            lines.append(f"## {predicate}")
            lines.append("")
            for assertion in sorted(live, key=lambda a: str(a.object)):
                if assertion.predicate == predicate:
                    lines += _bullet(assertion)
            lines.append("")
    lines += _retired_section(retired)
    return "\n".join(lines).rstrip() + "\n"


def _headline(live: list[StoredAssertion]) -> str:
    """The one line that says how much of the truth this document is.

    A reader who cannot tell a complete snapshot from a truncated one will treat
    both as complete, so the truncated case says the word.
    """
    if len(live) < _SNAPSHOT_LIMIT:
        return f"**{len(live)} live assertion(s).** This is the whole believed state."
    return (
        f"**Showing {_SNAPSHOT_LIMIT} live assertions, and there may be more.** "
        "This is a ceiling rather than a page - the subset is stable but "
        "arbitrary, because the embedder in this build models no semantics. Use "
        "`memory.search` with a predicate filter for a complete answer about one "
        "question."
    )


def _bullet(assertion: StoredAssertion) -> list[str]:
    """One fact, with the two things that make it governed rather than stored.

    The confidence and the verbatim span are not decoration: they are the
    difference between this panel and a notes file, and a reader who cannot see
    where a fact came from has no way to challenge it.
    """
    view = assertion_view(assertion)
    citation = view["provenance"][0] if view["provenance"] else None
    line = f"- **{view['object']}** - confidence {view['confidence']:.2f}"
    if view["corroboration"] > 1:
        line += f", corroborated {view['corroboration']}x"
    out = [line]
    if citation is not None:
        out.append(f'  - source ({citation["tier"]}): "{citation["verbatim"]}"')
    return out


def _retired_section(retired: list[StoredAssertion]) -> list[str]:
    """What has stopped being believed, and why that is on the same page.

    §2.1's reasoning for `excluded` applies to a human panel at least as
    strongly as to a tool result: "the agent should be able to tell the
    difference between 'we have no record' and 'we retired that record,' and so
    should anyone reading the transcript later." A snapshot that showed only
    live facts would answer a question about a missing one with silence.
    """
    if not retired:
        return []
    lines = ["## Retired", "", "No longer believed. Kept, never deleted - ADR-0002.", ""]
    for assertion in retired:
        view = assertion_view(assertion)
        until = assertion.valid_to.date().isoformat() if assertion.valid_to else "unknown"
        lines.append(f"- ~~{view['predicate']}: {view['object']}~~ - retired {until}")
    lines.append("")
    return lines
