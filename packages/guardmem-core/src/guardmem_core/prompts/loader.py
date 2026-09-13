"""Versioned prompt files, loaded and rendered.  S2.1

`RULES.md` §3: "Prompts live in versioned files (`prompts/<name>/v<N>.md`) with
frontmatter: model tier, expected schema, changelog. Prompts are never
f-string-assembled inline in business logic."

**The notebook introduces `render()` at S2.2 and needs it at S2.1.** S2.2's
extractor calls `render("extract_memories", v=1, ...)` as though the function
already existed; S2.1 is the earlier step, and it is the first one that sends a
prompt to a model, so the rule above binds there first. It lands here.

Two deliberate departures from that S2.2 snippet:

- **`variables` is a mapping, not `**kwargs`.** With keyword arguments, a
  template variable named `v` or `name` collides with the function's own
  parameters, and the collision is silent in the direction that matters - the
  caller's value is swallowed as configuration.
- **`version` is spelled in full.** `v=1` reads as a template variable at the
  call site, which is exactly the confusion the previous point is about.

**Frontmatter is parsed as flat `key: value`, not as YAML.** That is a choice,
not an oversight. `pyyaml` is a declared dependency of this package's eventual
runtime but deliberately not in its manifest until S3.5, the step that loads
real YAML (the ontology); pulling a parser in one step early for five scalar
keys is the wrong trade. So the parser here is total and strict: it accepts a
block of `key: value` lines and **rejects everything else loudly** - indentation,
list items, block scalars, duplicate keys - rather than guessing. It does not
claim to parse YAML and will refuse valid YAML it cannot represent, which is the
safe direction for a file that pins what a model is asked to do.

Loads are cached, so an edit to a prompt file needs a restart. That is the
intended behaviour rather than a limitation: `RULES.md` §3 pins prompt versions
so replay is honest, and a prompt that could change under a running process
would make `prompt_version` a claim rather than a fact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Final

from pydantic import Field

from guardmem_core.llm.base import Tier
from guardmem_core.schemas.base import GMModel

__all__ = ["PromptSpec", "RenderedPrompt", "render"]

# Prompt files live beside this module: prompts/<name>/v<N>.md.
_PROMPT_ROOT: Final = Path(__file__).parent

_FRONTMATTER_FENCE: Final = "---"

# Template variables are lowercase snake identifiers and nothing else. A loose
# pattern here would make `{{ontology.yaml()}}` look like a variable and fail at
# render time with a message about a missing key rather than a bad template.
_PLACEHOLDER: Final = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

_FRONTMATTER_LINE: Final = re.compile(r"^([a-z_][a-z0-9_]*):[ \t]*(.*)$")


# A prompt name is one lowercase path segment and nothing else. `name` is a
# module constant at every call site today, so this is defence in depth rather
# than a live hole - but `render` is a public function of a library package, and
# `_PROMPT_ROOT / name` happily resolves `../../..`. Today that escapes the
# package and fails only because no `v1.md` happens to sit at the traversed
# path, which is a property of the filesystem rather than of this code.
_PROMPT_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")


class PromptSpec(GMModel):
    """The frontmatter of one prompt file.

    Attributes:
        name: Must equal the directory name, which is what makes a copied file
            in the wrong folder a load error rather than a silent mis-render.
        version: Must equal the `N` in `v<N>.md`, for the same reason.
        tier: Which rung of `ARCHITECTURE.md` §2.8's ladder this prompt is
            written for. Declared here so the call site cannot route a prompt
            authored for FAST onto FRONTIER by passing the wrong argument -
            `render` returns it and the caller passes it straight through.
        output_schema: The name of the Pydantic model the reply must validate
            against. A string rather than the class, because the prompt file is
            data and importing from it would invert the dependency.
        changelog: Why this version exists. `RULES.md` §3 makes a prompt change
            a semver-minor change that triggers the nightly eval gate, so the
            reason a version exists has to survive next to it.
    """

    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    tier: Tier
    output_schema: str = Field(min_length=1)
    changelog: str = Field(min_length=1)


class RenderedPrompt(GMModel):
    """A prompt ready to send, and the identity of the file it came from.

    Attributes:
        spec: The frontmatter.
        text: The rendered body, with every placeholder substituted.
        version_id: `"<name>@v<N>"`. This is the value that goes onto
            `MemoryCandidate.prompt_version` - `llm/base.py` records why the
            client cannot supply it: it is handed a rendered string and has no
            idea which file produced it, so the caller that chose the file is
            the one that records it.
    """

    spec: PromptSpec
    text: str
    version_id: str


def _split_frontmatter(raw: str, source: Path) -> tuple[str, str]:
    """Split a prompt file into its frontmatter block and its body.

    Args:
        raw: The whole file.
        source: Where it came from, for the error message.

    Returns:
        The frontmatter text and the body text.

    Raises:
        ValueError: if the file does not open with a `---` fence, or the fence
            is never closed.
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        raise ValueError(
            f"{source}: a prompt file must open with a '{_FRONTMATTER_FENCE}' "
            "frontmatter fence; RULES.md 3 requires tier, schema and changelog "
            "to travel with the prompt"
        )
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_FENCE:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]).strip()
    raise ValueError(f"{source}: the frontmatter fence is never closed")


def _parse_frontmatter(block: str, source: Path) -> dict[str, str]:
    """Parse a flat `key: value` block, refusing anything richer.

    Args:
        block: The text between the fences.
        source: Where it came from, for the error message.

    Returns:
        The keys and their values, with one layer of matching quotes removed.

    Raises:
        ValueError: on a line that is not `key: value`, or on a duplicate key.
            Both are refusals rather than interpretations - see the module
            docstring on why this is not a YAML parser.
    """
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        match = _FRONTMATTER_LINE.match(line)
        if match is None:
            raise ValueError(
                f"{source}: frontmatter line {line!r} is not `key: value`. This "
                "parser is deliberately flat and refuses what it cannot "
                "represent exactly - nesting, lists and block scalars included."
            )
        key, value = match.group(1), match.group(2).strip()
        if key in fields:
            raise ValueError(f"{source}: duplicate frontmatter key {key!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    return fields


def _spec_from(fields: Mapping[str, str]) -> PromptSpec:
    """Validate a parsed frontmatter mapping into a `PromptSpec`.

    Routed through JSON deliberately. `GMModel` is `strict=True`, so validating
    the mapping directly rejects `"1"` for an `int` and `"fast"` for a `Tier` -
    which is the whole point of that setting, and exactly wrong for a file whose
    every value arrives as text. `schemas/base.py` records the escape hatch:
    strict mode relaxes for JSON input, because JSON has no type that could
    carry a datetime or an enum otherwise.

    Only `version` needs help, since JSON distinguishes `1` from `"1"` and the
    frontmatter cannot. A non-numeric version is left as a string so that
    pydantic reports it rather than this function.

    Going through pydantic rather than constructing the model by hand is what
    makes `extra="forbid"` reach the frontmatter: a misspelled key there is then
    a load error rather than a silently ignored line.

    Args:
        fields: The parsed `key: value` pairs.

    Returns:
        The validated spec.

    Raises:
        pydantic.ValidationError: on a missing, unknown or ill-typed key.
    """
    document: dict[str, object] = dict(fields)
    raw_version = document.get("version")
    if isinstance(raw_version, str) and raw_version.isdigit():
        document["version"] = int(raw_version)
    return PromptSpec.model_validate_json(json.dumps(document))


@cache
def _load(name: str, version: int) -> tuple[PromptSpec, str]:
    """Read and validate one prompt file. Cached for the life of the process.

    Args:
        name: Prompt directory name.
        version: The `N` in `v<N>.md`.

    Returns:
        The validated frontmatter and the raw body.

    Raises:
        FileNotFoundError: if no such prompt file is installed. On an editable
            install this means a typo; on a wheel it means the `.md` files were
            not packaged, which is worth checking with `uv build --wheel` the
            way S1.5 checked `py.typed`.
        ValueError: if `name` is not a single path segment, if `version` is
            below 1, if the frontmatter is malformed, or if `name`/`version`
            inside it disagree with the path it was loaded from.
        pydantic.ValidationError: if a frontmatter field is missing or has the
            wrong type - an unknown `tier`, most usefully.
    """
    if not _PROMPT_NAME.match(name):
        raise ValueError(
            f"prompt name {name!r} is not a single lowercase path segment; "
            "a name is a directory under prompts/, never a path"
        )
    if version < 1:
        raise ValueError(f"prompt version must be 1 or greater, got {version}")

    source = _PROMPT_ROOT / name / f"v{version}.md"
    if not source.is_file():
        raise FileNotFoundError(
            f"no prompt at {source}. Prompts live in "
            "packages/guardmem-core/src/guardmem_core/prompts/<name>/v<N>.md "
            "(RULES.md 3)."
        )
    frontmatter, body = _split_frontmatter(source.read_text(encoding="utf-8"), source)
    spec = _spec_from(_parse_frontmatter(frontmatter, source))
    if spec.name != name or spec.version != version:
        raise ValueError(
            f"{source}: frontmatter declares {spec.name}@v{spec.version} but the "
            f"file is at {name}/v{version}.md. The path is the identity a call "
            "site uses, so the two may not disagree."
        )
    if not body:
        raise ValueError(f"{source}: the prompt body is empty")
    return spec, body


def render(name: str, version: int, variables: Mapping[str, str]) -> RenderedPrompt:
    """Render a versioned prompt file with the given variables.

    Substitution is verbatim and single-pass: a value containing `{{x}}` is not
    re-expanded. That matters because values here are frequently untrusted
    content, and a second pass would let that content name a template variable.

    Args:
        name: Prompt directory name, e.g. `"classify_noise"`.
        version: The `N` in `v<N>.md`.
        variables: One entry per `{{placeholder}}` in the body. The mapping must
            match the template exactly in **both** directions: a missing key is
            a prompt sent with a literal `{{...}}` in it, and an extra key is
            almost always a typo in the one that was meant - silently dropping
            it would send the model a prompt with a hole where the content
            should be.

    Returns:
        The rendered prompt, its frontmatter, and its `version_id`.

    Raises:
        FileNotFoundError: if the prompt file is not installed.
        ValueError: if the file is malformed, or if `variables` and the
            template's placeholders do not correspond exactly.
        pydantic.ValidationError: if the frontmatter does not satisfy
            `PromptSpec`.
    """
    spec, body = _load(name, version)
    expected = set(_PLACEHOLDER.findall(body))
    supplied = set(variables)
    if expected != supplied:
        missing = sorted(expected - supplied)
        unexpected = sorted(supplied - expected)
        raise ValueError(
            f"{name}@v{version}: template variables do not match. "
            f"missing={missing} unexpected={unexpected}"
        )
    text = _PLACEHOLDER.sub(lambda m: variables[m.group(1)], body)
    return RenderedPrompt(spec=spec, text=text, version_id=f"{name}@v{version}")
