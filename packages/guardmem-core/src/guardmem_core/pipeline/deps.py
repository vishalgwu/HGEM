"""What `run()` needs, and the three things nothing in this repository supplies.

S5.6's sketch is `async def run(proposal, deps)`, and this is `Deps`. Most of it
is the infrastructure protocols from S1.7 - an `LLMClient`, a `VectorStore`, a
`GraphStore`, an `Embedder` - plus the ontology and the thresholds the layers
read. Three are new, and they are new because composing the pipeline is what
finally showed that they are missing.

**`EntityResolver` was the largest gap in the build and now has a decision.**
`MemoryCandidate.subject` is a surface form - "Joan Ellery", as the speaker said
it - and `retrieve_incumbents` needs a resolved `EntityId`. Nothing turns one
into the other, and nothing in the design suite said how: entity resolution
appeared in exactly one line across every document, `ARCHITECTURE.md` §5's node
shape.

**ADR-0008 decides it: resolution binds, it does not match.** Three sources in
order - `hints.subject` when the caller names the entity, the namespace when it
is subject-bound (`<type>:<id>`, id derived as a pure function of tenant and
namespace), and otherwise a refusal. No name comparison happens anywhere, which
is what makes a false *merge* - one person's allergy on another person's record
- unreachable rather than merely unlikely. A default that used the surface form
as the id would have been worse than none, for the reason this docstring gave
before the ADR existed: it splits one patient across three spellings and every
incumbent lookup then returns nothing, which reads as "this is a novel fact" and
writes a duplicate.

**Still a `Protocol` with no implementation shipped, and `run()` still cannot be
called.** Two things the ADR requires and this file does not yet have: `resolve`
takes an `expected_type` (`PredicateSpec.subject` - the entity type, which is
`NOT NULL` on the `entity` row and which this signature cannot supply), and
something has to write that row before the assertion's foreign key will accept
it. Both are the implementation step; the decision they were waiting on is
made.

**`CandidateClassifier` is gone, and ADR-0009 is why.** It was built to supply
the three risk features §3.3 names and defines nowhere - `scope`, `pii_class`
and `irreversibility`. They turned out to be three problems rather than one, and
none of them was this protocol's:

- `scope` is read off the namespace prefix by `scope_of_namespace`, below, and
  always was.
- `pii_class` and `irreversibility` are predicate-level policy, and the ontology
  is already where predicate-level policy lives. `impact` is the same kind of
  judgement by the same people, feeding the same §3.3 score through the same
  `{0, .33, .66, 1}` shape, and it was a declared field the whole time. Nothing
  separated them except that one had been written down. They are now required
  `PredicateSpec` fields.

So the protocol had no implementation, no possible implementation that was not
"read the spec", and one consumer. `CandidateRisk` went with it: three field
reads do not need a model to group them.

**`entail` is S5.1's `EntailFn`, and `run()` needs it for two different
questions.** §3.1 clusters K samples by meaning, and §3.2's `S_src` asks whether
a candidate's own verbatim span entails its claim. Both are "does this text
entail that text". Injected, so the LID detector §3.1 names can back both
without this module knowing.

It now has a producer - `llm/entailment.py`'s `LLMEntailer` - which is the
first of these three gaps to close. **Binding it is not a one-line change**, and
the reason is in `entropy.py`: `EntailFn` is sync, `LLMEntailer` is async, and
the resolution S5.1 asked for is that a caller "precompute the pairs it needs
and pass a lookup". So `Deps.entail` stays what it is, and the orchestrator has
to assemble the pairs for a candidate and await one lookup before scoring it.
That is a change to `_score_and_decide`, not to this file, and it is the next
step rather than this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from guardmem_core.pipeline.l3_score.impact_features import Scope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from guardmem_core.llm.base import LLMClient
    from guardmem_core.llm.entailment import EntailmentPair
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder, VectorStore
    from guardmem_core.pipeline.l2_validate import NLIJudge
    from guardmem_core.pipeline.l3_score import ConfidenceWeights, EntailFn, RiskBetas
    from guardmem_core.schemas.ontology import Ontology
    from guardmem_core.schemas.verdict import Thresholds
    from guardmem_core.types import EntityId, Namespace, TenantId, TraceId

__all__ = [
    "Deps",
    "EntailLookup",
    "EntityResolver",
    "scope_of_namespace",
]


class EntailLookup(Protocol):
    """Scores a batch of entailment pairs and hands back the sync callable.

    **The shape `entropy.py` asked for.** `EntailFn` is a plain sync callable -
    what a local cross-encoder is - and S5.1 kept it that way so LID's detector
    could back it later. An async backend cannot satisfy that directly, and its
    own docstring says what to do instead: "precompute the pairs it needs and
    pass a lookup, because every comparison here is independent and none of them
    need to be sequential." This is that seam.

    `LLMEntailer.lookup` is the implementation. A Protocol rather than a
    `Callable` alias because `trace_id` is keyword-only, which `Callable` cannot
    express - and because the local cross-encoder that replaces it should be
    held to the same contract rather than to a structural accident.
    """

    async def __call__(self, pairs: Sequence[EntailmentPair], *, trace_id: TraceId) -> EntailFn:
        """Score `pairs` and return a callable that answers from them.

        Args:
            pairs: Every pair the caller is about to ask about.
            trace_id: The proposal's trace, for provider and injection errors.

        Returns:
            An `EntailFn` total over `pairs`. It is expected to **raise** on a
            pair it was not given rather than return a default: a number nothing
            measured, inside a confidence score, is the failure CHECKPOINT B
            exists to catch.
        """
        ...


class EntityResolver(Protocol):
    """Says which entity a candidate is about.

    Structural, like every other seam in this package. **Nothing implements it
    here** - see the module docstring.

    Deliberately *not* "turns a surface form into the entity it names", which is
    what this said before ADR-0008. Under that decision an implementation does
    no name matching at all: it reads an explicit binding - the caller's
    `hints.subject`, or a subject-bound namespace - and refuses when it has
    neither. Deciding what "Joan Ellery" and "Joan E." mean within one tenant is
    a matching problem with a precision/recall trade-off and no data here to set
    it with, and the ADR defers it rather than guessing at a threshold.

    `NamespaceEntityResolver` in `memory/entities.py` is the implementation.
    This stays a Protocol because ADR-0008 explicitly defers the matching
    problem: a resolver that does compare names arrives behind this same seam,
    with an eval behind it.
    """

    async def resolve(
        self,
        subject: str,
        *,
        tenant_id: TenantId,
        namespace: Namespace,
        expected_type: str,
    ) -> EntityId:
        """Resolve which entity `subject` belongs to, within this tenant.

        Args:
            subject: The surface form Layer 1 extracted, or the caller's
                `hints.subject` where one was given. **Recorded, not matched on**
                - ADR-0008 sets `canonical_name` from it on first creation and
                never compares it to anything.
            tenant_id: Whose entity graph to resolve against. Required, and not
                optional: resolving across tenants is the isolation failure the
                whole product exists to prevent.
            namespace: The isolation scope the candidate landed in, and under
                ADR-0008 the thing that usually answers the question - a
                `<type>:<id>` namespace names one subject.
            expected_type: The entity type the predicate declares its subject to
                be, `PredicateSpec.subject`. Required because `entity.type` is
                `NOT NULL` and nothing else in this signature could supply it -
                the gap that made the protocol unimplementable until ADR-0008.

        Returns:
            The entity id. An implementation creates the entity for a binding it
            has not seen before, rather than raising: a first mention is the
            normal case, not an error.

        Raises:
            ValidationRejected: no binding is available - neither a
                `hints.subject` nor a subject-bound namespace - so the subject
                cannot be identified. Refusing is ADR-0008's decision and the
                message is expected to name both remedies. A resolver that
                guessed here would attach a fact to the wrong record, which no
                invariant in this system would catch.
        """
        ...


def scope_of_namespace(namespace: Namespace) -> Scope:
    """Read §3.3's `scope` off the namespace's own prefix.

    Args:
        namespace: The candidate's isolation scope.

    Returns:
        `Scope.ORG` for an `org:` namespace, `Scope.SESSION` for a `session:`
        one, and `Scope.USER` for everything else.

    `MEMORY_ENGINE.md` line 50 documents the convention this reads -
    `"patient:8812" | "org:acme" | "session:xyz"` - as an illustration of the
    format rather than as a declared vocabulary, which is why this is a shipped
    *default* and not part of `CandidateClassifier`. A deployment whose
    namespaces do not follow it should answer the question itself.

    Everything else is `USER` because the pattern is `<type>:<id>` and a
    namespace naming one subject is one subject's blast radius. The case that
    reading gets wrong is a shared namespace with an unrecognised prefix -
    `team:eng`, say - which would score as `USER` (0.5) rather than `ORG` (1.0)
    and under-price the reach of a write. Worth knowing before adding a third
    sharing tier.
    """
    if namespace.startswith("org:"):
        return Scope.ORG
    if namespace.startswith("session:"):
        return Scope.SESSION
    return Scope.USER


@dataclass(frozen=True, slots=True)
class Deps:
    """Everything `run()` talks to, bound once per process.

    Attributes:
        llm: The provider client. Used by the noise filter, the extractor and
            the NLI judge.
        vector: The assertion store, already bound to one tenant.
        graph: The entity graph.
        embedder: The one the store was built with - retrieval compares vectors
            from a single model or the distances mean nothing.
        nli: §2.2(a)'s judge.
        entail: §3.1's and §3.2's entailment, as the batch-then-lookup seam
            `EntailLookup` describes. One awaited call per candidate covers
            both questions - the clustering's `K(K-1)` comparisons and the
            one grounding pair - because `inputs.entail_pairs` assembles
            them together.
        resolver: Surface form to `EntityId`.
        ontology: The tenant's pack, already validated.
        thresholds: §3.4's five cut points and their version. Built by
            `Settings.thresholds()`, never read inside `decide()`.
        weights: §3.2's confidence weights.
        betas: §3.3's risk coefficients.
        policy_version: Which policy pack produced the obligations.
        max_concurrent_scores: How many candidates `run()` may score at once,
            from `settings.max_concurrent_scores`. Here rather than a module
            constant in `orchestrator.py` because it is configuration and
            `RULES.md` §2.4 puts configuration in `Settings` - which declared
            the field from S1.4 and had nothing reading it, so the orchestrator
            shipped with a hard-coded 8 and the environment variable that was
            supposed to tune it did nothing. A setting nobody reads is worse
            than no setting: it is a knob that answers when you turn it.

    A frozen dataclass rather than a `GMModel` because half of these are
    Protocols, and pydantic would try to validate them as fields. `RULES.md`
    §2.1's typing rules are satisfied by the annotations; there is nothing here
    to validate that `mypy --strict` does not already check.
    """

    llm: LLMClient
    vector: VectorStore
    graph: GraphStore
    embedder: Embedder
    nli: NLIJudge
    entail: EntailLookup
    resolver: EntityResolver
    ontology: Ontology
    thresholds: Thresholds
    weights: ConfidenceWeights
    betas: RiskBetas
    policy_version: str
    max_concurrent_scores: int
