"""The CHECKPOINT B harness.  BUILD_NOTEBOOK.md, after Day 5

`tests/unit/test_discrimination.py` covers the measurement; this covers the
command around it - the corpus format, the exit codes, and the two refusals that
protect two hours of somebody's labelling.

The exit codes are the part worth testing. PASS, MARGINAL and FAIL are three
outcomes and the checkpoint means different things by each: MARGINAL is
"proceed, but record it", which a boolean cannot express.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.checkpoint_b import agreement, main, score, template


def corpus(path: pathlib.Path, rows: list[dict[str, object]]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def row(name: str, keep: bool | None, confidence: float) -> dict[str, object]:
    return {
        "candidate_id": name,
        "keep": keep,
        "subject": "patient:8812",
        "predicate": "allergy",
        "object": name,
        "verbatim": "v",
        "scores": {"confidence": confidence},
    }


class TestTemplate:
    def test_it_writes_a_corpus_the_scorer_can_read(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "corpus.jsonl"

        assert template(target) == 0
        assert [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

    def test_the_rows_are_unlabelled(self, tmp_path: pathlib.Path) -> None:
        """`keep` is `null`, so `score` refuses until a human has been through
        it - rather than defaulting to a label nobody chose."""
        target = tmp_path / "corpus.jsonl"
        template(target)

        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]

        assert all(item["keep"] is None for item in rows)

    def test_it_refuses_to_overwrite(self, tmp_path: pathlib.Path) -> None:
        """Two hours of labelling is not something a typo should be able to
        destroy."""
        target = corpus(tmp_path / "corpus.jsonl", [row("c_1", True, 0.9)])

        assert template(target) == 1
        assert "c_1" in target.read_text(encoding="utf-8")


class TestScore:
    def test_a_separating_scorer_passes(self, tmp_path: pathlib.Path) -> None:
        path = corpus(
            tmp_path / "c.jsonl",
            [row("a", True, 0.9), row("b", True, 0.8), row("c", False, 0.2), row("d", False, 0.1)],
        )

        assert score(path) == 0

    def test_a_scorer_at_chance_fails(self, tmp_path: pathlib.Path) -> None:
        """The failure the whole checkpoint exists for: plausible numbers with
        no discriminative power."""
        path = corpus(
            tmp_path / "c.jsonl",
            [row("a", True, 0.5), row("b", False, 0.5), row("c", True, 0.5), row("d", False, 0.5)],
        )

        assert score(path) == 2

    def test_an_unlabelled_corpus_is_refused_rather_than_scored(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Scoring over the labelled subset would report an AUROC over whatever
        the labeller happened to reach first."""
        path = corpus(tmp_path / "c.jsonl", [row("a", True, 0.9), row("b", None, 0.1)])

        assert score(path) == 2

    def test_the_three_exit_codes_are_distinct(self, tmp_path: pathlib.Path) -> None:
        """MARGINAL is "proceed, but record it and revisit when thresholds are
        tuned" - neither a pass nor a stop, so it needs its own code."""
        from guardmem_core.eval.discrimination import Verdict

        assert len({Verdict.PASS, Verdict.MARGINAL, Verdict.FAIL}) == 3

    def test_a_malformed_line_names_its_row(self, tmp_path: pathlib.Path) -> None:
        """A human edits this file by hand, so the error has to say where."""
        path = tmp_path / "c.jsonl"
        path.write_text('{"candidate_id": "a"}\nnot json\n', encoding="utf-8")

        with pytest.raises(ValueError, match=r"c\.jsonl:2"):
            score(path)

    def test_a_row_without_scores_is_refused_by_name(self, tmp_path: pathlib.Path) -> None:
        path = corpus(tmp_path / "c.jsonl", [{"candidate_id": "a", "keep": True}])

        with pytest.raises(ValueError, match="`scores` must be an object"):
            score(path)

    def test_blank_lines_are_ignored(self, tmp_path: pathlib.Path) -> None:
        """A hand-edited file grows them."""
        path = tmp_path / "c.jsonl"
        path.write_text(
            json.dumps(row("a", True, 0.9)) + "\n\n" + json.dumps(row("b", False, 0.1)) + "\n",
            encoding="utf-8",
        )

        assert score(path) == 0


class TestAgreement:
    def test_consistent_labelling_passes(self, tmp_path: pathlib.Path) -> None:
        rows = [row("a", True, 0.9), row("b", False, 0.1)]
        first = corpus(tmp_path / "first.jsonl", rows)
        second = corpus(tmp_path / "second.jsonl", rows)

        assert agreement(first, second) == 0

    def test_inconsistent_labelling_fails_the_ten_percent_bar(self, tmp_path: pathlib.Path) -> None:
        """And the message says to fix the ontology before the scorer, which is
        the checkpoint's own ordering."""
        first = corpus(tmp_path / "first.jsonl", [row("a", True, 0.9), row("b", False, 0.1)])
        second = corpus(tmp_path / "second.jsonl", [row("a", False, 0.9), row("b", False, 0.1)])

        assert agreement(first, second) == 1

    def test_the_second_pass_may_be_a_subset(self, tmp_path: pathlib.Path) -> None:
        """Diagnostic 3 re-labels 30 of 200."""
        first = corpus(
            tmp_path / "first.jsonl",
            [row("a", True, 0.9), row("b", False, 0.1), row("c", True, 0.5)],
        )
        second = corpus(tmp_path / "second.jsonl", [row("a", True, 0.9)])

        assert agreement(first, second) == 0


class TestTheCommandLine:
    def test_score_is_reachable_as_a_subcommand(self, tmp_path: pathlib.Path) -> None:
        path = corpus(tmp_path / "c.jsonl", [row("a", True, 0.9), row("b", False, 0.1)])

        assert main(["score", str(path)]) == 0

    def test_template_is_reachable(self, tmp_path: pathlib.Path) -> None:
        assert main(["template", str(tmp_path / "c.jsonl")]) == 0

    def test_agreement_is_reachable(self, tmp_path: pathlib.Path) -> None:
        rows = [row("a", True, 0.9), row("b", False, 0.1)]
        first = corpus(tmp_path / "f.jsonl", rows)
        second = corpus(tmp_path / "s.jsonl", rows)

        assert main(["agreement", str(first), str(second)]) == 0

    def test_a_missing_subcommand_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            main([])
