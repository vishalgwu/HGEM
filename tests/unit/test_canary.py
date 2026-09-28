"""The one canary.  RULES.md §3

Each stage's own echo test (`test_extractor_refusals.py`, `test_noise_filter.py`,
`test_nli_judge.py`, `test_entailment.py`) proves that stage refuses an echo.
This proves the pieces they share, and that nothing mints a canary of its own
again: five modules once did, and two of them drifted to twice the length the
other three used while their comments said "same length".
"""

from __future__ import annotations

import re

import pytest

from conftest import REPO_ROOT
from guardmem_core.errors import InjectionDetected
from guardmem_core.prompts.canary import CANARY_BYTES, mint_canary, reject_echo
from guardmem_core.types import TraceId

TRACE = TraceId("tr_canary")


class TestMinting:
    def test_it_is_canary_bytes_of_hex(self) -> None:
        assert re.fullmatch(rf"[0-9a-f]{{{2 * CANARY_BYTES}}}", mint_canary())

    def test_it_is_fresh_on_every_call(self) -> None:
        """A reused token can be learned from one transcript and avoided."""
        assert len({mint_canary() for _ in range(50)}) == 50


class TestTheEchoCheck:
    def test_a_reply_without_the_canary_passes(self) -> None:
        reject_echo("feedface", ['{"facts": []}', "nothing here"], stage="x", trace_id=TRACE)

    def test_one_echoing_sample_is_enough(self) -> None:
        """One leaking sample is the injection, whichever sample gets used."""
        with pytest.raises(InjectionDetected, match=r"^extraction echoed the canary token$"):
            reject_echo(
                "feedface",
                ['{"facts": []}', "sure: feedface"],
                stage="extraction",
                trace_id=TRACE,
            )

    def test_it_reads_a_generator_to_the_end(self) -> None:
        samples = (text for text in ["clean", "clean", "feedface"])

        with pytest.raises(InjectionDetected):
            reject_echo("feedface", samples, stage="x", trace_id=TRACE)


def test_nothing_else_mints_a_canary() -> None:
    """The length, the source of randomness and the check live in one module.

    A module that mentions a canary and calls `token_hex` is minting its own,
    which is how the five copies started drifting.
    """
    trees = [REPO_ROOT / "packages", REPO_ROOT / "services"]
    minting = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for tree in trees
        for path in tree.rglob("*.py")
        if path.name != "canary.py"
        and "token_hex" in (text := path.read_text(encoding="utf-8"))
        and "canary" in text
    )

    assert minting == [], "mint canaries with guardmem_core.prompts.canary.mint_canary"
