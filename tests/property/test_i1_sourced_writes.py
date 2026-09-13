"""Invariant I1: nothing reaches the pipeline without a span.  S2.3

S2.3's DONE WHEN: "property test — for any candidate with no matching substring,
the pipeline emits `REJECT(UNSOURCED)` and never a stored assertion. This is
invariant I1 in `RULES.md`."

**What that can honestly assert today, and what it cannot.** `RULES.md` states
I1 as "every AUTO_WRITE assertion has a non-null `source_span`". Neither half of
that exists yet: `Decision` is a `StrEnum` with no `decide()` behind it until
S5.4, and there is no store until S3.2. So the literal form of the assertion has
to wait.

What exists is the boundary where the rule is actually enforced, and it is
enforced by construction rather than by policy: `MemoryCandidate` requires a
`Provenance`, `Provenance` requires a span, and `link_span` is the only thing
that produces one. An unsourced fact therefore cannot become a candidate - there
is no code path that would let it. These properties pin that:

- a claim the source does not contain never yields a candidate,
- every candidate that *is* produced carries a span that slices back to its own
  verbatim,
- and nothing is silently lost: candidates plus unsourced drops always equal the
  facts the model proposed.

The last is the one worth having. Rejecting an unsourced fact and forgetting to
count it would satisfy the first two while making the anti-confabulation rule
invisible in the funnel, which is the failure ADR-0006 exists to prevent.

`hypothesis` drives a sync function that calls `asyncio.run`, rather than an
async test. `@given` has no way to await a coroutine, and an event loop per
example is cheap here because nothing in this path does I/O - the LLM is a fake
and the prompt loader is cached.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fixtures.extraction import CONTENT, CONTEXT, ONTOLOGY, response
from fixtures.fakes import FakeLLM
from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.l1_extract.extractor import extract
from guardmem_core.pipeline.l1_extract.span_linker import link_span
from guardmem_core.schemas import ExtractionResult

# `RULES.md` §5: "property | hypothesis on pipeline invariants | must hold for
# 500 examples".
_EXAMPLES = 500

# Text drawn from a small alphabet, so that generated claims collide with the
# source often enough to exercise *both* branches. Drawn from unicode at random,
# every claim would be unsourced and the "a real span slices back" property
# would never fire - a suite that passes without testing anything.
_FRAGMENTS = st.text(alphabet="abc ", min_size=0, max_size=12)

# Real substrings of the source, which must always be found.
_SUBSTRINGS = st.builds(
    lambda start, length: CONTENT[start : start + length],
    st.integers(min_value=0, max_value=len(CONTENT) - 1),
    st.integers(min_value=1, max_value=40),
).filter(lambda text: bool(text.strip()))


def _fact(verbatim: str) -> dict[str, object]:
    return {
        "subject": "patient:8812",
        "predicate": "allergy",
        "object": "penicillin",
        "verbatim": verbatim,
    }


def _run(verbatims: Sequence[str]) -> ExtractionResult:
    """Extract with a canonical sample proposing exactly these claims."""
    body = json.dumps({"facts": [_fact(v) for v in verbatims]})
    llm = FakeLLM(responses=[response(body), response(body, body)])
    return asyncio.run(
        extract(
            CONTENT,
            llm,
            context=CONTEXT,
            ontology_yaml=ONTOLOGY,
            k=3,
            tier=Tier.FAST,
        )
    )


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(verbatim=_FRAGMENTS)
def test_a_span_is_returned_only_when_the_source_supports_it(verbatim: str) -> None:
    """`link_span` is total: a match always slices back, or there is no match."""
    match = link_span(verbatim, CONTENT)
    if match is None:
        return
    start, end = match.span
    assert 0 <= start < end <= len(CONTENT)
    assert CONTENT[start:end] == match.text
    assert match.text.strip(), "a span of whitespace quotes nothing"
    assert 0.0 <= match.alignment <= 1.0


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(verbatim=_SUBSTRINGS)
def test_text_that_is_in_the_source_is_always_found(verbatim: str) -> None:
    """The rule may only reject what is absent, never what is present.

    The converse of I1, and the reason it matters: a linker that rejected real
    citations would show up as lost recall rather than as a bug, because the
    drop is counted the same way a confabulation is.
    """
    match = link_span(verbatim, CONTENT)
    assert match is not None, f"{verbatim!r} is a substring of the source"
    assert match.text == verbatim
    assert match.alignment == 1.0


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(verbatims=st.lists(_FRAGMENTS, max_size=4))
def test_no_candidate_exists_without_a_span_that_resolves(verbatims: list[str]) -> None:
    """I1 at the boundary where it is actually enforced.

    Every candidate carries a span, and that span quotes the candidate's own
    verbatim out of the source - by construction, since ADR-0007 makes the
    stored verbatim *be* the source text.
    """
    result = _run(verbatims)
    for candidate in result.candidates:
        start, end = candidate.provenance.source_span
        assert CONTENT[start:end] == candidate.provenance.verbatim
        assert start < end


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(verbatims=st.lists(_FRAGMENTS, max_size=4))
def test_nothing_is_lost_between_the_model_and_the_result(verbatims: list[str]) -> None:
    """Candidates plus unsourced drops always equal what the model proposed.

    The conservation law. Rejecting an unsourced fact and forgetting to count it
    would satisfy every other property here while making §1.3's rule invisible
    in the funnel - which is precisely what ADR-0006 added the counter for.
    """
    result = _run(verbatims)
    assert len(result.candidates) + result.dropped_unsourced == len(verbatims)
    assert len(result.samples[0]) == len(verbatims), "the raw sample keeps the drops"


@settings(max_examples=_EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(verbatims=st.lists(_FRAGMENTS, min_size=1, max_size=4))
def test_claims_the_source_cannot_support_yield_nothing(verbatims: list[str]) -> None:
    """The confabulation case, stated directly.

    Filtered to claims `link_span` rejects, so this asserts the thing S2.3's
    DONE WHEN asks for: a candidate with no matching substring never becomes a
    stored assertion. It cannot even become a candidate.
    """
    unsourced = [v for v in verbatims if link_span(v, CONTENT) is None]
    if not unsourced:
        return
    result = _run(unsourced)
    assert result.candidates == []
    assert result.dropped_unsourced == len(unsourced)
