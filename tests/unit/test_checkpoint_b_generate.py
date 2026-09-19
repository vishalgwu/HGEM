"""CHECKPOINT B's generation step, in the parts that need no model.

`generate` is mostly composition - a pool, a provider, a resolver, an ontology -
and composition is what the live run exercises. What is testable here is the two
places it makes a *decision* about the data, and both of them are places a
mistake would be silent:

- **`_row` orients every score so that higher is better.** `auroc` reports the
  probability that a kept candidate outranks a rejected one, so a term ranked
  the wrong way round comes back *below* 0.5 and reads as anti-predictive when
  it is working. `H_norm` is uncertainty, so the row carries `1 - H_norm`.
- **`_read_proposals` refuses a malformed line** rather than skipping it, which
  on a corpus job means the difference between 200 candidates and 180 with no
  indication which twenty are missing.

The provider selection is here too, for one reason: asking for Anthropic without
a key must refuse rather than fall back to the local model. An AUROC measured on
Claude and one measured on a 7B local model are different numbers, and a silent
fallback makes the sign-off's provider line a lie.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from fixtures.assertions import ENTITY, TENANT, WHEN
from fixtures.conflict import candidate
from fixtures.decisions import conflict, risk
from fixtures.mcp import settings
from fixtures.providers import ollama_client, ollama_transport
from guardmem_core.llm.base import Tier
from guardmem_core.llm.providers import OllamaClient, build_llm
from guardmem_core.pipeline.orchestrator import Proposal
from guardmem_core.pipeline.per_candidate import GovernedCandidate
from guardmem_core.schemas.receipt import SourceTier
from guardmem_core.schemas.turn import Turn, TurnRole
from guardmem_core.schemas.verdict import ConfidenceReport, Decision, DecisionRecord
from guardmem_core.types import Namespace, TraceId, TurnId
from scripts.checkpoint_b_generate import _provider_settings, _row
from scripts.checkpoint_b_proposals import read_proposals

# `_read_proposals` takes K explicitly rather than reading `Settings`, so these
# tests pin their own value: what they assert is namespace and trace derivation,
# neither of which depends on the sample size.
_TEST_K = 3


def governed(*, entropy: float = 0.25, confidence: float = 0.8) -> GovernedCandidate:
    """One candidate paired with a decision about it.

    The candidate comes from `fixtures.conflict.candidate`, which is the shared
    builder - a second hand-rolled `MemoryCandidate` here would be free to drift
    from the real shape, which is the drift the S3.6 audit found four times.
    """
    return GovernedCandidate(
        candidate=candidate(
            predicate="allergy", obj="penicillin", verbatim="allergic to penicillin"
        ),
        subject_id=ENTITY,
        record=DecisionRecord(
            decision=Decision.HITL_REVIEW,
            reason_codes=[],
            confidence=ConfidenceReport(
                semantic_entropy=entropy,
                grounding=0.9,
                schema_fit=1.0,
                corroboration=0.0,
                consistency=1.0,
                confidence=confidence,
                weights_version="v1",
            ),
            risk=risk(),
            conflict=conflict(),
            thresholds_version="v1",
            policy_version="checkpoint-b",
        ),
    )


def proposal(trace: str = "tr_ckb_0001") -> Proposal:
    """The conversation a row is attributed to.

    `_row` qualifies `candidate_id` with the trace, because the pipeline's own
    ids restart at `c_1` for every proposal and a corpus spans dozens of them.
    """
    return Proposal(
        trace_id=TraceId(trace),
        tenant_id=TENANT,
        namespace=Namespace("patient:1"),
        turns=[Turn(turn_id=TurnId("t1"), role=TurnRole.USER, text="hello", captured_at=WHEN)],
        source_tier=SourceTier.VERIFIED_USER,
        k=3,
        tier=Tier.FAST,
    )


class TestEveryScoreIsOrientedHigherIsBetter:
    """The property that decides whether a diagnostic means anything."""

    def test_uncertainty_is_one_minus_entropy(self) -> None:
        """`H_norm` is uncertainty and §3.2 weighs `w_H(1 - H_norm)`.

        Writing the raw value would make `auroc` rank a confident candidate
        *below* an uncertain one, so a working entropy term would report an
        AUROC under 0.5 and read as evidence against itself. The template
        shipped a `semantic_entropy` key and had exactly this bug.
        """
        row = _row(governed(entropy=0.25), proposal())

        assert row["scores"]["uncertainty"] == pytest.approx(0.75)

    def test_no_score_is_the_raw_entropy(self) -> None:
        """Belt and braces: the inverted key must not survive anywhere."""
        assert "semantic_entropy" not in _row(governed(), proposal())["scores"]

    def test_the_composite_is_carried_under_the_name_discriminate_expects(self) -> None:
        """`discriminate` takes `composite="confidence"` and raises if it is not
        among the scores. A corpus that omitted it would be unscoreable."""
        row = _row(governed(confidence=0.42), proposal())

        assert row["scores"]["confidence"] == pytest.approx(0.42)


class TestTheRowIdentifiesOneRow:
    """A corpus-wide identifier, not a per-proposal one."""

    def test_the_candidate_id_is_qualified_by_its_trace(self) -> None:
        """The pipeline restarts candidate numbering at `c_1` for every
        proposal, so an unqualified id repeats once per conversation. A 60
        conversation corpus carried 7 distinct ids over 234 rows.

        That is not cosmetic: `checkpoint_b._labels` builds `dict[str, bool]`
        keyed on this field, so the self-agreement diagnostic collapsed to one
        entry per distinct id and reported a rate over 7 labels while claiming
        30. Diagnostic 3 is what you run when the gate fails, and it lied.
        """
        row = _row(governed(), proposal("tr_ckb_0007"))

        assert row["candidate_id"].startswith("tr_ckb_0007_")

    def test_two_proposals_do_not_collide_on_the_same_candidate_number(self) -> None:
        """The property the corpus actually needs, stated over two rows the
        pipeline would both have numbered `c_1`."""
        first = _row(governed(), proposal("tr_ckb_0001"))
        second = _row(governed(), proposal("tr_ckb_0002"))

        assert first["candidate_id"] != second["candidate_id"]


class TestTheRowIsLabellable:
    def test_it_carries_what_a_human_needs_to_judge(self) -> None:
        """A `DecisionRecord` carries none of this - `MEMORY_ENGINE.md` §0 gives
        it eight fields and not one says what was decided *about*. Without
        `GovernedCandidate` the corpus would be numbers with nothing to read."""
        row = _row(governed(), proposal())

        assert row["subject"] == "Joan Ellery"
        assert row["predicate"] == "allergy"
        assert row["object"] == "penicillin"
        assert row["verbatim"] == "allergic to penicillin"

    def test_keep_is_null_and_stays_null(self) -> None:
        """The whole validity of the gate. "A human labels each one [...] No
        model grading"."""
        assert _row(governed(), proposal())["keep"] is None

    def test_the_decision_is_carried_for_the_labeller_not_for_the_score(self) -> None:
        """Useful to a human - a row the pipeline would auto-write and they are
        about to mark `false` is the most interesting row in the file - and not
        among `scores`, because AUROC is over `C`."""
        row = _row(governed(), proposal())

        assert row["decision"] == "hitl_review"
        assert "decision" not in row["scores"]


class TestReadProposals:
    def test_each_line_is_one_conversation(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "p.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "namespace": f"patient:{n}",
                        "turns": [
                            {
                                "turn_id": "t1",
                                "role": "user",
                                "text": "hello",
                                "captured_at": WHEN.isoformat(),
                            }
                        ],
                    }
                )
                for n in range(3)
            ),
            encoding="utf-8",
        )

        proposals = list(read_proposals(path, TENANT, _TEST_K))

        assert [p.namespace for p in proposals] == ["patient:0", "patient:1", "patient:2"]

    def test_the_trace_is_derived_from_the_line_so_a_rerun_matches(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`replay_trace.py` needs something stable to reproduce, and a random
        trace per run would give it nothing to hold on to."""
        path = tmp_path / "p.jsonl"
        line = json.dumps(
            {
                "namespace": "patient:1",
                "turns": [
                    {
                        "turn_id": "t1",
                        "role": "user",
                        "text": "hello",
                        "captured_at": WHEN.isoformat(),
                    }
                ],
            }
        )
        path.write_text(line, encoding="utf-8")

        first = list(read_proposals(path, TENANT, _TEST_K))
        second = list(read_proposals(path, TENANT, _TEST_K))

        assert first[0].trace_id == second[0].trace_id

    def test_a_malformed_line_names_its_row(self, tmp_path: pathlib.Path) -> None:
        """Skipping it would mean a short corpus with no indication which
        conversations are missing from it."""
        path = tmp_path / "p.jsonl"
        path.write_text('{"namespace": "patient:1"}\n', encoding="utf-8")

        with pytest.raises(ValueError, match="needs an object with `namespace` and `turns`"):
            list(read_proposals(path, TENANT, _TEST_K))

    def test_a_line_that_is_not_json_names_its_row(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "p.jsonl"
        path.write_text("not json\n", encoding="utf-8")

        with pytest.raises(ValueError, match=r"p\.jsonl:1"):
            list(read_proposals(path, TENANT, _TEST_K))


class TestProviderSelection:
    """Built with `fixtures.mcp.settings`, which passes `_env_file=None`.

    **Not a hand-rolled `Settings`.** The first version of this class built one
    from a literal dict and omitted `redis_url`, `neo4j_user` and
    `neo4j_password` - which a developer's `.env` supplied silently and CI,
    where `.env` is gitignored and absent, did not. Four tests passed locally
    and failed on the runner. The shared builder's own docstring warns about
    exactly this, and using it is the fix.
    """

    def test_ollama_needs_no_credential(self) -> None:
        chosen = _provider_settings("ollama", settings(anthropic_api_key=""))

        assert chosen.llm_provider == "ollama"

    def test_anthropic_without_a_key_refuses_rather_than_falling_back(self) -> None:
        """The sign-off has to name the provider, so a silent fallback to the
        local model would make it a lie - and an AUROC from a 7B local model is
        not an AUROC from Claude."""
        with pytest.raises(ValueError, match="GM_ANTHROPIC_API_KEY is empty"):
            _provider_settings("anthropic", settings(anthropic_api_key=""))

    def test_an_unknown_provider_names_what_it_expected(self) -> None:
        with pytest.raises(ValueError, match="expected 'ollama' or 'anthropic'"):
            _provider_settings("gpt", settings(anthropic_api_key=""))

    def test_openai_is_refused_even_though_settings_would_accept_it(self) -> None:
        """`Settings.llm_provider` allows it and this harness does not. Nothing
        has been measured against OpenAI, and the gate's sign-off names the
        provider that produced the corpus - so the narrower list is the honest
        one and it has to be enforced rather than assumed from `--provider`'s
        `choices`, which a direct caller does not go through.
        """
        with pytest.raises(ValueError, match="expected 'ollama' or 'anthropic'"):
            _provider_settings("openai", settings(anthropic_api_key=""))

    def test_the_local_model_comes_from_settings_not_from_os_environ(self) -> None:
        """`pydantic-settings` reads `.env` without exporting it to `os.environ`.

        This module used to read `GM_OLLAMA_MODEL` with `os.environ.get`, so a
        value set in `.env` - which is where `.env.example` says to set it -
        configured the rest of the process and was silently ignored here. The
        corpus would then have been generated against `llama3.1:8b` whatever the
        operator selected, which is the one defect the harness exists to avoid.
        """
        chosen = _provider_settings(
            "ollama", settings(anthropic_api_key="", ollama_model="qwen2.5:7b")
        )

        assert chosen.ollama_model == "qwen2.5:7b"

    async def test_the_local_tier_ladder_collapses_to_one_model(self) -> None:
        """Worth pinning because it is a caveat on any number this corpus
        produces: a BALANCED judge call and a FAST extraction hit the same
        weights, so `MEMORY_ENGINE.md` §3.5's cost ladder is not exercised at
        all by a local run.

        Asserted through the payload rather than the adapter's private model
        map - what matters is which model the request names, not how the client
        stores it.
        """
        seen: list[dict[str, Any]] = []
        http = ollama_client(ollama_transport(seen=seen))
        chosen = _provider_settings("ollama", settings(anthropic_api_key=""))
        try:
            async with build_llm(chosen, http) as client:
                assert isinstance(client, OllamaClient)
                for tier in Tier:
                    await client.complete(prompt="p", tier=tier)
        finally:
            await http.aclose()

        assert len({body["model"] for body in seen}) == 1
