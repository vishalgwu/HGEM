"""The prompt surface.  MCP_INTEGRATION.md §4, BUILD_NOTEBOOK.md S6.4

Four prompts, two served and two declining. What is worth pinning is not that
they render - `prompts/loader.py` is tested where it lives - but the three
claims the surface makes about itself:

- **The served prompts are the pipeline's own files, not paraphrases.** §4 says
  exposing the canonical extraction prompt "reduces schema-gate rejections
  dramatically", which is only true if it is the same bytes `extract()` sends. A
  copy that drifted would keep passing every test that only checked it rendered.
- **The canary is fresh every time and reaches the caller.** A served prompt
  moves the injection check to the client, so the token has to be in `_meta` for
  the client to have anything to check against - and it must never repeat, or an
  attacker who saw one transcript can instruct around it.
- **The two that decline must decline.** A prompt that invented a review brief
  for a queue that does not exist would be fabricating exactly the artefact this
  product exists to stop people fabricating.
"""

from __future__ import annotations

import pytest

from fixtures.mcp import state
from guardmem_core.prompts.loader import render
from mcp_server.prompts import _BUILDERS, PROMPTS, PROMPTS_BY_NAME, get_prompt
from mcp_server.tools.context import ToolRefusedError

EXTRACT = "guardmem/extract_memories"
ADJUDICATE = "guardmem/adjudicate_conflict"
REVIEW = "guardmem/review_brief"
HYGIENE = "guardmem/memory_hygiene_report"

CONTENT = "Patient is allergic to penicillin."


def _text(result: object) -> str:
    """The single user message a prompt renders to."""
    assert hasattr(result, "messages")
    return str(result.messages[0].content.text)


class TestTheAdvertisedSurface:
    def test_every_published_prompt_has_a_builder(self) -> None:
        """A prompt that lists in a client's menu and has no builder fails only
        when somebody picks it, which is the worst place to find out."""
        assert set(PROMPTS_BY_NAME) == set(_BUILDERS)

    def test_all_four_of_section_four_are_published(self) -> None:
        """§4's table is the contract. Two decline when invoked and both are
        still listed: a refusal that names its build step teaches something, and
        an absence teaches nothing."""
        assert set(PROMPTS_BY_NAME) == {EXTRACT, ADJUDICATE, REVIEW, HYGIENE}

    def test_the_unavailable_two_say_so_in_their_titles(self) -> None:
        """A client's menu is the last place a person looks before picking one,
        so the disappointment belongs there rather than after the click."""
        for name in (REVIEW, HYGIENE):
            assert "not available" in (PROMPTS_BY_NAME[name].title or "").lower()

    def test_required_arguments_match_section_four(self) -> None:
        """§4 names the arguments and a client builds its form from them."""
        required = {
            prompt.name: {a.name for a in (prompt.arguments or []) if a.required}
            for prompt in PROMPTS
        }

        assert required[EXTRACT] == {"content"}
        assert required[ADJUDICATE] == {"candidate", "incumbent"}
        assert required[REVIEW] == {"task_id"}
        assert required[HYGIENE] == {"namespace"}

    def test_an_unknown_prompt_names_the_real_ones(self) -> None:
        with pytest.raises(ToolRefusedError, match="unknown prompt"):
            get_prompt(state(), "guardmem/nope", {})


class TestTheExtractionPrompt:
    def test_it_is_the_pipeline_s_own_file_byte_for_byte(self) -> None:
        """The claim §4 makes for this prompt is that it is *the same one*.

        Rendered here with the canary the server minted, so the comparison is
        against the identical inputs - anything else would be comparing two
        different renders and passing for the wrong reason.
        """
        result = get_prompt(state(), EXTRACT, {"content": CONTENT})
        canary = (result.meta or {})["canary"]
        ontology = state().ontology

        expected = render(
            "extract_memories",
            1,
            {"content": CONTENT, "ontology": ontology.as_prompt_yaml(), "canary": str(canary)},
        )

        assert _text(result) == expected.text

    def test_the_canary_is_in_the_text_and_in_the_meta(self) -> None:
        """A served prompt moves the injection check to the client, so the token
        has to be somewhere the client can read it without parsing the prompt."""
        result = get_prompt(state(), EXTRACT, {"content": CONTENT})
        canary = str((result.meta or {})["canary"])

        assert canary
        assert canary in _text(result)

    def test_two_renders_never_share_a_canary(self) -> None:
        """A reused token is one an attacker can learn from a single transcript
        and then instruct the model to avoid, which leaves the check passing
        while the injection succeeds."""
        first = get_prompt(state(), EXTRACT, {"content": CONTENT})
        second = get_prompt(state(), EXTRACT, {"content": CONTENT})

        assert (first.meta or {})["canary"] != (second.meta or {})["canary"]

    def test_the_pinned_version_is_reported(self) -> None:
        """`RULES.md` §3 makes a prompt change a versioned event, so a client
        that cached v1's text must be able to tell that it did."""
        result = get_prompt(state(), EXTRACT, {"content": CONTENT})

        assert (result.meta or {})["prompt_version"] == "extract_memories@v1"

    def test_k_defaults_to_the_middle_arm_of_the_ladder(self) -> None:
        """§3.1 takes entropy over the spread and a single sample has none, so a
        client that omits `k` and draws one would compute a confidence this
        pipeline would not recognise."""
        assert (get_prompt(state(), EXTRACT, {"content": CONTENT}).meta or {})["k"] == 3

    def test_k_is_carried_but_never_rendered(self) -> None:
        """It is a property of the call, not of the text - `MEMORY_ENGINE.md`
        §1.2's sample count. The template has no slot for it and should not."""
        result = get_prompt(state(), EXTRACT, {"content": CONTENT, "k": "5"})

        assert (result.meta or {})["k"] == 5
        assert "5" not in _text(result).replace(CONTENT, "")[:200]

    @pytest.mark.parametrize("bad", ["0", "11", "three", "-1"])
    def test_an_impossible_k_is_refused_rather_than_clamped(self, bad: str) -> None:
        """Silently returning 3 to a caller who asked for 40 would make their
        entropy divisor and their sample count disagree."""
        with pytest.raises(ToolRefusedError, match="k must be"):
            get_prompt(state(), EXTRACT, {"content": CONTENT, "k": bad})

    def test_missing_content_is_refused_by_name(self) -> None:
        with pytest.raises(ToolRefusedError, match="requires the 'content'"):
            get_prompt(state(), EXTRACT, {})

    def test_blank_content_is_refused_rather_than_rendered(self) -> None:
        """Arguments are strings by protocol, so an untouched form field arrives
        as `""` - and an empty `content` asks a model to find facts in nothing,
        which it will obligingly do."""
        with pytest.raises(ToolRefusedError, match="non-empty string"):
            get_prompt(state(), EXTRACT, {"content": "   "})

    def test_a_foreign_ontology_is_refused_rather_than_substituted(self) -> None:
        """An agent told to extract against `legal` and handed `clinical`
        produces candidates the gate quarantines for reasons it cannot see."""
        with pytest.raises(ToolRefusedError, match="cannot serve 'legal'"):
            get_prompt(state(), EXTRACT, {"content": CONTENT, "ontology_ref": "legal"})

    def test_naming_the_installed_pack_is_accepted(self) -> None:
        """Refusing a caller who asked for exactly what is installed would be a
        guard that only punishes the careful."""
        result = get_prompt(state(), EXTRACT, {"content": CONTENT, "ontology_ref": "clinical"})

        assert "clinical" in str((result.meta or {})["ontology"])


class TestTheAdjudicationPrompt:
    def test_it_renders_the_pinned_file(self) -> None:
        result = get_prompt(
            state(), ADJUDICATE, {"candidate": "pharmacy = CVS", "incumbent": "pharmacy = Lloyds"}
        )

        assert (result.meta or {})["prompt_version"] == "adjudicate_conflict@v1"
        assert "CVS" in _text(result)
        assert "Lloyds" in _text(result)

    def test_section_four_s_singular_fills_the_template_s_plural(self) -> None:
        """§4 publishes `incumbent` and the file's slot is `incumbents`, because
        §2.2 retrieves the top ten. A client that read §4 must work, so one
        incumbent renders as a set of one."""
        result = get_prompt(
            state(), ADJUDICATE, {"candidate": "c", "incumbent": "the-only-incumbent"}
        )

        assert "the-only-incumbent" in _text(result)

    def test_both_claims_are_required(self) -> None:
        """Adjudication with nothing to adjudicate against is not a degraded
        answer, it is a different question."""
        with pytest.raises(ToolRefusedError, match="requires the 'incumbent'"):
            get_prompt(state(), ADJUDICATE, {"candidate": "c"})

    def test_it_carries_its_own_canary(self) -> None:
        result = get_prompt(state(), ADJUDICATE, {"candidate": "c", "incumbent": "i"})

        assert str((result.meta or {})["canary"]) in _text(result)


class TestTheTwoThatDecline:
    def test_the_review_brief_names_the_step_that_will_provide_it(self) -> None:
        """A `HITL_REVIEW` decision is audited and produces no task, so there is
        nothing to brief. Naming S18.1 is what makes that a gap rather than a
        mystery."""
        with pytest.raises(ToolRefusedError, match=r"S18\.1"):
            get_prompt(state(), REVIEW, {"task_id": "rt_1"})

    def test_the_review_brief_still_validates_its_argument_first(self) -> None:
        """ "I left out a field" and "this feature does not exist" send a person
        to different places, so the order the request is wrong in is kept."""
        with pytest.raises(ToolRefusedError, match="requires the 'task_id'"):
            get_prompt(state(), REVIEW, {})

    def test_the_hygiene_report_refuses_rather_than_reporting_two_of_three(self) -> None:
        """Drift and contradiction pressure are unmeasured. A report that
        quietly dropped them would read as a clean bill of health, which is an
        absence rendering identically to a negative finding."""
        with pytest.raises(ToolRefusedError, match="drift"):
            get_prompt(state(), HYGIENE, {"namespace": "patient:7781"})

    def test_the_hygiene_report_points_at_what_does_work(self) -> None:
        """A refusal that leaves a reader with nothing is a worse refusal."""
        with pytest.raises(ToolRefusedError, match="guardmem://memory/"):
            get_prompt(state(), HYGIENE, {"namespace": "patient:7781", "window": "30d"})
