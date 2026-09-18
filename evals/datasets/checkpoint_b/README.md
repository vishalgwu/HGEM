# CHECKPOINT B — corpus provenance

**Read this before quoting any AUROC produced from this directory.**

## What is here

| File | What it is | Who wrote it |
|---|---|---|
| `proposals.jsonl` | 20 conversations, one subject each, 229 turns | **Claude, synthetically** |
| `corpus.jsonl` | The pipeline's candidates and their scores, `keep: null` on every row | `checkpoint_b generate`, from a real model |

## The caveat that travels with the number

**The transcripts are synthetic.** They were written by Claude on 2026-09-16 and
reviewed by the repository owner. That is a deliberate choice, made because the
alternative was a gate that stayed blocked, but it is a real limitation and it
belongs in any limitations section that cites a score from here.

`BUILD_NOTEBOOK.md`'s CHECKPOINT B says not to hand-author the 200 candidates,
and that rule is honoured: **no candidate in `corpus.jsonl` was written by
hand.** Every one was extracted by the real pipeline — the same prompt, the same
K-sampling, the same span linker — from the conversations in `proposals.jsonl`.

What the rule is protecting against is grading the scorer against the author's
idea of a plausible mistake. Writing the *input* conversations is a milder form
of the same bias rather than an escape from it: the distribution of clean facts,
hedged ones and contradictions is one author's guess at a clinical intake call,
not a sample of real ones. **Real transcripts would produce a more trustworthy
number, and replacing these is the right upgrade when any exist.**

## What was deliberately varied

The conversations were written to span the cases a labeller has to decide
between, because a corpus where every candidate is obviously keepable has no
spread and therefore no AUROC:

- clean durable facts, stated plainly
- hedged statements (`"I think I might react to codeine, but I'm not certain"`)
- facts about a third party (`"My mother has COPD, but that's her, not me"`)
- corrections inside one conversation (shellfish reclassified as food poisoning)
- superseded values (`"I was on amlodipine before that but we stopped it"`)
- speculation about the future (`"I may come off it in the summer"`)
- tool turns that contradict the patient (EHR says `A+`, patient says `O negative`)
- noise: scheduling, weather, `"don't write that down"`

None of that is a label. Which candidates survive extraction is the pipeline's
answer; which deserve to survive is the labeller's, and the two disagreeing is
the entire point of the gate.

## Extraction is not deterministic on a local model

Measured on `llama3.1:8b` with `extract_memories@v2`, same input, five repeats:

| conversation | result |
|---|---|
| `patient:9001` | 4-5 admitted, 0 rejected, 0 quarantined, every run |
| `patient:9013` | 3 admitted in four runs; **0 admitted / 7 quarantined in the fifth** |

The failing run degenerates into predicates the ontology does not declare -
`has = "84 kilos"`, `lost = "private cover"`, `is = "still running"` - which the
gate quarantines, so the conversation contributes nothing.

Two consequences for anyone building or reading a corpus from here:

- **A single generation pass under-samples, invisibly.** Roughly one
  conversation in five can contribute zero candidates, and the total gives no
  hint which. Count per-conversation yields and re-run the empty ones.
- **The corpus samples the model's variance as well as the scorer's quality.**
  For a gate whose entire purpose is measuring how well `C` separates good
  writes from bad, an unstable extractor underneath it is a real confound. This
  is a measured argument for generating on a frontier model rather than a
  preference.

The object-shape half is stable: 0 rejections in 5 of 5 runs, both
conversations. That was `extract_memories@v1`'s bug and v2 fixes it.

## What a full run actually produces

20 conversations on `extract_memories@v2` and `llama3.1:8b`, one clean pass:
**88 candidates, 0 rejected, 0 quarantined, 0 failed, and every one of the 20
contributing.** Under v1 the same transcripts lost everything in 8 of 15
conversations, so this is the fix measured at scale rather than on three.

4.4 candidates per conversation, so the checkpoint's 200 needs roughly 46
conversations. A short corpus is a corpus problem, not a code one.

**The spread of `C` is usable, which is the thing to check before labelling.**
18 distinct values over 0.25-0.84, mean 0.72, sd 0.14. The decision mix looks
lopsided - 78 `hitl_review` against 6 `auto_write`, 3 `reject`, 1 `escalate` -
but the bands are narrow, not the score underneath them, and AUROC reads the
score.

**Two of the five confidence terms are constant, and both for structural
reasons:**

| term | distinct values | why |
|---|---|---|
| `corroboration` | 1 (all 0.0) | every candidate is single-sourced |
| `consistency` | 1 (all 1.0) | one fresh subject per conversation, so no incumbents and no conflicts |
| `schema_fit` | 2 | mean 0.993, effectively constant |
| `uncertainty` | 3 | `K = 3` leaves entropy few possible values |
| `grounding` | 12 | 0-0.95, sd 0.29 - where the variance lives |

The per-term diagnostic over `corroboration` is therefore entirely ties and must
return exactly 0.5; `auroc` handles that deliberately.

**`uncertainty` is the coarsest term and the heaviest.** `w_H = 0.35` is the
largest weight in `C`, and `K = 3` gives it three distinct values.
`MEMORY_ENGINE.md` §1.2's ladder allows 5 on HIGH, which would resolve it more
finely at 67% more model calls. Worth deciding before a 200-candidate run.

## Labelling

`keep` is `null` on every row and `discriminate` refuses a corpus with any row
unlabelled, so this is not scoreable until a human has read it. **No model may
set `keep`** — that is the rule that makes the number mean anything.

```bash
# score, once every row carries a keep
uv run python -m scripts.checkpoint_b score evals/datasets/checkpoint_b/corpus.jsonl
```

The gate is AUROC of `C` ≥ 0.80. 0.75–0.80 proceeds as MARGINAL and is recorded.

## Regenerating

```bash
uv run python -m scripts.checkpoint_b generate \
  evals/datasets/checkpoint_b/corpus.jsonl \
  --proposals evals/datasets/checkpoint_b/proposals.jsonl \
  --provider ollama
```

Trace ids are derived from the line number, so a re-run over the same file
produces the same traces and `replay_trace.py` has something stable to
reproduce. Generation writes no memory: `run()` applies no decision.

**A number from one provider is not comparable with a number from another.**
`MEMORY_ENGINE.md` §1.2 draws the canonical sample at temperature 0 and the rest
at 0.7; Anthropic accepts neither parameter, so entropy measured on Claude and
entropy measured on `llama3.1:8b` are two different instruments. Record which
one produced a score alongside the score.
