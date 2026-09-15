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

**`CandidateClassifier` supplies the three risk features §3.3 names and defines
nowhere.** `scope`, `pii_class` and `irreversibility` are in §3.3's table and in
no ontology field, no extractor output, and no other document. Two of the three
are genuinely policy: whether a predicate is special-category data, and whether
what an agent did on a belief can be undone, are decisions a deploying
organisation makes. `Scope` is the exception - `MEMORY_ENGINE.md` line 50
documents the namespace convention it follows - so a default is shipped for that
one alone, and named as a default rather than folded into the protocol.

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

from guardmem_core.pipeline.l3_score.impact_features import Irreversibility, PiiClass, Scope
from guardmem_core.schemas.base import GMModel

if TYPE_CHECKING:
    from guardmem_core.llm.base import LLMClient
    from guardmem_core.memory.graph.base import GraphStore
    from guardmem_core.memory.vector.base import Embedder, VectorStore
    from guardmem_core.pipeline.l2_validate import NLIJudge
    from guardmem_core.pipeline.l3_score import ConfidenceWeights, EntailFn, RiskBetas
    from guardmem_core.schemas.candidate import MemoryCandidate
    from guardmem_core.schemas.ontology import Ontology
    from guardmem_core.schemas.verdict import Thresholds
    from guardmem_core.types import EntityId, Namespace, TenantId

__all__ = [
    "CandidateClassifier",
    "CandidateRisk",
    "Deps",
    "EntityResolver",
    "scope_of_namespace",
]


class CandidateRisk(GMModel):
    """The three §3.3 features a deployment has to decide for itself.

    Attributes:
        scope: How widely shared the namespace is.
        pii_class: What kind of personal data the claim carries.
        irreversibility: Whether what an agent did on this belief can be undone.

    Grouped into one model rather than three arguments because they are answered
    together, by the same policy, about the same candidate - and because a
    protocol returning a tuple of three enums is a protocol whose arguments get
    transposed.
    """

    scope: Scope
    pii_class: PiiClass
    irreversibility: Irreversibility


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

    **This signature is one argument short of the ADR and has not been changed
    yet.** Creating an entity needs `type`, which is `NOT NULL` on the row;
    `PredicateSpec.subject` carries it, `_decide_one` has the spec in scope, and
    it is not passed. Adding `expected_type` is the first implementation step.
    """

    async def resolve(self, subject: str, *, tenant_id: TenantId, namespace: Namespace) -> EntityId:
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


class CandidateClassifier(Protocol):
    """Answers §3.3's three undeclared features for one candidate.

    Structural, and nothing implements it here for two of the three. See
    `scope_of_namespace` for the one that can be derived.
    """

    async def classify(self, candidate: MemoryCandidate) -> CandidateRisk:
        """Classify a candidate's scope, PII class and reversibility.

        Args:
            candidate: The proposed fact, after the schema gate has admitted it.

        Returns:
            The three features, each already a member of its vocabulary rather
            than a float - so a wrong answer is a wrong *category* and shows up
            in the audit record as one.
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
        entail: §3.1's and §3.2's entailment function. See the module docstring
            on why one callable answers two questions.
        resolver: Surface form to `EntityId`.
        classifier: §3.3's three undeclared features.
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
    entail: EntailFn
    resolver: EntityResolver
    classifier: CandidateClassifier
    ontology: Ontology
    thresholds: Thresholds
    weights: ConfidenceWeights
    betas: RiskBetas
    policy_version: str
    max_concurrent_scores: int
