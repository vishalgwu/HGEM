# GuardMem AI

[![CI](https://github.com/vishalgwu/HGEM/actions/workflows/ci.yml/badge.svg)](https://github.com/vishalgwu/HGEM/actions/workflows/ci.yml)

**A memory governance gateway for long-running AI agents.**

> **Status: Layer 1 works end to end, and it now has somewhere to write to.**
> The design suite, the toolchain and the gates are in place; `guardmem_core`
> carries its typed foundation — settings, domain ids, the error hierarchy, the
> Pydantic schema layer, the store and LLM protocols — the whole of **Layer 1**
> (noise filter, K-sample extractor, span linker), and from S3.1–S3.5 the
> bitemporal Postgres schema, the pgvector store over it, the outbox that
> coordinates the dual write, the graph store on the other side of it, and the
> ontology that says what a predicate is allowed to mean — with `make seed`
> putting all of it together, and S4.1 starting Layer 2 by enforcing that
> vocabulary, S4.2 retrieving what it already believes, S4.3 deciding
> whether the two can both be true and S4.4 settling what to do about it:
> write, search, supersede, point-in-time recall of a fact that has since been
> retired, and a partial write that is never retrievable. Nothing yet validates,
> scores or decides a candidate — that is Layer 2. Every performance
> and quality figure below is a **target**, not a measurement — see
> [Status](#status) before quoting any number.

---

## The problem

An agent is correct on turn 3, absorbs a wrong fact on turn 40, and is
confidently wrong for six weeks — because nothing sat between the model and the
datastore.

Almost every serious agent deployment eventually writes to long-term memory.
Almost none of them govern that write. There is no uncertainty estimate, no
conflict check, no blast-radius assessment, and no lineage. A hallucinated fact
is stored with exactly the same authority as a confirmed one, and six weeks
later nobody can prove where it came from.

## What GuardMem does

It intercepts every candidate fact *before* it becomes durable state:

```
raw turns ──► L1 EXTRACTION ──► L2 VALIDATION & CONFLICT ──► L3 SCORING ──► DECISION
              noise → cands       ontology, NLI, dedupe        H, C, R       AUTO_WRITE
                                                                             HITL_REVIEW
                                                                             REJECT
                                                                             ESCALATE
```

1. **Extract** structured candidates, each anchored to a **verbatim source span**.
   No span, no write — this single rule kills most confabulation before any
   scoring happens.
2. **Validate** against a tenant ontology and check three independent conflict
   signals: NLI contradiction, predicate cardinality, temporal overlap.
3. **Score** confidence `C` from semantic entropy over meaning clusters, and
   impact risk `R` from blast radius. These are deliberately separate axes — a
   perfectly confident write to the account-owner field is still a high-risk
   operation.
4. **Decide** — auto-write the safe majority, queue the ambiguous middle for a
   one-click human review, reject the rest.
5. **Receipt** — every durable decision lands in a hash-chained audit log.

Four stances drive the rest of the design:

- **The agent never blocks on governance.** `propose` returns fast with a
  `trace_id`; evaluation runs on a worker.
- **Nothing is deleted.** Contradiction resolves by supersession + tombstone,
  and bitemporal columns make *"what did the agent believe on 2026-03-14?"* a
  normal query.
- **Fail closed, not quiet.** Provider outage, budget cap, policy error,
  injection detection — all degrade toward human review. Degradation never
  widens the auto-write path.
- **The audit log is the product.** If a decision isn't reconstructable, that's
  a bug of the same severity as a wrong decision.

---

## Repository layout

```
docs/              the design suite - start here
packages/          guardmem-core, the decision engine
services/          the deployable units - mcp_server today, gateway and worker later
scripts/           operational commands - seed, replay, the checkpoint harness
infra/             the dev datastore stack and the Alembic migrations
tests/             unit / integration / contract / property / security
requirements/      layered, pinned Python dependencies
.github/workflows/ CI gates
Makefile           the developer entry points - run `make` for the list
.env.example       environment template
```

Everything else in [`docs/PROJECT_TREE.md`](docs/PROJECT_TREE.md) is a
**blueprint, not a checklist**. Each file gets created at the build step that
needs it. An empty module that exists is indistinguishable at a glance from a
finished one, which is exactly the confusion the build order exists to prevent.

## Start here

| Read | For |
|---|---|
| [docs/README.md](docs/README.md) | the index, and **which document owns which decision** |
| [docs/PRD.md](docs/PRD.md) | problem, scope, personas, acceptance targets |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, write/read paths, failure behavior |
| [docs/MEMORY_ENGINE.md](docs/MEMORY_ENGINE.md) | **the spec of record** — scoring math, schemas, thresholds |
| [docs/RULES.md](docs/RULES.md) | engineering standards, the seven invariants, testing gates |
| [docs/BUILD_NOTEBOOK.md](docs/BUILD_NOTEBOOK.md) | the step-by-step build, day by day |

Read `docs/README.md` first. It carries a "which document owns what" table, so
you know where a change belongs before you make it.

---

## Local setup

Requires Python 3.12, Docker, and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/vishalgwu/HGEM.git && cd HGEM

uv venv --python 3.12 --prompt HGEM --seed .venv
.venv\Scripts\activate                       # PowerShell
# source .venv/Scripts/activate              # Git Bash / macOS / Linux

uv pip install -r requirements.lock.txt      # third-party deps, 313 packages
uv pip install -e packages/guardmem-core     # the decision engine
uv pip install -e services/mcp_server        # the MCP server (S6.1)
python -m spacy download en_core_web_lg      # presidio needs this; ~400 MB

cp .env.example .env                         # then paste your API keys into .env
```

Install the git hooks, then verify — these are the same gates CI runs:

```bash
make hooks                                   # pre-commit, once per clone
make lint && make typecheck && make test     # all three must pass
```

`make` on Windows: it is not installed by default and does not ship with Git for
Windows. `winget install ezwinports.make` gives GNU Make 4.4.1 with no MSYS
dependency; restart your shell afterwards so the PATH change takes effect. Run
`make` with no target for the full list.

The two editable installs are not optional and are easy to skip.
`requirements.lock.txt` pins only third-party packages; `guardmem_core` and
`mcp_server` live in this repo and are installed from source in editable mode, so
your edits take effect without reinstalling. Omit them and every import of
`guardmem_core` — or, since S6.1, of `mcp_server` — fails with
`ModuleNotFoundError` on an otherwise perfectly good environment, and
`guardmem-mcp` is not on your PATH.

> **Do not run bare `uv sync` here.** It is *exact* — it uninstalls everything
> not in `uv.lock`, which today means roughly 300 of the 313 packages above.
> `uv run` is inexact and safe. See the note at the bottom of `pyproject.toml`.

`requirements.lock.txt` is the fully-resolved transitive set — 313 packages,
verified to reproduce the environment exactly rather than approximately.
`requirements/ml-local.txt` (torch, transformers) is **excluded on purpose** and
is only needed if week-2 latency forces a local cross-encoder.

The dev datastore stack is `infra/docker/docker-compose.dev.yml`, in place since
S1.3: Postgres+pgvector, Redis, Neo4j and Arize Phoenix, every image pinned and
healthchecked. `make dev` brings it up and waits for health. Langfuse is
deliberately **not** in it — from v3 it needs ClickHouse, MinIO and a worker
container, so it arrives at S13.1 in `docker-compose.observability.yml` with the
stack it actually requires.

---

## Status

The decision engine is built and the write path is not. The repository was reset
to a documentation baseline on 2026-09-09; `BUILD_NOTEBOOK.md` Day 1 is complete
(S1.1 – S1.7), Layer 1 is complete (S2.1 – S2.3), the storage layer is complete
(**Day 3, S3.1 – S3.6**), **Layer 2 is complete (S4.1 – S4.4)** — the schema
gate, incumbent retrieval, conflict detection, and the resolution matrix with the
merge behind it — **Day 5 is complete (S5.1 – S5.6)**: semantic entropy, the
confidence composite, the impact-risk score, the decision matrix, the
hash-chained audit log, and the orchestrator that runs a proposal through all of
it — and **Day 6 is at S6.2**: the MCP server, its four core tools, and the
first user-facing surface this project has had.

**S9.1 is in**, out of order and deliberately: Anthropic, OpenAI and Ollama
behind one `LLMClient`. That retires the first of the two caveats this section
used to open with — the engine can call a real model now, and does.

One remains, and everything below assumes you know it: **nothing writes an
assertion.** The pipeline returns decisions and applies none; the applier needs
an ADR, and so does entity resolution. Checkpoint B — the gate that would say
whether the scoring separates good writes from bad — **has not run**, and S9.1
removed one of the four things standing in its way rather than all four. See
"The gate that has not run yet", which now lists the other three.

So the toolchain, the gates, the local datastore stack, the typed foundation of
`guardmem_core` — settings, domain ids, the error hierarchy, the Pydantic schema
layer, and the `LLMClient` / `VectorStore` / `GraphStore` protocols with
in-memory fakes — Layer 1, and durable storage are real.

The noise filter is the first component with a measured number attached, and it
is a narrow one: over a 40-turn hand-labelled corpus, its deterministic rules
drop 17 turns at **precision 1.000** and settle 30 of 40 turns without a model
call. That is a unit-test gate on one small corpus, not a production figure, and
two of the five drop classes are deliberately under-detected until the embedder
(S3.2) and the ontology (S3.5) exist — both now do, and the rules that use them
are Layer 2.

S2.2 and S2.3 add the extractor and the span linker: K samples with a
temperature-0 canonical draw, each proposed fact anchored to a verbatim span of
the source, and anything the source cannot support rejected as unsourced and
counted. That span rule is the anti-confabulation mechanism, and it is not a
judgement — a model that invents a fact must also invent the sentence it came
from, and an invented sentence is not in the source. It is enforced by
construction rather than by policy: a candidate requires provenance, provenance
requires a span, and the linker is the only thing that makes one.

S3.1 adds the schema underneath all of it: bitemporal assertions, provenance as
its own table, an append-only audit chain, the dual-write outbox, and row-level
security. Four of `RULES.md`'s non-negotiables are now properties of the
database rather than promises of the application — `DELETE` on an assertion is
revoked at the role level, the audit chain refuses UPDATE and DELETE, an
assertion without a citation cannot commit, and a session with no tenant set
sees nothing.

S3.2 is what reads and writes it. `PgVectorStore` implements the `VectorStore`
protocol over that schema, and the three properties that matter are structural
rather than conventional: a write lands `visible = false` as a statement
literal, so a half-finished dual write has no parameter through which it could
be made retrievable; the tenant is bound at construction and applied as
`SET LOCAL app.tenant_id`, which is the value row-level security reads; and
supersession is an `UPDATE` of `valid_to` that raises rather than passing
quietly when it matches nothing. A retired fact leaves search and stays
recoverable through `as_of`, which is the claim the product rests on and is
tested against a real Postgres rather than against a double.

S3.3 closes the loop. The assertion row and an outbox event commit in one
transaction; a relay then applies the graph side and sets `visible = true`, and
nothing else in the system may set that column. So a dual write that is
genuinely half-applied — the edge landed, the process died before the flip — is
not briefly wrong but simply absent, and the event that releases it is still in
the queue. Delivery is at-least-once and the effects are exactly-once, which is
the only guarantee a queue with a crashing consumer can honestly offer: the
graph write is idempotent by `assertion_id`, and the completion will not move a
timestamp it has already written. `StoreRouter` sits in front of it as the
app-layer tenant check `RULES.md` §4 asks for alongside row-level security.

S3.4 gives the relay something real to write to. `NetworkXGraphStore` is a
`MultiDiGraph` whose edges are keyed by `assertion_id`, so a replayed dispatch
replaces an edge rather than duplicating it, and `degree()` returns a number the
risk scorer can use — without it `MEMORY_ENGINE.md` §3.3's `graph_fanout`
feature is a constant and the blast-radius half of the decision matrix cannot be
evaluated at all. It is the dev and single-tenant backend, and it enforces that:
the `GraphStore` protocol gives its read methods no tenant to filter on, so it
refuses to hold two. Neo4j swaps in at S7.1 behind the same three methods.

S3.5 is the first thing that can say what a predicate *means*. The clinical
starter pack declares fifteen predicates over six entity types, and each one
states its cardinality, its blast radius, the weakest source allowed to assert
it, and whether one source is enough. Those four fields are what Layer 2 and the
risk scorer read: cardinality is what turns a second live value into a conflict
regardless of what a language model thinks, and impact is what floors the risk
score so a confident write to a critical field cannot auto-write on confidence
alone. Nothing consumes it yet — the schema gate is the first consumer, and it
is Layer 2.

S3.6 puts all of it together. `make seed` writes one tenant, seven entities and
twenty-eight assertions through the real path — router, store, outbox, relay,
graph store, ontology — from a forty-turn synthetic intake call, and every
seeded fact cites a real character span in the turn it was quoted from. Two of
them are superseded by a follow-up call five months later, so the demo database
contains a point-in-time query worth running. Running it twice changes nothing:
every id is derived from a stable key, which is the same property the outbox
relay's replay needed.

Two caveats about seeded data, stated here because they are easy to forget. The
vectors come from a deterministic hash — there is no embedding provider until
S9.1 — so nothing about retrieval quality can be measured on them. And
`confidence` is a placeholder, because Layer 3 does not exist yet; only `risk`
is real, and only because it is the impact floor the ontology declares.

That Postgres is a testcontainer, started by the suite from the repository's own
`initdb` scripts and migrated with `alembic upgrade head`; CI runs it on every
S4.1 is the first component of Layer 2 and the first thing in the pipeline to
read the ontology. Every candidate Layer 1 produced arrives claiming a predicate
and a value; the gate decides whether the tenant's vocabulary admits it, and
there are four answers rather than three. A predicate the ontology declares and
a value already of the right type passes. A value that can become the right type
without inventing meaning is coerced, and pays for it in the confidence score. A
predicate the ontology has never heard of is *quarantined* — rewritten into a
`quarantine:<tenant>` namespace where it stays retrievable and flagged, because
an unknown predicate is a gap in the ontology as often as a bad extraction. And
a value the declared type cannot hold is rejected outright.

Most of that module is refusals, deliberately. A gate that coerces too eagerly
is worse than one that rejects too readily: a rejection shows up in the funnel,
while a wrong coercion is a stored fact that reads as though somebody meant it.
`bool("no")` is `True` in Python, so a boolean predicate takes a small closed
vocabulary rather than a truthiness test — recording a declined consent as a
given one is the one place this code could do real harm.

S4.2 is where Layer 2 starts depending on Day 3: you cannot detect a
contradiction without knowing what is already believed. It fetches the ten
nearest live facts sharing a candidate's namespace, subject and predicate, plus
the graph's one-hop view of what else that subject asserts — the first code in
the pipeline to call both stores, and the first caller of the partial index the
schema was given for exactly this query.

One property there is worth naming because it fails silently: the candidate and
the stored facts must be turned into text by the *same* renderer. If they were
not, the cosine distances would still be numbers, would still order the results,
and would mean nothing at all. So there is one such function and both sides go
through it.

S4.3 asks the question retrieval was for: can this candidate and what is already
on record both be true? There are three ways to answer and the order matters.
Two of them are arithmetic — a predicate the ontology says holds one value at a
time already has one, or two validity intervals overlap — and neither needs a
model. Only the third does, and it goes last, so a contradiction that the
ontology alone can see costs nothing to find.

When a model is needed it is asked once for the whole set rather than once per
pair, and it returns one judgement per numbered incumbent. That count is
checked rather than trusted: the judgements are matched back to the incumbents
by position, so a reply one entry short would read one fact's contradiction as
another's and retire the wrong one.

The quality signal is a sixty-pair set written by hand — conflicts, duplicates
that must not be mistaken for conflicts, and cases sitting deliberately in the
band where the right answer is *ask a human*. It classifies all sixty correctly
against a bar of ninety percent, and five deliberate mutations of the code were
each caught by it, which is the part of that claim worth believing. What it
measures honestly is this repository's reading of the numbers; the judge's own
accuracy is a later step's question, and this same set is what will ask it.

S4.4 turns that finding into an action. A duplicate is merged rather than
stored: the new citation is appended to the fact it restates and the source
count goes up, so three independent mentions of one fact read as a corroborated
fact rather than as three. A claim that is strictly narrower than what is on
record supersedes it. A contradiction stops and asks a human, because the rule
for resolving one automatically depends on a confidence score that Layer 3 has
not computed yet — the rule is implemented in full, and *ask a human* is what it
returns when that number is missing.

The invariant underneath it is that a predicate declared to hold one value holds
one value. That is property-tested over five hundred generated write sequences,
with three further properties beside it so that it cannot be satisfied by
refusing to write anything: the value that survives is the one asserted last,
nothing is ever deleted, and a fact restated is corroborated rather than
duplicated. Building that test is what found a real hole — an incumbent holding
the identical value did not trip any check, because sameness was being inferred
from similarity scores rather than read off the values themselves.

S5.1 starts the scoring layer with the uncertainty term. A model asked the same
question several times may word its answer differently every time without being
any less sure, so counting distinct strings measures vocabulary rather than
doubt. The samples are grouped by meaning instead — two answers are the same
answer only when each one implies the other, in both directions — and the
uncertainty is the spread across those groups. "Dr. Alvarez" and "Alvarez, MD"
are one answer; "Dr. Chen" is a second; "unclear" is a third.

Both directions matter, and that is not a detail. A narrower claim always
implies a broader one — "allergic to penicillin and amoxicillin" implies
"allergic to penicillin" — so a one-way test would fold a specific answer into a
vague one and report agreement where the model had actually given two different
answers.

S5.2 combines that with four other signals into a single confidence score: how
consistently the model answered, whether the quoted source actually supports the
claim and how far that source is trusted, how cleanly the fact fits the schema,
how many independent sources say it, and whether it sits well with what is
already on record. Each is kept alongside the total rather than collapsed into
it, because a reviewer needs to see *which* signal was weak, and because the
weights are meant to be refitted from real decisions later.

Two of those deserve naming. Corroboration counts independent sources, not
citations — quoting one planted document three times is one source, and the
distinction is what stops a confidence score being inflated by repetition. And a
fact that conflicts with live memory scores zero on the consistency term even
when no model was asked to judge it, because a conflict the system caught by
arithmetic is still a conflict; reading the unmeasured value as agreement would
have awarded a contradiction full marks.

S5.3 scores the other axis, and the point of it is that the two are not
opposites. How confident the system is that a fact is *true* says nothing about
what breaks if it is wrong — a perfectly-confident write to the field naming an
account's owner is still a dangerous write. So risk is computed separately, from
how far the change reaches: what the ontology declares the field's impact to be,
whether the write retires an existing fact or merely adds one, how many other
facts hang off the same subject, whether the data is medical or financial,
whether anything an agent already did on it can be undone.

Underneath that sits a floor. A field declared critical can never score below
0.80 however harmless everything else looks, which means the weighting of the
individual signals is allowed to be imperfect without the safety property being
imperfect. The test for it is the one the build notebook calls the whole point
of keeping the two scores apart.

S5.4 is where the two scores become one answer: write it, send it to a human,
reject it, or spend more compute and decide again. That is a twelve-cell table
over confidence and risk, followed by seven rules that can only ever make the
answer stricter — a detected injection, retrieved web content touching an
important field, a claim from one source where policy wants two, degraded
capacity, and so on.

The function is deliberately boring: no clock, no configuration lookup, no
randomness, nothing but its arguments. That is what makes a decision replayable
months later, which is the whole premise of an audit log that can be trusted.
A test asserts the module imports none of those things, rather than trusting
that nobody adds one.

Two details are worth knowing because they look like mistakes and are not.
More confidence does not always give a friendlier answer: a middling claim is
sent for a second opinion from a larger model, while a slightly better one goes
straight to a person, because more compute only helps where the model is
genuinely unsure. And the rules never soften an outcome — a policy asking for
human review cannot reopen something already rejected.

S5.5 makes those decisions provable. Every one is written to an append-only log
where each entry's fingerprint is computed over its own contents *and* the
previous entry's fingerprint. That does not stop someone editing history — it
makes editing it visible. Change an entry and its own fingerprint stops
matching; fix the fingerprint too and the next entry no longer points at it. The
check reports the exact row where the arithmetic first fails.

The database refuses the application any way to update or delete those rows at
all, so the chain is the second line rather than the only one. The honest limit
is worth stating: lopping entries off the *end* leaves a shorter log that still
adds up, and catching that needs something outside the system to remember how
long it should have been.

S5.6 joins the parts up. One call takes a conversation and returns a decision
per fact it found, with the reasoning attached: the filter, the extractor, the
schema check, the lookup against what is already known, the conflict check and
all four scores. Candidates are handled in parallel with a ceiling on how many
models run at once, and a fact whose scoring fails is reported on its own rather
than taking the rest of the batch down with it.

It stops short of writing. Storing a fact and recording that it was stored have
to happen together or not at all, and the storage interface is deliberately
backend-agnostic — it has no transaction to share. Closing that needs a decision
about which of the two to bend, so what ships is the part that can be trusted:
every decision, complete and re-checkable.

Re-checkable is the last piece. A script re-runs any recorded decision from the
inputs stored alongside it and reports whether today's code still reaches the
same answer. It deliberately does not re-run the language models — those are not
reproducible, and pretending otherwise would make the check meaningless. What it
proves is narrower and more useful: that the rules which decided whether a fact
was believed have not silently moved.

### The gate that has not run yet

The build notebook puts a make-or-break checkpoint after Day 5: take 200
candidates, have a human label each one keep-or-discard, and measure whether the
confidence score actually separates the two. Everything after that point —
gateway, guardrails, dashboard, review queue — assumes it does.

**It has not been run.** The measurement needs candidates from real extraction,
because hand-writing the 200 would measure the author's idea of a plausible
mistake — the same problem the "no model grading" rule exists to prevent — and
scoring against the test double would measure scripted answers.

Until S9.1 there was no adapter to a real model provider at all, and that was
the whole of the explanation. It is no longer. `pipeline.run()` takes four
dependencies this repository did not supply; two are still open, and
`pipeline/deps.py` says why for each:

| Missing | What it feeds | Share of `C` |
|---|---|---|
| `EntityResolver` | incumbent retrieval → conflict → `S_con` | 0.15 |
| `CandidateClassifier` | §3.3's `pii_class` and `irreversibility` → `R` | none, but `run()` will not execute without it |

Each needs an ADR before an implementation — the first is a matching problem
with a precision/recall trade-off that no document specifies, the second is
deployment policy.

The other two are closed. `LLMClient` at S9.1, and `EntailFn` — the largest at
0.60 of `C`, feeding both §3.1's meaning clustering and §3.2's grounding — by
`LLMEntailer`, which scores a whole batch of text pairs in one BALANCED call and
hands back a lookup. It is built and **not yet wired**: the callable the scorer
takes is synchronous and the producer is not, so the orchestrator has to collect
a candidate's pairs and await one lookup before scoring it.

Two further gaps are not dependencies but are equally in the way. The harness
has no `generate` subcommand, so nothing fills a corpus; and the seed transcript
is forty turns for one patient supporting 28 facts, which is not 200 candidates.

So what is built is the *measurement*, not the thing measured: the metric, the
diagnostics, the label format, the self-consistency check on the labels, and a
command that runs the eight manual verifications by running the tests that
establish them. Those eight pass. The discrimination number does not exist, and
this section is here so that it is not mistaken for one that does.

That Postgres is a testcontainer, started by the suite from the repository's own
`initdb` scripts and migrated with `alembic upgrade head`; CI runs it on every
push.

S6.1 and S6.2 are the newest steps: a process that speaks MCP over stdio, starts
its pool, loads the ontology, and serves the four tools `MCP_INTEGRATION.md` §2
publishes — `memory.search`, `memory.propose`, `memory.commit` and
`memory.get_entity`.

**Two of the four work and two decline, and that is the honest state rather than
an unfinished one.** `memory.search` and `memory.get_entity` read governed memory
end to end: point one at the seeded demo tenant and it returns believed facts
with the verbatim source span each came from, and lists separately what has been
*retired* — which is the difference between "we have no record" and "we no longer
believe that". `memory.propose` and `memory.commit` validate every published
constraint and then refuse, naming what is missing: the decision pipeline needs a
model provider (S9.1), an entity resolver (specified in no document) and a
candidate classifier. **They do not return an invented decision.** The whole claim
of this project is that a fact was governed before it was believed, and a tool
that says so without having done it would be worse than no tool at all.

S9.1 adds the three provider adapters, and the interesting part is what they do
*not* agree about: `n`, `temperature`, `seed`, structured output and every usage
field name differ across the three, and a caller sees one `LLMResponse`
regardless. One prompt runs through all three in CI.

Building it turned up the kind of bug this project exists to be afraid of. A
fixed sampling seed made every sample in a K-sample draw come back identical, so
the semantic-entropy term would have scored **maximum confidence on every
candidate, forever** — no error, no exception, a plausible number. Every mock
passed; only a call to a real model showed it.

The next step is **Checkpoint B itself** — via the three dependencies listed
under "The gate that has not run yet", which S9.1 did not supply.

It does **not** need an API key. `GM_ANTHROPIC_API_KEY` has been blank since
S0.2 and a local Ollama model runs the gate for nothing, which is why the
sign-off block has a line for the provider: an AUROC measured on Ollama and one
measured on Claude are two different numbers, and a stated provider is the
difference between a cheap result and a misleading one.

**Nothing in the design suite is evidence of an implemented feature.** All
runtime paths, service URLs, package names, deployment examples, CI gates and
runbook commands describe the *intended* build. Every latency and quality figure
is a target until measured — the measurement happens in week 4, and the honest
numbers replace the targets then.

Two figures worth stating precisely, because they are the ones people quote:

- **Median review time** is owned by `PRD.md` §6.2 as a ≤ 25 s acceptance gate.
  `DESIGN_SYSTEM.md` §3.1 sets a tighter 18 s *design* target, which is not a gate.
- **Coverage** is owned by `RULES.md` §5 — 90% on `guardmem-core` at release,
  with 85% as the Week-1 interim floor.

The single most important milestone is **Checkpoint B** (`BUILD_NOTEBOOK.md`,
Day 5): the AUROC of `C` against 200 hand-labelled candidates must reach 0.80.
If the scoring cannot separate good writes from bad ones, nothing downstream
matters — and that is deliberately learned in week 1, not week 4.

## Licensing

Core engine under **Apache-2.0** — see [LICENSE](LICENSE). The `apps/dashboard`
HITL queue and the SSO/audit-export modules are intended to ship under BUSL-1.1
converting to Apache-2.0 after four years, a standard open-core split. That
dashboard code does not exist yet; the split will be recorded in its own LICENSE
file when it does.

## Security

Please do not open public issues for vulnerabilities. See [SECURITY.md](SECURITY.md).
