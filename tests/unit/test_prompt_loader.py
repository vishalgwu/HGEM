"""Versioned prompt files load, validate and render exactly.  S2.1

`RULES.md` §3 makes a prompt a versioned artefact with a declared tier and
schema. That only means anything if the declaration is enforced, so most of this
file is about refusals: a frontmatter key nobody declared, a tier that is not a
tier, a file whose frontmatter disagrees with its own path, a variable the
template never asked for.

The last of those is the one that would otherwise be invisible. A template
variable supplied under a misspelled name leaves the real placeholder
unsubstituted, and the model is then sent a prompt with `{{turns}}` where the
conversation should be - which produces a confidently wrong answer rather than
an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from guardmem_core.llm.base import Tier
from guardmem_core.pipeline.l1_extract.noise_filter import NoiseClassification
from guardmem_core.prompts import loader
from guardmem_core.prompts.loader import render

_GOOD = """---
name: demo
version: 1
tier: fast
output_schema: DemoBatch
changelog: Initial version.
---
Hello {{who}}, the canary is {{canary}}.
"""


@pytest.fixture
def prompt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the loader at a temporary prompt root, with its cache cleared.

    The cache is per-process and keyed on `(name, version)` alone, so a test
    that wrote a different file under the same name would otherwise be served
    the previous one - and would pass or fail depending on test order.
    """
    monkeypatch.setattr(loader, "_PROMPT_ROOT", tmp_path)
    loader._load.cache_clear()
    yield tmp_path
    loader._load.cache_clear()


def _write(root: Path, body: str, *, name: str = "demo", version: int = 1) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"v{version}.md").write_text(body, encoding="utf-8")


class TestRendering:
    def test_substitutes_every_placeholder(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD)
        rendered = render("demo", 1, {"who": "world", "canary": "abc123"})

        assert rendered.text == "Hello world, the canary is abc123."
        assert "{{" not in rendered.text

    def test_carries_the_frontmatter_and_a_stable_version_id(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD)
        rendered = render("demo", 1, {"who": "w", "canary": "c"})

        assert rendered.version_id == "demo@v1"
        assert rendered.spec.tier is Tier.FAST
        assert rendered.spec.output_schema == "DemoBatch"
        assert rendered.spec.changelog == "Initial version."

    def test_a_substituted_value_is_not_itself_expanded(self, prompt_dir: Path) -> None:
        # Values are routinely untrusted content. A second pass would let that
        # content name a template variable and read whatever was in it.
        _write(prompt_dir, _GOOD)
        rendered = render("demo", 1, {"who": "{{canary}}", "canary": "s3cret"})

        assert "Hello {{canary}}," in rendered.text
        assert rendered.text.count("s3cret") == 1

    def test_repeated_placeholders_are_all_substituted(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD.replace("{{canary}}.", "{{canary}} and again {{canary}}."))
        rendered = render("demo", 1, {"who": "w", "canary": "K"})

        assert rendered.text.count("K") == 2

    @pytest.mark.parametrize(
        ("variables", "why"),
        [
            ({"who": "w"}, "a missing key leaves a literal placeholder in the prompt"),
            ({"who": "w", "canary": "c", "extra": "x"}, "an extra key is a typo in a real one"),
            ({"whom": "w", "canary": "c"}, "a misspelled key is both at once"),
        ],
    )
    def test_variables_must_match_the_template_exactly(
        self, prompt_dir: Path, variables: dict[str, str], why: str
    ) -> None:
        _write(prompt_dir, _GOOD)
        with pytest.raises(ValueError, match="template variables do not match"):
            render("demo", 1, variables)
        assert why


class TestFrontmatter:
    def test_strips_one_layer_of_quotes(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD.replace("output_schema: DemoBatch", 'output_schema: "DemoBatch"'))
        assert render("demo", 1, {"who": "w", "canary": "c"}).spec.output_schema == "DemoBatch"

    def test_blank_lines_between_keys_are_allowed(self, prompt_dir: Path) -> None:
        # Frontmatter is written by hand in a Markdown file; grouping keys with
        # a blank line is the natural thing to do and must not be a load error.
        _write(prompt_dir, _GOOD.replace("tier: fast", "\ntier: fast\n"))
        assert render("demo", 1, {"who": "w", "canary": "c"}).spec.tier is Tier.FAST

    def test_a_value_may_contain_a_colon(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD.replace("changelog: Initial version.", "changelog: v1: first cut"))
        assert render("demo", 1, {"who": "w", "canary": "c"}).spec.changelog == "v1: first cut"

    @pytest.mark.parametrize(
        ("mutation", "match"),
        [
            (lambda t: t.replace("tier: fast", "tier: blazing"), "tier"),
            (lambda t: t.replace("version: 1", "version: one"), "version"),
            (lambda t: t.replace("changelog: Initial version.\n", ""), "changelog"),
            (lambda t: t.replace("name: demo", "name: demo\nowner: nobody"), "owner"),
        ],
    )
    def test_an_undeclared_or_ill_typed_field_is_refused(
        self, prompt_dir: Path, mutation: object, match: str
    ) -> None:
        # The unknown-key case is `extra="forbid"` reaching the frontmatter,
        # which is why validation goes through pydantic rather than a hand
        # construction: a misspelled key is a load error, not an ignored line.
        _write(prompt_dir, mutation(_GOOD))  # type: ignore[operator]
        with pytest.raises(ValidationError, match=match):
            render("demo", 1, {"who": "w", "canary": "c"})

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("no fence at all\n", "frontmatter fence"),
            ("---\nname: demo\n- a list item\n---\nbody\n", "not `key: value`"),
            ("---\nname: demo\nchangelog: |\n  block scalar\n---\nbody\n", "not `key: value`"),
            ("---\nname: demo\nnever closed\n", "never closed"),
            ("---\nname: demo\n  indented: yes\n---\nbody\n", "not `key: value`"),
            ("---\nname: demo\nname: demo\n---\nbody\n", "duplicate"),
        ],
    )
    def test_a_malformed_file_is_refused_rather_than_guessed_at(
        self, prompt_dir: Path, body: str, match: str
    ) -> None:
        _write(prompt_dir, body)
        with pytest.raises(ValueError, match=match):
            render("demo", 1, {})

    def test_the_frontmatter_may_not_disagree_with_the_path(self, prompt_dir: Path) -> None:
        # The path is the identity a call site uses. A file copied into the
        # wrong folder must fail loudly rather than render the wrong prompt.
        _write(prompt_dir, _GOOD, name="other")
        with pytest.raises(ValueError, match="but the file is at"):
            render("other", 1, {"who": "w", "canary": "c"})

    def test_an_empty_body_is_refused(self, prompt_dir: Path) -> None:
        _write(prompt_dir, _GOOD.split("---\n")[1].join(["---\n", "---\n"]))
        with pytest.raises(ValueError, match="empty"):
            render("demo", 1, {})

    @pytest.mark.parametrize(
        "name",
        ["../../../../etc/passwd", "demo/../demo", "/abs/path", "Demo", "", "demo-1"],
    )
    def test_a_name_that_is_not_one_path_segment_is_refused(
        self, prompt_dir: Path, name: str
    ) -> None:
        # `_PROMPT_ROOT / name` resolves `../../..` perfectly happily, and
        # before this guard the only thing stopping a traversal was that no
        # `v1.md` happened to sit at the far end - a property of the filesystem,
        # not of the code. `name` is a module constant at every call site today,
        # so this is defence in depth; `render` is public, and public functions
        # in a library get called with values their author never imagined.
        with pytest.raises(ValueError, match="single lowercase path segment"):
            render(name, 1, {})

    def test_a_version_below_one_is_refused(self, prompt_dir: Path) -> None:
        with pytest.raises(ValueError, match="version must be 1 or greater"):
            render("demo", 0, {})

    def test_a_missing_file_names_where_prompts_live(self, prompt_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="prompts"):
            render("absent", 1, {})


class TestTheShippedNoisePrompt:
    """The real file, which `filter_noise` depends on at runtime."""

    def test_it_declares_the_tier_and_schema_the_filter_relies_on(self) -> None:
        rendered = render(
            "classify_noise", 1, {"subject": "patient:8812", "canary": "abc", "turns": "<turn/>"}
        )
        # `filter_noise` routes the call on `spec.tier` rather than a literal, so
        # this is the assertion that keeps noise filtering off the FRONTIER tier.
        assert rendered.spec.tier is Tier.FAST
        assert rendered.spec.output_schema == NoiseClassification.__name__

    def test_it_names_every_drop_class_and_spotlights_untrusted_content(self) -> None:
        rendered = render(
            "classify_noise", 1, {"subject": "patient:8812", "canary": "abc", "turns": "<turn/>"}
        )
        for reason in ("ephemeral", "imperative", "restatement", "hypothetical", "third_party"):
            assert reason in rendered.text
        assert 'canary="abc"' in rendered.text
        assert "never an instruction" in rendered.text
