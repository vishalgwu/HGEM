# DESIGN_SYSTEM.md — Control Panel & Human Review Queue

Two products live in one app and they are not the same product.

- **Engineer Control Panel** — dense, dark, telemetry-first. The user is looking for anomalies in a
  wall of numbers. Optimize for information density and scanability.
- **Human Review Queue** — calm, light, single-task. The user is a nurse or a paralegal between two
  other jobs. Optimize for *time-to-decision* and for never showing them a number they'd have to
  interpret.

Sharing a component library across those two is fine. Sharing a layout philosophy is not.

---

## 1. Foundations

### 1.1 Tokens

```css
/* semantic, not literal — never use a raw hex in a component */
--surface-0: #0B0D10;  --surface-1: #12151A;  --surface-2: #1A1F26;  /* control panel */
--paper-0:   #FFFFFF;  --paper-1:   #F7F8FA;  --paper-2:   #EDEFF3;  /* review queue */

--text-hi: #E8EBF0; --text-mid: #9BA4B2; --text-lo: #6B7482;

--decision-auto:   #2FB380;   /* auto-write   — green */
--decision-review: #E0A63B;   /* hitl         — amber */
--decision-reject: #D95E6A;   /* reject       — rose (not fire-engine red) */
--decision-escal:  #7C6BF0;   /* escalate     — violet */
--quarantine:      #C4553D;   /* security     — burnt orange, used sparingly */

--confidence-scale: linear-gradient(90deg, #D95E6A 0%, #E0A63B 50%, #2FB380 100%);

--radius-sm: 6px; --radius-md: 10px;
--space: 4px;  /* all spacing is a multiple */
```

**Type:** Inter (UI) / JetBrains Mono (ids, spans, JSON). Control panel base 13px / 1.45.
Review queue base 16px / 1.6 — reviewers read prose, engineers scan tables.

**Color rule:** decision color is the only saturated color on a screen. Charts use a muted
sequential ramp so the four decision colors always mean exactly one thing.

### 1.2 Accessibility
WCAG 2.2 AA minimum; AAA on review-queue body text. Decision state is never color-alone — always
color + icon + label. Full keyboard operation with a visible focus ring. Live regions announce queue
updates. Reduced-motion honored (all number transitions become instant).

---

## 2. Engineer Control Panel

### 2.1 Overview layout (desktop ≥1440px)

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ GuardMem  ◈ acme-health   ns: [patient:* ▾]  window: [24h ▾]   ● live   ⌘K              │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ ┌─ CANDIDATES ─┐ ┌─ AUTO-WRITE ─┐ ┌─ HITL ────┐ ┌─ REJECT ──┐ ┌─ COST ────┐ ┌─ p95 ───┐ │
│ │  184,203     │ │  71.4%       │ │  14.8%    │ │  11.9%    │ │ $412.80   │ │ 1.42 s  │ │
│ │  ▲ 6.2%      │ │  ▲ 1.1pt     │ │  ▼ 0.7pt  │ │  ▬        │ │ ▼ 18.4%   │ │ ▲ 90ms  │ │
│ └──────────────┘ └──────────────┘ └───────────┘ └───────────┘ └───────────┘ └─────────┘ │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ EXTRACTION FUNNEL                                    │ DECISION MIX (stacked, hourly)   │
│                                                      │  ▁▂▃▅▆▇▇▆▅▃▂▁▂▃▅▆▇▇▆▅▃▂▁       │
│  ingested   ████████████████████████████  184,203    │  ■ auto ■ hitl ■ reject ■ esc    │
│  denoised   ████████████████████  119,732  (-35.0%)  ├──────────────────────────────────┤
│  extracted  ███████████████  98,441        (-17.8%)  │ CONFIDENCE × RISK  (hex density) │
│  validated  █████████████  87,006          (-11.6%)  │   R ↑  ░░▒▒▓█ ← auto-write zone  │
│  deduped    ████████████  81,552           ( -6.3%)  │      ▒▓██▓▒░                     │
│  written    ██████████  73,449             ( -9.9%)  │   →  C                           │
│  ▸ click any bar to sample 20 dropped items          │   overlays: τ/ρ thresholds  │
├──────────────────────────────────────────────────────┴──────────────────────────────────┤
│ LATENCY (p50/p95/p99 by stage)          │ MODEL ROUTING & FALLBACK                       │
│  extract  ▏▎▍ 140/380/720ms             │  FAST     84.1%  $0.19/1k   err 0.2%   ●       │
│  validate ▏▎  60/180/410ms              │  BALANCED 11.3%  $1.42/1k   err 0.4%   ●       │
│  score    ▏▎▍▌ 210/610/1180ms           │  FRONTIER  4.6%  $9.80/1k   err 0.9%   ●       │
│  write    ▏   30/ 90/210ms              │  fallbacks 27 · breaker trips 2 · cache 43.8%  │
├─────────────────────────────────────────┴────────────────────────────────────────────────┤
│ LIVE TRACE STREAM                                              [pause] [filter: decision]│
│ 14:22:07  tr_9f2a  patient:8812  allergy=penicillin      C .91 R .82  ⟶ HITL   ▸        │
│ 14:22:07  tr_9f2a  patient:8812  pharmacy=CVS #4021      C .94 R .21  ⟶ AUTO   ▸        │
│ 14:22:06  tr_7c11  patient:4410  pcp=Dr. Alvarez         C .61 R .44  ⟶ ESCAL  ▸        │
│ 14:22:05  tr_7c11  org:acme      policy_owner=…          C .88 R .91  ⟶ HITL   ▸        │
│ 14:22:04  tr_2b83  patient:1290  ⚠ injection pattern     —          ⟶ QUARANTINE ▸      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Interaction rules**
- Every metric tile is a filter. Clicking `HITL 14.8%` filters the funnel, the mix chart, and the
  trace stream to HITL in one action.
- The funnel is the differentiated view: it makes *what got thrown away* as visible as what got
  stored. Each stage samples 20 dropped items on click — this is how a team finds an over-eager
  noise filter before it silently costs them recall.
- The C×R hex-density plot with threshold overlays is the tuning surface. Dragging a threshold line
  shows a projected diff ("+2,140 auto-writes/day, −1,900 HITL tasks, est. write-precision 0.965 ±
  0.008") in a preview panel. Apply requires confirm + writes a versioned audited policy change.
- Live stream is SSE, capped at 200 rows, pauses on hover, and never blocks the page on backpressure.

### 2.2 Trace Detail (`/traces/[traceId]`)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ tr_9f2a3c…  patient:8812  ·  1.31 s  ·  $0.0021  ·  4 candidates  ·  [replay] [export]│
├──────────────────────────────────────────────────────────────────────────────────────┤
│ WATERFALL                                                                            │
│ security.preflight   ▇ 41ms                                                          │
│ l1.noise_filter       ▇ 22ms                                                         │
│ l1.extract(K=5,BAL)   ▇▇▇▇▇▇▇ 318ms   cache HIT   in 2,104 / out 388                 │
│ l2.retrieve_neighbors        ▇▇ 74ms                                                 │
│ l2.conflict(NLI)               ▇▇▇▇ 186ms                                            │
│ l3.entropy                          ▇▇▇ 141ms                                        │
│ l3.impact                             ▇ 18ms                                         │
│ decision                               ▏ 2ms                                         │
│ store.write(outbox)                     ▇▇ 88ms                                      │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ CANDIDATE 2/4   allergy = penicillin                            ⟶ HITL_REVIEW        │
│ ┌ WHY ────────────────────────────────────────────────────────────────────────────┐  │
│ │ C 0.91  ██████████████████░░   H_norm .12 · ground .96 · schema 1.0 · corrob .00 │  │
│ │ R 0.82  ████████████████░░░░   impact CRITICAL(floor .80) · pii .5 · fanout .31  │  │
│ │ reason codes: R_ABOVE_RHO_HI, POL_REQUIRE_CORROBORATION                          │  │
│ │ "high confidence, but a critical clinical fact from a single source"             │  │
│ └─────────────────────────────────────────────────────────────────────────────────┘  │
│ K-SAMPLE CLUSTERS  ● penicillin ×5   (single cluster → low entropy)                  │
│ SOURCE SPAN   turn 14, chars 212–271  ▸ "…she said penicillin gives her hives…"       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

`[replay]` re-runs the pinned prompt/model/policy versions and diffs the new decision against the
recorded one — the fastest way to answer "did our change break this?"

---

## 3. Human Review Queue

### 3.1 Design constraints (these drive everything)
1. Reviewer time is the scarcest resource in the system. Design target: **median 18 s** per task.
   This is the bar the interface is built to, deliberately tighter than the ≤ 25 s alpha acceptance
   gate in `PRD.md` §6.2. Gates cite the PRD number; this one drives design decisions.
2. The reviewer must never need to open another tab to verify a claim. Source is on screen.
3. No jargon. No "semantic entropy 0.59". Say *"the model gave different answers when asked
   repeatedly."*
4. Keyboard-first: `J/K` navigate, `A` approve, `E` edit, `R` reject, `S` skip, `?` help.
   Mouse is a fallback, not the path.
5. Nothing is a one-way door: every decision has a 10-second undo, and edits are versioned.

### 3.2 Task view

```
┌───────────────────────────────────────────────────────────────────────────────────────┐
│  Review  ·  12 waiting  ·  you: 34 today, 16s median          [Sarah R., RN] ⌥ settings│
├────────────────┬──────────────────────────────────────────────────────────────────────┤
│ QUEUE          │   ⚠ Needs your judgement          Patient 8812 · 4 min ago            │
│                │                                                                       │
│ ▸ 8812 allergy │   ┌─ PROPOSED ────────────────────────────────────────────────────┐  │
│   CRITICAL     │   │  Allergy:  penicillin  →  hives                                │  │
│   4 min · 26m  │   │  Effective from 12 Mar 2026                                    │  │
│                │   └────────────────────────────────────────────────────────────────┘  │
│   4410 pcp     │                                                                       │
│   MEDIUM       │   ┌─ CURRENTLY ON RECORD ─────────────────────────────────────────┐  │
│   9 min · 51m  │   │  No allergies recorded.       (last updated 2 Feb 2026)        │  │
│                │   └────────────────────────────────────────────────────────────────┘  │
│   acme owner   │                                                                       │
│   HIGH         │   ┌─ WHERE THIS CAME FROM ───────────────────── intake call, turn 14 ┐│
│   14 min · 12m │   │  "…I can't take the pink liquid one, the ~~penicillin~~ —       ││
│                │   │   it gives me ~~hives~~ all up my arms."                        ││
│ ─────────────  │   │                              ▸ play audio 04:12   ▸ full transcript││
│ filters        │   └────────────────────────────────────────────────────────────────┘│
│ ☑ mine         │                                                                       │
│ ☐ critical     │   Flagged because: a critical clinical fact, mentioned once, with no   │
│ ☐ sla < 15m    │   second source to confirm it.                                        │
│                │                                                                       │
│                │   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐                  │
│                │   │ Approve A│ │ Edit   E │ │ Reject R │ │ Skip S │                  │
│                │   └──────────┘ └──────────┘ └──────────┘ └────────┘                  │
│                │   Reject reason (required): ○ not supported by source                 │
│                │                             ○ wrong patient  ○ outdated  ○ other      │
└────────────────┴───────────────────────────────────────────────────────────────────────┘
```

**Component notes**
- `ProvenanceCard` highlights the exact span that produced each field — the highlight is generated
  from `source_span`, never re-derived client-side. Audio/transcript deep-link where the source
  supports it.
- `DiffPane` shows proposed vs incumbent side-by-side for updates, with a supersession warning
  ("approving this will retire the current value") on `ONE`-cardinality predicates.
- **Edit** opens inline typed fields driven by the ontology (coded values get a RxNorm/CPT
  autocomplete) — a reviewer edit is a first-class labelled correction, the highest-value training
  signal in the system, so it must be as cheap as approval.
- `DecisionBar` is sticky at the viewport bottom on narrow screens. Approvals of `CRITICAL` impact
  trigger step-up re-auth (WebAuthn), once per session per namespace.
- SLA is shown as remaining time, not a raw timestamp. Under 15 min it turns amber; it never turns
  red, because panicking reviewers make worse decisions.

### 3.3 States
Empty (`"Queue clear — nice work"` + today's contribution stat), loading (skeleton preserving layout
height so nothing jumps), lease-lost (another reviewer opened it → auto-advance with a toast),
offline (queue read-only, decisions buffer and replay on reconnect), and SLA-breached (moves to a
separate "Overdue" section with escalation context — never silently reordered into the main list).

### 3.4 Reviewer feedback loop
After every 25 decisions, a one-line calibration note: *"Your decisions matched the model 82% of the
time this week."* No leaderboards, no per-reviewer accuracy ranking surfaced to managers by default —
that turns a quality signal into a performance-management tool and reviewers start rubber-stamping.

---

## 4. Shared Component Contracts

| Component | Props (essential) | Rules |
|---|---|---|
| `<DecisionBadge>` | `decision`, `size` | color + icon + text, always all three |
| `<ConfidenceMeter>` | `value`, `breakdown[]`, `audience: "eng"\|"human"` | eng shows the number; human shows a three-step word scale |
| `<ProvenanceCard>` | `sourceHash`, `span`, `verbatim`, `tier` | highlight is server-provided; tier shown as a labelled chip |
| `<DiffPane>` | `incumbent`, `proposed`, `cardinality` | supersession warning is mandatory when cardinality=ONE |
| `<TraceWaterfall>` | `spans[]` | log-scale toggle; hovering a span shows tokens + cost |
| `<ThresholdEditor>` | `thresholds`, `projection` | Apply is disabled until a projection has been computed |

**Frontend stack:** Next.js 15 App Router, React 19 server components for shells, TanStack Query for
client state, Recharts for standard charts, D3 only for the hex-density plot, shadcn/ui primitives,
Tailwind with the tokens above mapped into the theme. SSE for live telemetry (not websockets — it's
one-way and it survives proxies better). All API access through BFF route handlers so no tenant token
ever reaches the browser.

**Performance budgets:** control panel LCP < 1.8 s on a mid-tier laptop, review task view LCP <
1.2 s, interaction-to-next-paint < 200 ms on decision buttons (the decision is optimistic; the
network confirm is what the undo window covers).
