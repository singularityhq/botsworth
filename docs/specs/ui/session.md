# Session blade: the stack below the explorer, the header, the turn list and the finders (S1b)

Status: draft for implementation, 2026-10-12. Owner: spec writer S1b.
The session blade is blade 1 of the stack. It answers "where did this session's money go" and "where did its time go", lists the session's turns in order with the waits between them, and finds the step the user wants (D33). The turn blade (`turn.md`) and the detail blade (`detail.md`) open to its right.

This file also owns the rules that the session, turn and detail blades share: the drill-down navigation (§1), the cost rule, the time states, the gap kinds and their colours (§2), and the finder keys (§6). `turn.md` and `detail.md` cite them as `SES-<n>` and do not restate them.

Visual rules, formats, glyphs, the P1–P4 patterns, the fold row, blade mechanics and global keys are in `system.md` (`SYS-<n>`). Requirement IDs here are `SES-<n>`.

Sources, in priority order: the decision log (D29, D31, D33, D34), `prototype notes (not in this repo)/CONSOLIDATION-NOTES.md`, `docs/specs/data-model.md` (S2, **DM-<n>**; for stored facts), then `prototype notes/REVIEW-v4-steps-r3.md` (**R3**, the primary design; time-gap option A) and its prototype `spike-steps-r3/` (`app.js`, `app.css`, `timeline.js`; **R3-proto**), `REVIEW-v4-session-r2.md` (**SESS-R**), `REVIEW-v4-turn-r2.md` (**TURN-R**), `evidence-turn-data.md`. Decisions, CONSOLIDATION-NOTES and S2 override the reviews and the prototype. Where the prototype and R3's text differ, R3's text wins.

Backend data that does not exist yet is marked **needs S2/S3** with a proposed field name. This file does not design the API.

## 0. Terms

Model terms (session, engagement, turn, llm_call, tool_call, step) are those of `docs/design/design.md` §4; turn kinds, `display_ix`, stretches and attributed turns are those of S2 §0. Terms new here:

| Term | Meaning |
|---|---|
| **Main turn** | A turn of the session's main transcript (S2 §0). Only main turns are listed in the session blade. |
| **Numbered turn** | A main turn whose kind is `human` or `message`. It has a `display_ix` and a row, and it can open (DM-24). |
| **Harness turn** | A main turn whose kind is `harness` (`/clear`, an interrupt, an empty record, a built-in command). It never opens and has no number (D34). |
| **Turn span** | `[turn.start_ms, turn.end_ms]`: from the turn's first record to the end of its last activity, including the agents it caused. Used for a turn's Wall, Waiting and time split (SES-21). |
| **Row** | One line of the turn blade: a model call, a tool call, an agent run, or a gap (`turn.md` §3). A row is the unit of the cursor. Thinking and text are not rows; they belong to their model call (SESS-R §7). |
| **Gap** | A stretch of at least 2 minutes in which nothing ran on the main thread, shown as a row (SES-14). |
| **Wall state** | One of the seven states of SES-10 that every instant of a session's wall has. |
| **Cursor** | The one selected row (turn blade) or the one selected turn (session blade). Shown per SYS-35. |
| **Finder** | A key or chip that moves the cursor to the next row of one kind: `b`, `w`, `e`, `s`, `a` (SES-50 to SES-56). |
| **Measure** | Cost, Time or Tokens. It drives the bars of the turn list and the turn ledger, and `b` (SES-31, SES-52). Distinct from the explorer's measure (EXP-31), which has no Time. |

## 1. Blade stack and navigation (session → turn → detail)

SES-1 — Drill-down. Opening a session from the explorer adds the session blade (blade 1). Opening a turn adds the turn blade (blade 2). Opening a row adds the detail blade (blade 3). Opening from a blade that is not the rightmost closes every blade to its right first (SYS-45; D33).

SES-2 — Blade strip (the maintainer 2026-10-12, binding; Azure-style continuous blades, Miller columns; the pattern itself is system.md's blade section, SYS-46; D38 L2). The blades form one horizontal strip:
- every parent blade has one standard compressed width, **400 px**; the newest (rightmost) blade takes the rest of the stage, **minimum 640 px**;
- the strip is scrolled so the newest blade and its immediate parent are in view; older blades sit off-screen to the left at their 400 px width. There are **no spines and no collapse**;
- off-screen blades are reachable by horizontal scroll of the strip, by the breadcrumb (SES-7), by ← / → (SES-5) and by Esc (SES-6);
- when the stage is narrower than 400 + 12 + 640, the parent is scrolled partly off-screen so the newest blade keeps 640 px.

| Open blades | In view at W = 1416 (1440 viewport) |
|---|---|
| explorer, session | explorer 400 + 12 + session 1004 |
| explorer, session, turn | session 400 + 12 + turn 1004 (explorer off-screen left) |
| explorer, session, turn, detail | turn 400 + 12 + detail 1004 (explorer and session off-screen left) |

The turn blade at 400 px stays readable: it shows the compact row form (TRN-1). Every path that opens a turn (click or Enter in the Ledger or Waterfall, a finder, a legend jump, `j` across a turn end from the session) gives the session-400 + turn layout; a turn is never opened with the session off-screen. Opening a row's detail moves only the session off-screen.

SES-3 — One transition. Every push (a blade added) and pop (a blade closed) uses the same transition: the strip slides left (push) or right (pop) while widths change, 280 ms, `cubic-bezier(.22,1,.36,1)`; instant under reduced motion (SYS-59). Closing a blade restores the previous layout exactly: the same blades in view, the same scroll position inside each blade, and the same cursor and selection.

SES-4 — Session rail (400 px), the session's compressed form while it is a parent blade (R3 §5; R3-proto `sessionBlade("rail")`):
- head (16 px padding, hairline below): line 1 = project dot + name (ink3), spacer, session cost (money, mono), session wall (mono ink3); line 2 = the display name, 14/600 ink1, one line, ellipsis, full name in `title`;
- body: one compact row per numbered turn that the current search and range keep (SES-32, SES-30), in order. Gap rows and harness folds are not shown in the rail;
- compact turn row (SYS-36): two lines, padding 8 px 16 px 8 px 12 px, hairline below. Line 1: turn number (mono ink3, 24 px column), turn text (14/500 ink1, ellipsis, full text in `title`), cost (money). Line 2 (ink3): start clock, wall, and `hourglass` + Waiting when Waiting ≥ 1 min, then the flags of SES-33 in compact form;
- the open turn's row has the open state (struct-soft fill, text in struct; SYS-35); when the turn blade changes turn (TRN-50), the rail's open row follows and scrolls into view.
- clicking a rail row opens that turn in the turn blade (SES-1); the detail blade, if open, closes.

SES-5 — Focus (SYS-48). ← and → move focus to the next blade left or right and scroll the strip so the focused blade is in view. On open, the new blade takes focus: the session blade when it opens, the turn blade when a turn opens, the detail blade when a row opens. ← and → move focus to the blade on the left or right (SYS-50). `j`/`k` act on the focused blade: in the session blade or rail they move the turn cursor; in the turn blade or the detail blade they move the row cursor, and an open detail blade follows the cursor (R3 §3; TURN-R top 5 #2).

SES-6 — Esc, in this order, the first that applies (extends SYS-50; R3-proto `back()`):
1. the palette, a popover, or the `?` overlay: close it;
2. a focused text field: blur it (the search text stays);
3. the detail blade: close it;
4. a zoom on the turn histogram: reset it (`turn.md` TRN-24);
5. the turn blade: close it; the session blade's cursor moves to that turn;
6. a search in the session blade: clear it;
7. a range on the session histogram: clear it (SES-30);
7a. [D56] the Review view: close it back to the list (review.md REV-3);
8. the session blade: close it (SYS-50 "the rightmost blade").
  Scenario: Given a session with search `pytest`, turn 4 open, and the detail open, when the user presses Esc three times, then the detail closes, then the turn closes with turn 4 selected, then the search clears.

SES-7 — Breadcrumb (SYS-49): `Sessions › <session display name> › Turn <N> › <detail label>`. The detail label is the row's name (`turn.md` TRN-30), cut at 44 characters. Clicking a crumb closes every blade to its right.

SES-8 — URL state. The blades add these query parameters to the explorer's (EXP-74). Parameters at their default are omitted (R3-proto `render()`; TURN-R §5):

| Param | Values | Default | Blade |
|---|---|---|---|
| `s` | session key (`op_id` of the session; an 8-character id prefix is accepted on load) | none (no session blade) | session |
| `bars` | `time`, `tokens` | `cost` | session and turn (the measure) |
| `find` | search text | empty | session and turn |
| `range` | `<start_ms>-<end_ms>` (session histogram range) | none | session |
| `over` | `1` (session "Over time" open) | closed | session |
| `sview` | `waterfall`, `review` [D56] | `ledger` | session |
| `review` | `1` (the scope review blade, review.md REV-21) [D56] | closed | scope review |
| `sreal` | `1` (session Waterfall on real time) | folded | session |
| `info` | `1` (session "Details" open) | closed | session |
| `t` | the turn's `display_ix` | none (no turn blade) | turn |
| `step` | row id (SES-9) | the turn's first row | turn |
| `view` | `waterfall` | `ledger` | turn |
| `order` | `cost`, `duration` | as it ran | turn |
| `real` | `1` (Waterfall on real time) | folded | turn |
| `zoom` | `<start_ms>-<end_ms>` | none | turn |
| `thist` | `0`, `1` (overrides the histogram rule, TRN-20) | the rule | turn |
| `tinfo` | `1` (turn "Details" open) | closed | turn |
| `detail` | `1` (detail blade open on `step`) | closed | detail |

The message detail (DET-50) is encoded as `step` = the turn's op_id (a RowRef of type 'turn', SES-9) with `detail=1`, so it reopens after a reload (D38 L6).

Which agents' rows are shown inline (`l`) and which quiet folds are open are not in the URL.
  Scenario: Given `?s=a11ce5e5&t=2&step=gap-1791790200000&detail=1`, when the page loads, then the session, turn 2 and the detail of that gap open, and the gap row is the cursor.

SES-9 — Row ids for `step` (TURN-R §5 "Landing from the session cursor"): the `op_id` of a model call, tool call or engagement (agent row); `gap-<start_ms>` for a gap; the turn's own `op_id` for the turn's message (RowRef type 'turn'; D38 L6). On load, `step` also accepts a provider request id, a `tool_use` id or an engagement id, and the URL is rewritten to the `op_id` form. A `step` that names a row in another turn of the session opens that turn instead of `t`. A `step` that names no row is dropped and the turn opens at its first row. The rows inside an agent that `step` names are shown inline (`l`) so the cursor is visible.
  Data: resolve a request id, tool_use id or engagement id to its row and turn (needs S3).

SES-10 — History (EXP-76). Opening a blade (session, turn, detail) adds a history entry, so browser Back closes the rightmost blade. Moving the cursor, swapping the turn in place (TRN-50, SES-56), and changing measure, order, view, search, range or zoom replace the URL without a new entry. [D56] Exception: a Review evidence link (REV-6) adds an entry, so Back returns to Review (`sview=review`) as it does on the scope review (REV-22).

SES-11 — Unknown values (EXP-75). A parameter with an unknown value is ignored and the URL is rewritten without it. An `s` that names no session shows the error state of SES-80. A `t` that names no numbered turn is dropped; the session blade opens alone.

## 2. Shared data rules

### 2.1 Cost

SES-12 — Cost rule (D34; DM-45; R3 §7 "Cost rule"). A turn's cost is its attributed cost (`attr_cost_usd`): its own main-thread model calls plus the **whole** cost of every engagement that its Agent (spawn) or SendMessage tool calls caused, even when that agent keeps running after the turn ends. Every turn figure (row, header, rail, `b` ranking) uses this rule, except inside a brushed zoom (SES-13).
- Main-thread part = the turn's own main-thread calls. Agents part = the rest.
- An agent row's cost is the whole cost of its engagement, including the engagement's own agents (`turn.md` TRN-33). So a turn's agent rows sum to the turn's agents part.
- Σ over the session's turns + the cost of agent work with no turn (DM-45 "unattributed") = the session's cost.
  Scenario: Given the 78-turn session, turn 35 (`main#38`), then its row reads $25.99 = own $0.88 + auth-stage2 $19.88 + auth-readiness $3.28 + auth-invite-mail $1.95, and the turn rows plus the unattributed figure sum to $259.37.

SES-13 — Clock rule, for a brushed range only (R3 §7). Inside a range on a histogram (SES-30, TRN-24) the cost figures are the sum of model calls, main thread and agents, that **start** inside the range. The agents caption then reads "agent calls in range" with the tooltip "Agent model calls that started inside the range" instead of "agents it started" (SES-17).

SES-14 — The session cost split: main thread = Σ cost of the session's main-transcript model calls; agents = the session's cost − main thread.

### 2.2 Time states

SES-15 — Every instant of a session's wall has exactly one of seven wall states, some with a substate (R3 §4; DM-50 [D54] for all of them):

| State | Label | Legend short form | Solid or hatched | When |
|---|---|---|---|---|
| `model` | Model working | model | solid | a main-thread model call runs |
| `tool` | Tools running | tools | solid | no main-thread call, a main-thread tool call runs; substate `background` [D55] when only a main-thread background command runs (and no agent), else none |
| `agents` | Only agents running | agents | solid | nothing on the main thread, an agent's call, tool or background command runs |
| `you` | Waiting on you | waiting on you | hatched | nothing else runs, and either a question tool (substate `ask`) or a permission prompt (substate `permission`) is open on a main-thread or agent call (DM-40), or nothing runs and the stretch ends where a `human` turn starts and SES-17 does not apply (substate `reply`) |
| `agentmsg` | Waiting for an agent's message | waiting on agent | hatched | nothing runs, and the stretch ends where a `message` turn with origin `peer`, `coordinator`, `teammate` or `agent` starts |
| `limit` | Usage limit (inferred) | usage limit | hatched | nothing runs, and SES-17 applies |
| `none` | Nothing running | nothing running | hatched | nothing runs, in every other case (inside a turn, before a harness, plugin, `sdk`, `system` or `exec` turn, or at the session's end); substate `stalled` [D55] while a finished task's notice waits for a thread that is not running (SES-19a), `before_first` before the first numbered turn (SES-18), else `none` |

Solid means something was recorded as running; hatched means nothing was, or a call was waiting on you (R3 top 5 #1; D54). A permission wait includes the approved command's run, which has no record (DM-40). The `you`, `agentmsg` and `limit` states are given only on recorded facts: a call that waited on the user (DM-40), or the kind of the next turn (D34 as amended by D54; S2 Q6), never as a cause.
  Substate wording, used in tooltips, gap reasons and the detail blade: `you`/`ask` "asked you a question", `you`/`permission` "waited for your approval", `you`/`reply` "the next turn is your message", `tool`/`background` "a background command ran" [D55], `none`/`stalled` "a finished task waited for an agent that was not running" [D55], `tool`/`test` and `agents`/`test` "a test run ran" [D56], `tool`/`wait` and `agents`/`wait` "a sleep or polling command ran" [D56]. The legend shows one item per state with the substates summed, except `none`/`stalled`, which has its own item "stalled" [D56] (SES-26); for `you`, `tool`, `agents` and `none` its tooltip lists the substates with their durations.
  Data: per session the `time_split` parts (DM-51); stretches with state and substate (DM-52); the next turn's origin and label for each stretch (DM-55).

SES-16 — Stretch thresholds (R3 §4; R3-proto `GAP_MIN`; DM-52):
- the store keeps stretches of 60 s or more that no main-thread call covers, with `state` and `substate` columns (DM-52; D37; D54);
- a stretch becomes a gap row only at **2 min** or more;
- shorter stretches count only in the totals, the split bars and the Waiting figure.
The 60 s threshold applies to stretches only: every instant counts in the totals (DM-50, DM-51), so a 30 s pause before the user's message counts as `you`/`reply`.

SES-17 — Usage-limit inference (R3 §7 "Usage-limit time is inferred"; R3-proto `RESUME`). A `none` stretch is `limit` when the turn that starts at its end is a `human` turn whose text, trimmed, starts with one of (case-insensitive): `I hit my usage limit`, `Please continue from where you left off`, `continue from where you left off`. The label always carries "(inferred)" in full form, and the gap detail says why (DET-41). The turn that starts there has the kind "Resume after usage limit" (SES-32).
  Scenario: Given session a11ce5e5, when the session opens, then a row "1h02m Usage limit (inferred) · the next turn resumes after a usage limit" sits directly above the resume turn (R3 frame `time-A-usage-limit`), and the legend shows "usage limit 1h02m".
  Not covered: a limit that ends a session segment (a resume gap) lies outside the wall and produces no stretch (DM-52). See Open Q4.

SES-19a [D55] — Stalled. A `none`/`stalled` stretch (DM-50 rule 5) is a gap of kind "Stalled" with the reason "a finished task waited for an agent that was not running". [D56] It has its own split-bar segment and legend item "stalled" (SES-26) and does not count in the `none` item; its gap bar (and its Waterfall bar) uses the same fill, `--color-error` hatched (REV-1). A `tool`/`background` stretch is a gap of kind "Background command" with the reason "a background command ran; nothing else did"; it counts under `tool` and in A of SES-26 (it is running time). Like an agents-only stretch (SES-19), it is a gap row in the turn ledger and never a row between turns in the session list (SES-36). [D56] A `tool`/`test` stretch (a background test run) is the same kind with the reason "a background test run ran; nothing else did". An `agents` stretch keeps the kind "Only agents running" whatever its substate.

SES-18 — Before the first message. A `none`/`before_first` stretch (DM-50 rule 5) is a gap of kind "Before the first message". It is never in the `you` total: the legend counts it under `none`, whatever the first numbered turn's kind (R3 §7 "Gap order"; D41 M11).

SES-19 — Agents-only stretches. A maximal stretch of state `agents` of 2 min or more is a gap of kind "Only agents running" in the turn ledger (`turn.md` TRN-34) and a target of `w` and of the "agents" legend jump (R3-proto `buildFlow`, `jumpState`). It is never a row between turns in the session list (SES-36).
  Data: `stretches` rows with state `agents`, with start and end (D37). The engagements running come from the gap detail's agent list (S3 Q7, D37).

SES-20 — State colours. New tokens for the seven states (R3-proto `app.css`; contrast measured in R3 §6 on l1):

| Token | Value | Fill | On l1 |
|---|---|---|---|
| `--st-model` | #aab0c8 | solid | 8.49 |
| `--st-tool` | #6b7899 | solid | 4.15 |
| `--st-agents` | #c6aee8 (= `--color-type-team`) | solid | 9.24 |
| `--st-you` | #66d3d4 (= `--color-type-user`) | hatched | 10.30 |
| `--st-agentmsg` | #c6aee8 (= `--color-type-team`) | hatched | 9.24 |
| `--st-limit` | #62b9f4 (= `--color-type-sys`) | hatched | 8.50 |
| `--st-none` | #5c6888 | hatched | 3.30 |

Hatch: `repeating-linear-gradient(135deg, <colour> 0 2px, transparent 2px 5px)`; in split bars and gap bars a hatched segment also has a 1 px inset outline in its colour. [D56] A `none`/`stalled` stretch is drawn in `--color-error`, hatched, wherever stretches are drawn (REV-1). Hatching means only "nothing ran" and is used nowhere else (R3 §5 "Colour axes"). The kind hues keep their meaning (teal you, lavender agents, sky system; SYS-9).

SES-21 — Turn time figures use the turn span (R3 §7: "the time split stays clipped to the turn's wall clock"; R3-proto `turnRange`):
- **Wall** = `end_ms − start_ms`;
- **Waiting** = Wall − (main-thread model time + main-thread tool time inside the span) = the time inside the span when nothing ran on the main thread (R3 top 5 #2);
- the turn's state split covers only the span; a wait after the turn's last activity is a gap row between turns (SES-36), not part of the turn's Wall.
An agent that outlives its turn adds cost to the turn (SES-12) but adds wall time only up to `end_ms`.
  Data: per main turn, the four state times over the span (needs S2/S3; DM-51 computes them over the window `[start, next turn start)`, which includes the trailing wait).

### 2.3 Model ramp for charts stacked by model

SES-22 — Chart-only model colours. This resolves system.md Q3 (SYS-12). Charts stacked by model (the turn histogram, `turn.md` TRN-22) and the model-call bars of the Waterfall (TRN-41) use one lightness ramp of cool neutrals. No value equals a project series colour, a kind colour, struct, money or error (CONSOLIDATION-NOTES: "a separate neutral or ink ramp per model family, and the legend carries the shape mark"):

| Token | Family (SYS-41 shape) | Value | On l1 #0e1522 | On l2 #121a29 |
|---|---|---|---|---|
| `--chart-model-opus` | ◆ opus | #e1e4ef | 14.40 | 13.72 |
| `--chart-model-sonnet` | ● sonnet | #b6bdd4 | 9.76 | 9.31 |
| `--chart-model-fable` | ■ fable | #949cb9 | 6.71 | 6.40 |
| `--chart-model-haiku` | ▲ haiku | #7a84a6 | 4.94 | 4.71 |
| `--chart-model-gpt` | ⬡ gpt | #a39a86 | 6.55 | 6.24 |
| `--chart-model-other` | small ● other, unknown | #646f94 | 3.69 | 3.52 |

Rules: stacked segments are separated by a 1 px gap of the chart's surface; the stack order, bottom to top, is the order of the table; the chart legend shows each family present as its P4 mark (shape in the family's chart colour, then the short name in ink2) with its total for the chart's measure; the colour is never the only cue (the tooltip names the model). The P4 mark in rows and text stays one ink (SYS-41); these colours appear only in charts. Measured with `refine-ui/scripts/check_contrast.py` (all ≥ 3:1 graphics floor).
Known closeness: `--chart-model-sonnet` and `--st-model` (#aab0c8) are near in value; they never share a band (bars above, state lane below, each labelled in the caption).

## 3. Session header

SES-23 — Layout, top to bottom (R3 §1.2, §5 "Attention order"): meta line; title; the two answers (cost left, time right); then, when open, the Details panel and the Over time histogram; then the finder line (§4); then the column header and the turn list (§5). Padding 16 px 24 px 12 px.

SES-24 — Meta line (14 px ink3, 8 px gaps, 28 px min height; R3-proto `sessionBlade`): project dot + project name (ink2); start date (`Ddd Mmm D`); clock range (`HH:MM–HH:MM`, `+Nd` per SYS-29); "N turns" (numbered turns only); when a session range is set, a scope chip (SES-27); spacer; "Over time" and "Details" disclosure buttons (`chevron-right` rotating 90° when open, ink3, struct on hover); a `keyboard` icon button with tooltip "Keyboard (?)" that opens the `?` overlay (SES-72).

SES-25 — Title: the session display name (SYS-30), 20/600 ink1, one line with ellipsis; the full text in `title`. The fallback form (`project · id8`) is mono 14/400 ink3 per SYS-30 (R3 §1.2 "Title (2 lines) → keep at 1 line").

SES-26 — The two answers (R3 top 5 #1; R3-proto `answers()`). A two-column grid, `minmax(220px, 1fr)` and `minmax(0, 2.2fr)`, 40 px gap.
Exception to SYS-42 (D38 L5): the session header replaces the stat strip with the two answers below and the stat line of SES-28, in this item order.
- **Cost (left).** The session cost as the one display figure (30/500 mono money, SYS-15); `$x+` per SYS-32 when it excludes unpriced calls. Under it an 8 px split bar on l2 (radius 4, 2 px gaps): main thread = solid `--color-money`; agents = `--color-money-bar` with a 1 px inset `--color-money` outline. Under the bar, the caption: "main thread **$X**" and, when agents ≥ $0.01, "agents **$Y**" (figures mono money 500). A session with no priced model call shows `—` and "no model calls" (S2 §5).
- **Time (right).** Line 1: the session wall as a display figure (30/500 mono ink1) + "wall" (14 ink3) + "**A** running · **B** not running" (14 ink2), where A = model + tools + agents and B = wall − A ([D56] "not running", so the line does not reuse the `none` state's label "nothing running" for a different figure). Then an 8 px split bar of the seven states in the order of SES-15, each segment's width ∝ its time, segments under 2 px drawn at 2 px, hatching per SES-20, tooltip "<label> <duration>". [D56] `none`/`stalled` is its own segment, directly before `none`, filled as REV-1's `stalled` (`--color-error`, hatched), tooltip "Stalled <duration>", so the header, the ledger and Review colour a stall alike. Then the legend: the states with a total of 30 s or more, largest first, at most 4; [D56] "stalled" (the `none`/`stalled` time) is an item of its own, listed first when it is 30 s or more, and the `none` item ("nothing running") excludes it. Each legend item is a button: a 10 px swatch (solid or hatched), the short form (ink3), the duration (mono ink1 500); tooltip "Go to the longest stretch of: <label>"; hover struct.
  Verify: the seven segments sum to the wall; the legend never shows a state below 30 s.

SES-27 — Legend jump (R3 top 5 #1; R3-proto `jumpState`). Clicking a legend item:
- `model` / `tool`: goes to the longest main-thread model-call row / tool row (`user_wait` NULL) of the session (SES-50 "go to");
- `you`: goes to the longest of the `you` gap rows and the main-thread tool rows that waited on the user (QRY-29; D54);
- `agents`, `agentmsg`, `limit`, `none`: goes to the longest gap row of that state (SES-15, SES-19);
- every legend jump goes to the longest row or stretch of that state (D38 H2, amending D37; QRY-29), and the wait states (`you`, `agentmsg`, `limit`, `none`) need a stretch of at least 2 min;
- the `none` jump covers the before-first gap of SES-18 too, since that gap counts under `none` (D41 M11);
- opens the turn that holds it, makes it the cursor, and opens its detail blade;
- toast: "Longest stretch of <short form>: **<duration>** at HH:MM · turn N" (for model/tool: "Longest model call: **<duration>** · turn N", "Longest tool run: …");
- when the state has no single stretch of 2 min or more: no navigation; toast "No single stretch of <short form> is 2 min or longer; the total is many short pauses."
  Scenario: Given a11ce5e5, when the user clicks "usage limit 1h02m", then the turn that holds that gap opens with the 1h02m gap row as the cursor, its detail blade is open, and the toast reads "Longest stretch of usage limit: 1h02m at HH:MM · turn N" with the gap's start and that turn's number.

SES-28 — Details (disclosure, closed by default; R3 §1.2; R3-proto `modelsTable`). Panel below the answers, padding 12 px 24 px 16 px, hairline below:
- a stat line (an exception to SYS-42's item order, D38 L5; 14 px; `icon + mono figure (ink1 500) + word (ink3)`, 20 px gaps): `coins` tokens · `arrow-left-right` model calls · `activity` active (the store's `active_ms`) · `bot` agents (distinct sub-agents);
- when agent work has no turn (SES-12): "Agent work with no turn **$X**" (money);
- the model table (columns Model, Calls, Cost, Model time, Speed): one row per model, by cost descending; Model = P4 mark; Speed = Σ out tokens / Σ model time as "N tok/s" (DM-47), blank when model time is 0; ttft only for models where it exists (DM-37), as an extra column "First token" with the mean;
- one `info` line (ink3) when any listed model has speed basis `derived`: "Model time for <provider> is taken from log record times, so speed is approximate." (DM-37, DM-47; R3-proto note).
  Data: per-session model lines with calls, cost, model time, speed, basis (DM-47, computed by S3).

SES-29 — Over time (disclosure, closed by default; R3 §1.2 "Heat strip → Over time"; R3-proto `drawHist`). A 104 px histogram over the session wall, inset 24 px:
- x axis: wall clock from the session's first to last record; every wait-state stretch (`you`, `agentmsg`, `limit`, `none`) of 10 min or more is folded to a fixed 24 px column (l3 fill, a centred `≈` in ink3);
- bars: 3 px bins along the drawn axis, 2 px wide; height **linear** in the bin's sum of the measure (cost; tokens when the measure is Tokens; cost when it is Time), minimum 2 px for a non-empty bin; colour `--color-money` for cost, ink2 for tokens; not stacked by model (SYS-12: only the turn histogram stacks);
- state lane: an 8 px band under the bars with every state segment in its SES-20 colour (hatched states at 55 % opacity);
- marks: a 4 px ink3 tick at each numbered turn's start, above the bars; an ink1 downward triangle (8 px wide, 7 px tall) at each skill load (SES-55); a 2 px struct line at the cursor's position when a turn is open;
- axis labels: 12 px mono ink3 clock times at 5, 15, 30, 60, 120, 240 or 480 min steps (the first step that gives fewer than 9 labels), at least 44 px apart, none inside a fold;
- caption line above (14 px): "Over time" (600 ink1), "bars cost" or "bars tokens", "lane: what ran", "≈ wait over 10 min, folded", spacer, "Drag across to show the turns in that range" (ink3).
The bars are linear (D31 "cost bars linear everywhere"); the prototype's square-root scale is not kept (SESS-R §4).

SES-30 — Session range (R3 §1.2; R3-proto `hookBrush`, `sRange`). Dragging across the session histogram by 4 px or more sets a range. The turn list then keeps only turns whose span overlaps the range; the meta line shows a scope chip (struct text on struct-soft, `clock` + `HH:MM–HH:MM` + an `x` button with tooltip "All turns (0)"). `0`, the chip's `x`, or Esc (SES-6 step 7) clears it. A drag under 4 px does nothing. The range does not open a turn (R3 §8). Mouse only (Open Q6).

## 4. Finder line (session)

SES-31 — One line under the header (R3 §1.2; R3-proto `finder`), 8 px gaps, left to right:
- **search field** (300 px, 32 px tall, edged per SYS-13): `search` icon, placeholder "Find a step: command, file, tool, text", `/` key cap;
- **measure segment** "Cost | Time | Tokens" (tooltips "Bars and Biggest by cost (1)" etc.);
- **Biggest** chip: `crosshair` + "Biggest" + `b` cap, tooltip "Next biggest turn (b)" (glyph: Open Q5);
- the **finder chips**, each hidden when its count is 0: `hourglass` N "waits" (`w`); `circle-alert` N "failed" (`e`; the glyph and the word in `--color-error`, SYS-8); `book-open` N "skill loads" (`s`); `bot` N "agents" (`a`). A chip is `icon + mono count + word + key cap`, 32 px tall, ink2, hover l3. Clicking a chip is the same as its key.
Key caps on the chips are an exception to SYS-50's "key hints in tooltips only" (R3 §1.2 "the chips carry their own key caps").
  Counts (session scope): waits = wait stretches of every state that last at least 2 min (SES-36, SES-19); failed = failed tool rows at any depth (TRN-32); skill loads = skill-load rows (SES-55); agents = agent rows at any depth (TRN-33). Each finder has one hit set (D38 M4), and the chip count, the per-turn counts and the key all read that set (QRY-38). Until the finder request returns, the chips show no count (D38 M8). If a finder request fails, its chip shows `circle-alert` in ink3 with no count, and its tooltip reads "Couldn't load. Click to retry"; a click retries the request (D41 M8).

SES-32 — Search (R3 §1.1 U3; R3-proto `matches`). Typing filters as you type (no submit). Matching is a case-insensitive substring test of the search text against each row's stored text: the row name, its sub-text and target, the tool name, the tool's command or file path, the step summary and snippet, the agent name, and the turn's label (DM fields; full thinking, full output and full asks are not searched, Open Q3).
- The turn list keeps only turns with at least one matching row; the flags slot of each kept turn shows `search` + the number of matching rows (tooltip "rows matching “x”"), in place of the other flags; the column header reads `Turns with “x”`.
- Enter or ↓ in the field: blurs it and moves the turn cursor to the first kept turn; the next Enter opens it.
- Esc in the field: blurs it; the search stays (SES-6).
- The search carries into the turn blade (TRN-12) and into the finders (SES-50).
- No match: the list area shows `search-x` 24 px ink3 and "No turn has a step matching “x”." (14 ink2).
  Data: matchable text per row (needs S3: a search over steps and ops of one session, returning matching row ids per turn).

## 5. Turn list (time-gap option A)

SES-33 — Column header (32 px, ink3, hairline below; R3-proto `.cols.sgrid`): When · (kind) · "Turns" (or `Turns with “x”`) · (flags) · "by cost|time|tokens" · Cost · Wall · Waiting · Tokens. The grid is `56px 20px minmax(0,1fr) 112px 104px 72px 64px 64px 56px 16px`, 12 px gap, padding 0 16 px 0 20 px. It keeps SYS-33's slot order with these widths: slot 4 is the measure bar, slot 5 the cost figure, and the Waiting figure takes the Active slot of slot 6 (R3 §1.2 "Turn row columns: Wall, Waiting, Tokens").

SES-34 — Turn row (one per numbered turn; 44 px min; R3 §1.2; SYS-33, SYS-35):
1. **When**: start clock `HH:MM` (mono ink3); the weekday when the local day differs from the row above (SYS-34);
2. **Kind**: the 16 px kind glyph, shown only when the kinds in the list vary (SYS-33). Kinds and glyphs (SYS-28; S2 DM-20 kinds and origins):

| Kind shown | From | Glyph | Label |
|---|---|---|---|
| Your message | `human`, origin `typed` or `queued` | `message-square-text` | Your message |
| Slash command | `human`, origin `slash` | `square-slash` | Slash command |
| Resume after usage limit | `human`, text per SES-17 | `rotate-ccw` | Resume after usage limit |
| Message from an agent | `message`, origin `peer`, `coordinator`, `teammate`, `agent` | `messages-square` | Message from an agent |
| Plugin or system message | `message`, origin `plugin:*`, `system` | `plug` | Plugin message / System message |
| Scripted prompt | `message`, origin `sdk`, `exec` | `terminal` | Scripted prompt |

3. **Name**: the turn number (`display_ix`, mono ink3) then the turn text (the turn label, DM-21–23), one line, ellipsis, the full label in `title`. "Your message", "Slash command" and "Resume" rows are 14/500 ink1; other kinds are 14/400 ink2 (R3-proto `.muted`);
4. **Flags** (fixed order, each hidden at 0, each with a tooltip): the compact model marks (SYS-41 shape only) of the turn's main-thread models, most costly first, at most 2 then "+N"; `bot` N agents; `book-open` N skill loads; `circle-alert` N failed (glyph `--color-error`). With a search active, this slot shows only `search` N (SES-32). The model marks follow D33 ("Model shown on turns and agents"), which overrides R3's removal (Open Q7);
5. **Measure bar** (SYS-39): linear against the largest visible turn for the measure; cost bars `--list-bar`, time and token bars ink2; right-anchored, 8 px tall;
6. **Cost** (money; SES-12); `$x+` per SYS-32; blank when the turn has no priced call;
7. **Wall** (mono ink2; SES-21);
8. **Waiting** (mono ink3; SES-21), shown only when ≥ 30 s; tooltip "Inside the turn, nothing ran on the main thread for this long";
9. **Tokens** (mono ink3; `—` when the turn's calls have null tokens, S2 §5);
10. `chevron-right` (ink3).
States per SYS-35: hover l3; cursor = 2 px struct outline; open = struct-soft fill and name in struct. Click opens the turn (SES-1).

SES-35 — Harness folds (D34; SESS-R §7 P2; DM-24). Consecutive harness turns fold into one 32 px row in their time position: `list-collapse` (ink3) + "N session events" + their labels joined by ", " (ink3, one line, ellipsis), e.g. "2 session events: /clear, Request interrupted". The fold shows the summed cost of its turns in the Cost column when it is ≥ $0.01 (so the list reconciles with the header, DM-24 "no cost is lost"). Clicking the fold expands it in place into one 32 px ink3 row per harness turn (clock, `list-collapse`, label); a second click folds it. Harness rows have no number, never open, are not cursor stops, and are hidden in the rail.

SES-36 — Gap rows between turns (option A; R3 top 5 #2, top 5 #5, §7 "Gap order"; R3-proto `gapBetween`). Every `none` stretch of 2 min or more that ends where a main turn starts, or that lies before the first numbered turn (SES-18), is a row in the turn list:
- **position**: by time. A gap that ends at a turn's start sits directly above that turn. A gap that lies after a turn's last activity and ends at a harness turn sits above that harness fold;
- **row** (44 px, l2 fill): When = the gap's start clock; glyph by kind (`hourglass` for Waiting on you, Nothing running and Before the first message; `timer-off` for Usage limit; `ear` for Waiting for an agent's message (D38 L4: no other row of the turn list uses these glyphs; SYS-28)); name = the duration (mono 500 ink1), the label (ink2), and the reason (ink3, ellipsis); the measure-bar slot holds a 12 px hatched bar in the state colour whose length ∝ the gap's duration against the longest gap in the list (minimum 2 %); the Wall column repeats the duration; other columns blank;
- **reasons** (R3-proto `gapWhy`): Waiting on you "the next turn is your message" (`reply`), "an agent asked you a question" (`ask`), "an agent waited for your approval" (`permission`); Usage limit (inferred) "the next turn resumes after a usage limit"; Waiting for an agent's message "the next turn is an agent's message"; Before the first message "the session was open before your first message" (or "before its first message" when the first turn is not `human`); Nothing running "no model call, tool or agent was running";
- **click** opens the gap's turn (SES-37) with the gap row as the cursor and the detail blade open;
- gap rows are not `j`/`k` stops in the session list (the cursor moves over turns only), and are hidden in the rail.
Waits inside a turn are not rows here; they are the turn's Waiting figure (SES-21) and rows in the turn ledger (TRN-34).
  Scenario: Given a11ce5e5, then "31m Before the first message" (09:33) sits above turn 1 (10:05), and the rows "16m Nothing running", "1h02m Usage limit (inferred)" and "2m36s Waiting on you" sit between later turns in time order (R3 §4, §7).
  Scenario: Given a 90 s stretch before turn 5, then no row appears and the 90 s counts in the session's `you` total.

SES-37 — The turn a gap belongs to (R3 §7 "Gaps attach to the real turn that contains them"): a gap lies in the turn ledger of the latest numbered turn that starts at or before the gap's start; a gap before the first numbered turn lies in the first numbered turn's ledger, as its first row. Gaps never attach to harness turns.

SES-38 — Cursor in the session blade (R3-proto `move`). On open, the cursor is on the first numbered turn that has rows. `j`/↓ and `k`/↑ move it over the numbered turns the list keeps; it stops at the ends. Enter opens the cursor's turn. `t`/`T` do the same as `j`/`k` here. The cursor row scrolls to about 30 % from the top of the list when it leaves the visible area (SESS-R §2 "fixed cursor height").

### 5.1 Session Waterfall (the maintainer + Kai 2026-10-12; prototype shots `wf-session-*.png`)

SES-40 — Waterfall pattern (defined once here; `turn.md` TRN-40–42 uses it for rows of a turn). A Waterfall is a view of an existing row list in which each row's figure columns are replaced by a time track:
- same rows, same order (as it ran), same cursor, keys, finders, folds and click behaviour as the list it replaces; only the right part of each row changes;
- row: one line, 32 px; columns When, glyph, name (max 300 px; 200 px under an 860 px blade width), the track (flex), Time;
- each row's bar runs from its start to its end on the shared axis, at least 2 px wide; colours by row kind are set by the view that uses the pattern; hatched fills only for waits (SES-20); error never colours a bar (SYS-8);
- axis segment **Fold gaps | Real time** in the finder line, default Fold gaps. Fold gaps: every wait-state stretch (`you`, `agentmsg`, `limit`, `none`) at least the view's fold threshold is drawn as a fixed break marked `≈` (l3 fill, ink3); the break width is set by the view (24 px unless the view says otherwise). Real time: linear. The column head names the axis ("Waits of N or more folded" / "Real time");
- a 12 px mono ink3 clock label row above the tracks, ticks as SES-29.

SES-41 — Session view toggle. The session finder line (SES-31) gets the segment **Ledger | Waterfall** (key `v` when the session blade is focused) before the search field, and in Waterfall mode the axis segment of SES-40. The mode is in the URL (`sview`, `sreal`; SES-8).

SES-42 — Session Waterfall rows: the same rows as the Ledger (turn rows, gap rows, harness folds), on the session's time axis (first to last record); fold threshold **2 min**, each folded wait a **12 px** break (Kai ruling 2026-10-12, spike-steps-r3 `SAX`). The `≈` marks sit only in the axis header, not in the rows; marks less than about 10 px apart merge into one mark "≈×N" with the tooltip "N waits of <total> folded" (one wait: "Wait of <duration> folded"):
- turn row: a bar over the turn's span (SES-21), ink2, struct-soft outline on the open turn; when an agent the turn caused runs past the turn's end, a **thin agent tail** (2 px tall, `--st-agents`, vertically centred) runs from the turn's end to that agent run's end, one tail per agent, the longest drawn last; tooltip "<agent>: ran to HH:MM";
- gap row: hatched bar in its state colour over the gap;
- harness fold: no bar;
- Time column = the turn's Wall or the gap's duration.
`j`/`k`, Enter, the finders, legend jumps and clicks behave exactly as in the Ledger (SES-38, §6).
  Scenario: Given a11ce5e5 in Waterfall, then the 1h02m usage-limit gap shows as a 12 px break with a `≈` mark in the axis header, its row keeps its hatched bar, and Real time shows it at full length.

## 6. Finders and their scope

SES-50 — "Go to a row" is the one navigation every finder uses (R3-proto `go`):
1. if the row is inside an agent whose rows are hidden, show that agent's rows inline (TRN-35);
2. if a zoom on the turn histogram hides the row, reset the zoom;
3. if the ledger's Order is Cost or Duration and the row is not in that list, set Order to "As it ran";
4. if a quiet fold hides the row, the fold opens (TRN-37);
5. open the row's turn in the turn blade (swapping the turn in place when another turn is open; TRN-50) and make the row the cursor, scrolled to about 30 % from the top;
6. the detail blade keeps its open or closed state, except where a requirement says it opens.

SES-51 — Scope (R3 top 5 #4; R3-proto `nextOf`). Every finder searches the **whole session** in session order (turns by time, rows in As-it-ran order inside each turn, an agent's rows after its agent row), skipping harness turns. From the session blade (no turn open) it starts after the start of the cursor's turn; from the turn blade it starts after the cursor row. It wraps from the last hit to the first (`W`, `S`: from the first to the last). With a search active, finders visit only rows that match it, and the chips count only those rows. Each jump shows a toast (SES-71): "<What> N of M: **<row name, 60 chars>** · turn K" (for a wait: "· **<duration>**" appended). With no hit at all: toast "No <what> in this session." and nothing moves.

SES-52 — `b` / `B`, Biggest (R3 §1.1 U1, U8; R3-proto `nextBig`):
- **session blade (no turn open)**: ranks the numbered turns that the list keeps by the measure (cost by SES-12, wall by SES-21, tokens). The first `b` selects the biggest turn (cursor only; Enter opens it); each further `b` selects the next in the ranking, `B` the previous, clamped at the ends. Toast: "Biggest turn by cost: **$24.69** · turn 23", then "2nd biggest turn by cost: …";
- **turn blade**: ranks every main-thread row (depth 0) of every numbered turn of the session with a value > 0 for the measure: model calls by their cost, time or tokens; tool rows by time; agent rows as one row with their whole cost, run time or tokens (so nothing counts twice; SESS-R §3); gap rows by time. `b` goes to the next row in the ranking (SES-50), crossing turns. Toast: "Biggest row by cost of 412: **$1.77** · turn 2 · <row name, 50 chars>";
- changing the measure resets the ranking position; the next `b` starts from the biggest. Tool rows with `user_wait` set or `max_silence_ms ≥ 300,000` are left out of a ranking by time (DM-40; Open Q8).
  Scenario: Given the 78-turn session in the session blade with measure Time, when the user presses `b`, then the cursor moves to the longest turn by wall and the toast names its wall.

SES-53 — `w` / `W`, waits: the next / previous wait stretch of any state that lasts at least 2 min, or main-thread tool row that waited on the user for at least 2 min (SES-36, SES-19, TRN-34; QRY-38; D54; the hit set the chip counts, D38 M4). The detail blade opens when it lands (R3-proto `d.gap`).

SES-54 — `e`, failures: the next failed tool row (TRN-32), at any depth; the hit set is the one the chip counts (D38 M4). There is no previous-failure key (R3-proto). If the failed-tool finder request fails, its chip shows the error state of SES-31 (D41 M8).

SES-55 — `s` / `S`, skill loads: the next / previous skill-load row. A skill load is a step with `skill_load` set, or the first step of a slash turn that has a `skill_invocation` event (DM-30; D34 "Skill load = verb='skill' plus target"). A slash turn whose event has no step is a hit on the turn itself: the turn opens at its first row.

SES-56 — `a`, agents: the next agent row (spawn or message; TRN-33), at any depth; the hit set is the one the chip counts (D38 M4).

SES-57 — Cross-turn landing. When the hit is in another turn, the turn blade swaps to that turn in place (no new blade, URL replaced; SES-10), the session rail's open row follows, and the zoom and unfolded quiet runs reset (TRN-24, TRN-37). From the session blade with no turn open, a finder other than `b` opens the turn blade (new blade, history entry).

## 7. Status bar, toasts, overlay

SES-70 — Status bar. While a session blade is open, a 36 px line runs along the bottom of the stage (hairline above, 24 px padding, ink2, one line, overflow hidden; R3-proto `status()`):
- session blade only: "Turn N selected";
- turn open: "Turn N · row R of M · main thread" (or "· inside <agent name>"), where R/M is the position in the turn's current row list (TRN-12); then the skill counter (TRN-52); then, when a search is active, `search` "“x” · Esc clears".
Key hints are not shown here (SYS-50).

SES-71 — Toasts (finders, legend jumps, turn hand-off): the EXP-73 look (bottom centre of the stage, 48 px above the bottom edge, l4, `--shadow-pop`, 14 px ink1, bold figures 500), one line, max 760 px, ellipsis; no Undo button. A new toast replaces the current one. It closes after 2.4 s (R3-proto `toast`), or on Esc. Toasts are announced to screen readers (`role="status"`).

SES-72 — `?` overlay: a centred l3 card (`--shadow-pop`, `--radius-card`) listing every key of this file and `turn.md` with its effect, as two columns (key caps, then text). `?` or Esc or its `x` closes it (R3 §1.2 "Top-bar key hints → `?` overlay").

SES-73 — Keys in the session and turn blades (adds to SYS-50; R3-proto `KEYS`). None act while a text field has focus, except Esc, Enter and ↓ in the search field (SES-32):

| Key | Effect |
|---|---|
| `j` / ↓, `k` / ↑ | next / previous turn (session blade) or row (turn blade); past a turn's last row, the next turn swaps in (TRN-50) |
| `J` / `K` | 10 rows down / up; stops at the turn's first or last row (TRN-51) |
| `t` / `T` | next / previous numbered turn |
| Enter | open the turn (session blade) or the detail of the cursor row (turn blade) |
| ← / → | focus the blade on the left / right (SYS-50) |
| Esc | SES-6 |
| `l` / `h` | show the cursor agent's rows inline / fold them (TRN-35) |
| `b` / `B` | SES-52 |
| `w` / `W` | SES-53 |
| `e` | SES-54 |
| `s` / `S` | SES-55 |
| `a` | SES-56 |
| `1` `2` `3` | measure Cost / Time / Tokens |
| `v` | Ledger / Waterfall of the focused blade (session or turn) |
| `r` | Review of the session (review.md REV-3) [D56] |
| `0` | reset the turn zoom and the session range |
| `/` | focus the session's search field; from the turn or detail blade, close them first (R3-proto) |
| `?` | the keys overlay |

## 8. States

SES-80 — Opening. The meta line, title and cost figure render at once from the explorer row that opened the session (when it came from the list). The time answer, legend, finder chips and 8 turn rows show skeletons (SYS-54) until the session's data arrives. Opening from a URL with no explorer row: the whole header shows skeletons.

SES-81 — Not found: `s` names no session → the session blade shows `search-x` 24 px ink3, "Couldn't find session <s>" (14 ink2) and a struct button "Back to sessions" that closes the blade.

SES-82 — Error: a failed request shows the error card (SYS-56) in the region it feeds (header answers, turn list, histogram, Details); the other regions keep working. Retry repeats that request.

SES-83 — Session with no numbered turns (for example a session with only harness turns, or a subagents-only session, DM-25): the header renders; the list area shows `info` 24 px ink3 and "This session has no turns with a message." (14 ink2); harness folds still show. See Open Q9 for subagents-only sessions.

SES-84 — Session with no priced model call: cost `—` and "no model calls" (S2 §5); the cost split bar is not drawn; the time answer still renders.

SES-85 — Missing times: a turn with a null start (S2 §5: 198 harness-like turns) sorts by `seq` and has a blank When; a numbered turn with a null start or end has blank Wall and Waiting and is left out of time rankings.

SES-86 — Reduced motion (SYS-59): no blade slide, no toast fade, no histogram bar animation.

## 9. Data the session blade needs (summary for S2/S3)

| Need | Used by | Status |
|---|---|---|
| Session: `display_title`, `title_source`, project name, dot slot, first/last ms, `incl_cost_usd`, `cost_lower_bound`, `wall_ms`, `active_ms`, `incl_tok_total` | header, rail | S2 DM-10, DM-60; exists |
| Session: `model_ms`, `tool_ms`, `agents_ms`, `none_ms` | time answer | S2 DM-51 |
| Idle stretches with their stored state and substate [D54] | gap rows, legend, `w` | S2 DM-52 |
| Per stretch: the next turn's origin and label (for `agentmsg`, `limit`) | SES-15, SES-17 | needs S3 (join) |
| Agents-only stretches ≥ 2 min (state `agents` in `stretches`), with the engagements running | SES-19, `w`, legend | D37 |
| Per main turn: `turn_kind`, `turn_origin`, `display_ix`, label, start/end, `attr_cost_usd`, `attr_tok_total`, `attr_cost_lower_bound` | rows, `b` | S2 DM-20–24, DM-45 |
| Per main turn: main-thread cost part | turn split | needs S3 (Σ own main-thread calls) |
| Per main turn: state times over the span `[start_ms, end_ms]` | Wall, Waiting, split | needs S2/S3 |
| Per main turn: main-thread models with cost; agent-row count; skill-load count; failed-tool count | flags, chips | needs S3 |
| Per main turn: end time of each agent run it caused (for Waterfall tails) | SES-42 | needs S3 |
| Unattributed agent cost per session | SES-12, Details | needs S3 (DM-45 invariant) |
| Per-session model lines (calls, cost, model time, speed, basis, ttft) | Details | S2 DM-47, computed by S3 |
| Histogram bins of cost and tokens over the session; turn starts; skill-load times | Over time | needs S3 |
| Session search: matching row ids per turn | SES-32 | needs S3 |
| Finder pools: ordered ids of gap, failed-tool, skill-load and agent rows; the cross-session ranking for `b` | SES-51–56 | needs S3 |
| Row-id resolution (request id, tool_use id, engagement id → op_id, turn) | SES-9 | needs S3 |

## 10. Resolved questions

| # | Question | Ruling | Applied in |
|---|---|---|---|
| R1 | Which time-gap option ships for the session list? | A, gap rows (Kai, steps-r3 outcome; R3 top 5 #5) | SES-36 |
| R2 | Which cost rule do turn and session figures use? | The causal rule; the clock rule only in a brushed range (R3 §7, D34, DM-45) | SES-12, SES-13 |
| R3 | When may a gap be labelled "Waiting on you"? | Only when the next turn's stored kind is `human` (S2 Q6) | SES-15 |
| R4 | Model colours for charts stacked by model (system.md Q3) | The ramp of SES-22, chosen and measured here | SES-22 |
| Q1 | S2 stores only `none` stretches. The turn ledger's "Only agents running" gap rows, `w` over them, and the "agents" legend jump need agents-only stretches. | Store every stretch of 60 s or more with a `state` column (`none` or `agents`) in the table `stretches` (renamed from `idle_stretches`). Gap rows, `w` and the "agents" legend jump use them. | D37 |
| Q2 | DM-51's per-turn state times use the window `[start, next turn start)`, which puts the wait before the next message inside the turn's Wall. R3 uses the turn span, so a turn's Wall and the `2`+`b` "slowest turn" ranking are not inflated by the user's think time, and that wait is a gap row instead. | Use the turn span for Wall, Waiting and the turn split (SES-21); keep DM-51's window figures for session sums. S2/S3 add span-based per-turn figures. | D37 |
| Q3 | Search matches only stored text (labels, summaries, snippets ≤ 160, commands, paths). Full thinking, tool output and asks live only in source files. | Stored text only in v1; the placeholder already names command, file, tool, text. Full-text search is a later item. | D37 |
| Q4 | A usage limit that ends a session segment is a resume gap outside the wall (DM-52), so it gets no row. | Accept for v1; mention in the gap detail's "Not in data" line only if the maintainer wants it. Also consider a stored `limit_hit` event inside a stretch as a second `limit` signal (DM-80 names the event). | D37 |
| Q5 | `crosshair` (Biggest chip), `keyboard` (keys button) are not in SYS-28's glyph map. | Add both to SYS-28 with a sign-off line: Biggest finder = `crosshair`; keyboard overlay = `keyboard`. | D37 |
| Q6 | The histograms are mouse-only (R3 §8). | Add keyboard range selection later, with the explorer chart's pattern (EXP-38a). | D37 |
| Q7 | D33 says "model shown on turns"; R3 removed model marks from turn rows. | D33 wins: compact marks in the flags slot (SES-34). R3's concern was clutter, so at most 2 shapes. | D37 |
| Q8 | DM-40: long derived tool durations (`max_silence_ms ≥ 5 min`) and tools with `user_wait` set [D54] would top any ranking by time. | Keep them as rows with `≈` and their flag (TRN-32). Leave them out of `b` by time, and sort them after all other rows in the Duration order (TRN-14). | D37 |
| Q9 | A subagents-only session has no main turns, so its agent work has no turn and the list is empty. | Show the empty state (SES-83) and the cost in "Agent work with no turn"; a list of its agents is a later item. | D37 |

## 11. Open questions (with the answer this spec applies)

None. Every question that was open here is resolved in section 10 by D37.

## 12. Not covered here
- The turn blade: `turn.md`. The detail blade: `detail.md`.
- The API, field names and the source-text endpoint: `data-model.md` (S2), `interfaces.md` (S3).
- Narrow widths below 1280 px, 200 % zoom reflow and screen-reader labels for the canvases are not specified (R3 §8); the build must at least give each histogram an `aria-label` stating its range and totals.
