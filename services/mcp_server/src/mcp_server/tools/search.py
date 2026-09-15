"""`memory.search`.  MCP_INTEGRATION.md §2.1, BUILD_NOTEBOOK.md S6.2

The read half of S6.2's DONE WHEN - "find it via `memory.search` with its
provenance" - and the one core tool that is complete today. It needs a store, an
embedder and a namespace, and all three exist; nothing here touches the pipeline,
so none of S9.1's absent dependencies reach it.

**`excluded` is the field this tool exists to get right.** §2.1: "the agent
should be able to tell the difference between 'we have no record' and 'we
retired that record,' and so should anyone reading the transcript later."
`search` cannot supply it - invariant I6 means a retired assertion never appears
in *retrieval* results, and the store enforces that - so S6.2 added
`VectorStore.retired`, which answers the other question in its own field. Before
that `excluded` could only ever have been an empty array.

**Three fields §2.1 publishes are not returned, and each is named rather than
faked.** See `_assertion_view` and `_tokens`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

from guardmem_core.types import TraceId
from mcp_server.tools.arguments import (
    bounded_limit,
    min_confidence,
    parse_as_of,
    require_query,
    store_filters,
    token_budget,
)

if TYPE_CHECKING:
    from datetime import datetime

    from guardmem_core.schemas.entity import StoredAssertion
    from mcp_server.tools.context import ToolContext

__all__ = ["assertion_view", "run_search"]

# How many retired assertions to report beside the results. Not `limit`: the
# `excluded` array is a footnote to the answer rather than a second answer, and
# a namespace that has churned heavily would otherwise return fifty tombstones
# next to ten facts.
_EXCLUDED_LIMIT: Final = 10

# Characters per token, for the budget trim. **Deliberately pessimistic.**
#
# The real answer is S14.3's context packer. What is wrong with the obvious
# interim - `tiktoken` - is that it is OpenAI's tokenizer (so it is an estimate
# against a Claude model regardless) and that it *downloads* its BPE ranks on
# first use. A stdio server launched inside a desktop app, asked to make a
# network call before it can answer a search, is a failure mode strictly worse
# than an approximate count.
#
# Three is below the ~4 characters/token English averages, so this over-counts
# and trims early. That is the safe direction: under-counting overruns the
# budget the caller set, and the caller set it because something downstream
# breaks when it is exceeded.
_CHARS_PER_TOKEN: Final = 3


async def run_search(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Answer §2.1, against the live store.

    Args:
        context: Tenant, namespace and a bound store.
        arguments: The tool call's arguments.

    Returns:
        §2.1's result object: `assertions`, `excluded`, `tokens_used`, `trace_id`.

    Raises:
        ToolRefusedError: an argument is present with the wrong type or out of range.
        StoreUnavailable: Postgres is unreachable.

    The query is embedded rather than parsed. `HashEmbedder` is what the server
    wires today and it models **no semantics** - identical text retrieves
    identically and nothing else does - so ranking here is lexical identity, not
    meaning. That is stated on `ServerState.embedder` and it is the single
    biggest gap between this tool and its description; S9.1's real embedder is
    what closes it, and nothing measured against these vectors is a retrieval
    quality number.
    """
    query = require_query(arguments)
    limit = bounded_limit(arguments)
    floor = min_confidence(arguments)
    as_of = parse_as_of(arguments)
    filters = store_filters(arguments)

    embedding = (await context.state.embedder.embed([query]))[0]
    hits = await _nearest(context, embedding, limit=limit, filters=filters, as_of=as_of)
    kept = [hit for hit in hits if hit.confidence >= floor][:limit]
    trimmed, tokens_used = _trim_to_budget(kept, token_budget(arguments))

    retired = await _retired_for(context, filters)
    return {
        "assertions": [assertion_view(a) for a in trimmed],
        "excluded": [_excluded_view(a) for a in retired],
        "tokens_used": tokens_used,
        "trace_id": _trace_id(context),
    }


async def _nearest(
    context: ToolContext,
    embedding: list[float],
    *,
    limit: int,
    filters: dict[str, object],
    as_of: datetime | None,
) -> list[StoredAssertion]:
    """Run the vector search, once per requested predicate.

    Args:
        context: Tenant, namespace and a bound store.
        embedding: The query vector, embedded **once** by the caller. Not
            re-derived per predicate: it does not depend on the filter, and
            embedding inside the loop would be one provider call per predicate
            the moment `HashEmbedder` is replaced by something that bills.
        limit: `k` for each query.
        filters: Store filters. Read, never mutated - `run_search` reuses the
            same dict for the `excluded` lookup, and an earlier version of this
            function popped `predicate` out of it, which silently un-scoped that
            second query.
        as_of: Point-in-time, or `None` for live.

    Returns:
        The hits, de-duplicated by assertion id, in the order the queries
        returned them.

    §2.1's `predicates` is an array and `VectorStore.search` takes a single
    `predicate` filter, so a multi-predicate query is several searches merged.
    The alternative - one unfiltered search, then discard the non-matching hits -
    is what the shape suggests and it silently **under-returns**: `k` is applied
    by the database before the filter, so asking for ten allergies among a
    thousand facts can return zero while the facts exist. Several bounded
    queries are correct; the list is small by construction, since it names
    predicates a caller cares about.
    """
    requested = filters.get("predicate")
    wanted: list[object] = list(requested) if isinstance(requested, list) else [None]
    base = {key: value for key, value in filters.items() if key != "predicate"}
    merged: dict[str, StoredAssertion] = {}
    for predicate in wanted:
        scoped = base if predicate is None else {**base, "predicate": predicate}
        hits = await context.store.search(
            namespace=context.namespace,
            embedding=embedding,
            k=limit,
            filters=scoped,
            as_of=as_of,
        )
        for hit in hits:
            merged.setdefault(hit.assertion.assertion_id, hit.assertion)
    return list(merged.values())


async def _retired_for(context: ToolContext, filters: dict[str, object]) -> list[StoredAssertion]:
    """What this query's subject matter has stopped being believed.

    Args:
        context: Tenant, namespace and a bound store.
        filters: The caller's own scoping, read exactly as `_nearest` reads it.

    Returns:
        Up to `_EXCLUDED_LIMIT` retired assertions, de-duplicated by id.

    **Scoped by predicate, and getting that wrong is what this docstring is
    for.** The first version asked one unscoped question - "what has this
    namespace retired lately?" - on the argument that `excluded` is a footnote
    and one query is cheaper than several. Driving it against the seeded demo
    tenant showed what that actually produces: a search for `allergy` came back
    with three allergies and "2 retired and excluded", and the two were a
    `home_address` and a `preferred_pharmacy`. An agent reading that has been
    told its allergy query had retired matches, which is false, and §2.1's whole
    reason for publishing the field is to make "we retired that record"
    *trustworthy*. A footnote that is usually wrong is worse than no footnote.

    So the predicate loop is the same shape as `_nearest`'s, for the same
    reason: the store's filter is one predicate, the argument is a list, and the
    list is small because it names predicates a caller cares about. With no
    predicates given the question genuinely is "what has this namespace retired
    lately?" and one unscoped query is the right answer to it.
    """
    requested = filters.get("predicate")
    wanted: list[object] = list(requested) if isinstance(requested, list) else [None]
    base = {key: value for key, value in filters.items() if key != "predicate"}
    merged: dict[str, StoredAssertion] = {}
    for predicate in wanted:
        scoped = base if predicate is None else {**base, "predicate": predicate}
        for assertion in await context.store.retired(
            namespace=context.namespace, filters=scoped, limit=_EXCLUDED_LIMIT
        ):
            merged.setdefault(assertion.assertion_id, assertion)
    return list(merged.values())[:_EXCLUDED_LIMIT]


def assertion_view(assertion: StoredAssertion) -> dict[str, Any]:
    """One assertion in §2.1's result shape.

    Public because `memory.get_entity` renders the same thing: §2.4 returns "its
    live assertions grouped by predicate", and an agent that has learned to read
    a search hit should not have to learn a second shape for the same object. It
    also means a change to the provenance rendering cannot land in one tool and
    not the other.

    **`reviewed_by` and `reviewed_at` are absent, and that is not an omission in
    this function.** §2.1's example carries them; `StoredAssertion` has no such
    fields and nothing writes them, because the reviewer identity lives on a
    `ReviewDecision` (`schemas/review.py`) and the queue that produces one is
    **S18.3**. Emitting `null` for both would be a claim that the fact was not
    reviewed, which is different from "this build cannot tell you".

    **`provenance` is a list where §2.1's example shows one object.** The model
    carries every span supporting the fact and `corroboration_count` counts the
    independent sources among them, so flattening to one would discard exactly
    what the neighbouring field reports. §2.1's blocks are examples rather than
    schemas - only the `inputSchema` is normative - so the model wins.
    """
    return {
        "assertion_id": assertion.assertion_id,
        "subject": assertion.subject_id,
        "predicate": assertion.predicate,
        "object": assertion.object,
        "confidence": assertion.confidence,
        "valid_from": assertion.valid_from.isoformat(),
        "valid_to": assertion.valid_to.isoformat() if assertion.valid_to else None,
        "corroboration": assertion.corroboration_count,
        "provenance": [
            {
                "source_hash": citation.source_hash,
                "span": list(citation.source_span),
                "tier": citation.source_tier.value,
                "verbatim": citation.verbatim,
            }
            for citation in assertion.provenance
        ],
    }


def _excluded_view(assertion: StoredAssertion) -> dict[str, Any]:
    """One retired assertion, in §2.1's `excluded` shape.

    `reason` follows the example's own wording - `"superseded_by a_7f21"` - and
    falls back to `"retired"` for a row with `valid_to` set and no successor,
    which is what a retraction looks like.
    """
    successor = assertion.superseded_by
    return {
        "assertion_id": assertion.assertion_id,
        "predicate": assertion.predicate,
        "object": assertion.object,
        "reason": f"superseded_by {successor}" if successor else "retired",
        "at": assertion.valid_to.isoformat() if assertion.valid_to else None,
    }


def _trim_to_budget(
    assertions: list[StoredAssertion], budget: int
) -> tuple[list[StoredAssertion], int]:
    """Drop from the tail until the estimate fits, and report what it cost.

    Returns:
        The kept assertions and the estimated tokens they occupy.

    Trims from the end because the list is already nearest-first: the budget
    should cost the least relevant fact, not an arbitrary one. Always keeps at
    least nothing - an empty result for an absurdly small budget is the honest
    answer, and is distinguishable from "no record" by `excluded` and by
    `tokens_used` being zero.
    """
    kept: list[StoredAssertion] = []
    used = 0
    for assertion in assertions:
        cost = _tokens(assertion)
        if used + cost > budget:
            break
        kept.append(assertion)
        used += cost
    return kept, used


def _tokens(assertion: StoredAssertion) -> int:
    """Estimate what one assertion costs in the caller's context.

    An estimate, and the result field is `tokens_used` because §2.1 names it
    that. It is computed over the *rendered* fields rather than a model dump, so
    it tracks what `_assertion_view` actually emits - the verbatim spans are the
    bulk of it, and a count that ignored them would under-report by most of the
    payload. See `_CHARS_PER_TOKEN` on why this deliberately over-counts, and
    S14.3 for the packer that replaces it.
    """
    rendered = f"{assertion.predicate}{assertion.object}" + "".join(
        citation.verbatim for citation in assertion.provenance
    )
    return max(1, len(rendered) // _CHARS_PER_TOKEN)


def _trace_id(context: ToolContext) -> TraceId:
    """A trace for this call.

    §2.1 returns one, and a read is worth tracing for the same reason a write
    is: "why did the agent answer that?" is answered by what it retrieved. Minted
    here because nothing upstream supplies one - the gateway that would assign a
    request id is S8.1.
    """
    del context  # Nothing about the caller distinguishes one read from another yet.
    return TraceId(f"tr_{uuid4().hex[:12]}")
