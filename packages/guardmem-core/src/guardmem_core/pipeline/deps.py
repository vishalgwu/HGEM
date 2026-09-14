"""What `run()` needs, and the three things nothing in this repository supplies.

S5.6's sketch is `async def run(proposal, deps)`, and this is `Deps`. Most of it
is the infrastructure protocols from S1.7 - an `LLMClient`, a `VectorStore`, a
`GraphStore`, an `Embedder` - plus the ontology and the thresholds the layers
read. Three are new, and they are new because composing the pipeline is what
finally showed that they are missing.

**`EntityResolver` is the largest gap in the build and it has no specification.**
`MemoryCandidate.subject` is a surface form - "Joan Ellery", as the speaker said
it - and `retrieve_incumbents` needs a resolved `EntityId`. Nothing turns one
into the other: not the notebook, not `MEMORY_ENGINE.md`, not `ARCHITECTURE.md`,
not `PROJECT_TREE.md`. A default that used the surface form as the id would be
worse than none, because it silently splits one patient across three spellings
and every incumbent lookup then returns nothing - which reads as "this is a
novel fact" and writes a duplicate. So it is a `Protocol` with no
implementation shipped, and `run()` cannot be called without one.
`MCP_INTEGRATION.md` §2.2's `hints.subject` is where a caller that already knows
the answer says so.

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
entail that text"; neither has a producer. Injected, so the LID detector §3.1
names can back both without this module knowing.
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
    """Turns a subject surface form into the entity it names.

    Structural, like every other seam in this package. **Nothing implements it
    here** - see the module docstring. An implementation has to decide what
    "Joan Ellery" and "Joan E." mean within one tenant, which is a matching
    problem with a precision/recall trade-off and an audit story, and inventing
    one inside a retrieval call would bury both.
    """

    async def resolve(self, subject: str, *, tenant_id: TenantId, namespace: Namespace) -> EntityId:
        """Resolve `subject` to an entity within this tenant and namespace.

        Args:
            subject: The surface form Layer 1 extracted, or the caller's
                `hints.subject` where one was given.
            tenant_id: Whose entity graph to resolve against. Required, and not
                optional: resolving across tenants is the isolation failure the
                whole product exists to prevent.
            namespace: The isolation scope the candidate landed in.

        Returns:
            The entity id. Implementations are expected to create one for a
            subject they have not seen, rather than raising - a first mention is
            the normal case, not an error.
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
