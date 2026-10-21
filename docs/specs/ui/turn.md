# Turn blade: header, Ledger and Waterfall, histogram and zoom, the step-through (S1b)

Status: draft for implementation, 2026-10-12. Owner: spec writer S1b.
The turn blade is blade 2. It shows one numbered turn: where its money and time went, and every model call, tool call, agent run and wait in it, as a Ledger or a Waterfall over one shared row list (D33). It is the place where the user steps through the work with `j`/`k`, and the cursor can cross into the next turn (D33 "j/k continues across turns").

Shared rules are in `session.md`: blade widths, focus, Esc order, breadcrumb and URL (SES-1 to SES-11), the cost rule (SES-12, SES-13), the wall states, gap kinds, thresholds and colours (SES-15 to SES-21), the chart model ramp (SES-22), the finders (SES-50 to SES-57), the status bar, toasts and keys (SES-70 to SES-73). System rules are `SYS-<n>` in `system.md`. Requirement IDs here are `TRN-<n>`.

Sources, in priority order: decisions D31, D33, D34; `CONSOLIDATION-NOTES.md`; `docs/specs/data-model.md` (**DM-<n>**); `REVIEW-v4-steps-r3.md` (**R3**, §2 is the turn blade) and `spike-steps-r3/app.js` (**R3-proto**, `turnBlade`, `itemRow`, `groups`, `move`); `REVIEW-v4-turn-r2.md` (**TURN-R**: Ledger, Waterfall, histogram rule, brush); `REVIEW-v4-session-r2.md` (**SESS-R**: cursor, skill landmarks, P1 mapping). All under `prototype notes (not in this repo)/` except the decisions and S2.

## 1. Layout

TRN-1 — Top to bottom (R3 §2.2; R3-proto `turnBlade`): meta line; title; the two answers; when open, the Details panel; the histogram when shown (TRN-20); the finder line (TRN-10); the column header; the row list (scrolls). Padding 16 px 24 px 12 px for the header. Width per SES-2: the rest of the stage (min 640 px) while it is the newest blade; the standard 400 px while the detail blade is open, and then it is never pushed off-screen. At 400 px it shows the compact form: meta line, title, cost and wall figures without split bars or legend, no histogram or finder line, and two-line rows (line 1: glyph, name, primary figure; line 2: When, Time, flags), as SES-4 does for turn rows. The cursor, `j`/`k` and the detail-follows behaviour are unchanged; opening the detail and closing it restore the previous layout (SES-3).

TRN-2 — Meta line (14 px ink3, 8 px gaps; R3 §2.2 "Kicker → meta line"): the turn's kind glyph (SES-34 table; the who-glyph may take its `--type-*` tint, SYS-26); "Turn N of M" (ink2; M = the session's numbered turns); the kind label; the span `HH:MM–HH:MM` (mono; `+Nd` per SYS-29); when zoomed, the zoom chip (TRN-24); spacer; the "Over time" disclosure only when the histogram rule does not show the chart (TRN-20); the "Details" disclosure; then three 28×28 icon buttons: `chevron-left` "Previous turn (T)" (disabled on the first turn), `chevron-right` "Next turn (t)" (disabled on the last), `x` "Close (Esc)".

TRN-3 — Title: the turn text (SES-34 item 3), 20/600 ink1, one line with ellipsis, the full label in `title`. Clicking the title opens the message detail (DET-50). (R3 §2.2 "Title (ask): keep at 1 line".)

TRN-4 — The two answers, as SES-26 with the turn's figures (the turn header names the same exception to SYS-42 as SES-26, and TRN-5's stat line the same as SES-28; D38 L5) (R3 top 5 #1, §2.2):
- **Cost**: the turn's cost (SES-12) as the display figure; the split bar main thread / agents; caption "main thread **$X**" and, when agents ≥ $0.01, "agents it started **$Y**" with the tooltip "Whole cost of the agent runs this turn started or messaged, including work after it ended". In a zoom the figures follow the clock rule and the caption reads "agent calls in range" (SES-13).
- **Time**: the turn's Wall (SES-21) + "wall" + "**A** running · **B** not running" (SES-26); the split bar over the span (or the zoom range) with the states that occur there; the legend (top 4 states with ≥ 30 s). A legend jump searches **this turn** only (R3-proto `jumpState(k, "t")`) and otherwise behaves as SES-27.
Grid `minmax(180px, 1fr)` and `minmax(0, 2fr)`, 32 px gap.
  Scenario: Given the 78-turn session, turn 35, then the cost answer reads $25.99 with "main thread $0.88" and "agents it started $25.11", and the agent rows of the ledger sum to $25.11 (R3 §7).

TRN-5 — Details (closed by default; R3 §2.2): the stat line (`coins` tokens, `arrow-left-right` model calls), the model table and speed note as SES-28 for this turn, then "Full message" (`message-square-text`, 14/600) with its character count (ink3) and a struct link "Open" that opens the message detail (DET-50) (R3 §2.2 "Full message → Details"; TRN-3).

## 2. Finder line, order and search

TRN-10 — Finder line (R3-proto `turnBlade` `.finder`), 8 px gaps: the view segment "Ledger | Waterfall" (`v`); then in Ledger the label "Order" (ink3) and the segment "As it ran | Cost | Duration", or in Waterfall the segment "Fold gaps | Real time"; spacer; the finder chips of SES-31 with **this turn's** counts (each hidden at 0). The turn blade has no Biggest chip and no search field; `b` and `/` work by key (SES-52, SES-73).

TRN-11 — Column header (32 px, ink3, hairline below). Ledger: When · (glyph) · name ("As it ran", "Top by cost" or "Top by duration") · (flags) · "by <measure>" · Cost · Time · Tokens. Waterfall: When · (glyph) · "N rows" · axis label ("Waits of 60 s or more folded" or "Real time") · Time. Ledger grid `76px 20px minmax(0,1fr) 96px 104px 72px 72px 56px`, 12 px gap, padding 0 20 px; under a blade width of 860 px, `72px 20px minmax(0,1fr) 56px 64px 64px 64px` (R3-proto `app.css` container query).

TRN-12 — The row list and search. The visible rows are the turn's rows (TRN-30) that: are not inside a folded agent (TRN-35); start inside the zoom range when zoomed (gap rows: overlap it); and match the search when one is active (SES-32). The cursor's position "row R of M" (status bar, detail stepper) counts this list in its current order. A search active in the session carries into the turn; with a search active, quiet folds are off (TRN-37). No match: "No rows match “x”." (14 ink2, centred).

TRN-13 — Order "As it ran" (default): rows by start time; an agent's rows follow its agent row when shown (TRN-35); gap rows in time position (TRN-34).

TRN-14 — Orders "Cost" and "Duration" (TURN-R §3; R3-proto `navList`): only main-thread rows (depth 0) of the visible list, sorted descending by cost (model calls and agents; tools and gaps have none and sort last) or by time (calls, tools, agents, gaps; rows named in SES Open Q8 sort last). The first 15 show, then a fold row "Show N more" with the hidden rows' cost sum in money (SYS-38); clicking it shows all. No quiet folds, no agent bands. Changing the order moves the cursor to the first row of the new list when the current row is not in it, and resets "show all".

## 3. Rows

TRN-30 — One row per model call, tool call, agent run and gap (R3 top 5 #3; SESS-R §7 P1 mapping; DM-46). Thinking and text are not rows; they show in the model call's detail. A row is one line, 36 px min (SYS-22 dense), hover l3, cursor = struct-soft fill + name in struct + the SYS-35 outline (no left bar; SYS-35). When = the start clock `HH:MM:SS` (mono ink3; seconds are allowed in the transcript, SYS-29). Rows inside an agent are indented 16 px per level (max 3 levels) inside the agent band (TRN-35).

TRN-31 — Model-call row (R3-proto `label`, `itemRow`; TURN-R §3):
- glyph `arrow-left-right`;
- name: the first line of its said text or final answer; else the first line of its thinking (ink2); else "Tool request: <tool names joined by ', '>" (ink2); else "Thinking only" (ink2). A final answer is prefixed by the tag "final" (12 px ink3, 1 px rule border, radius 4; 12 px is allowed for this tag by the D31 exception, D38 L3; SYS-15);
- flags: the compact model mark (SYS-41);
- measure bar (SYS-39), Cost (money; `$x+` / blank per DET-62 for an unpriced call), Time = model time, Tokens (`—` when null; S2 §5);
- the row's figure for the active measure is 600 ink1 (money stays money) when it is ≥ 25 % of the list's largest (R3-proto `.big`).
  Data: per call `summary`/`snippet` of its say, answer and think steps (exists, ≤ 160).

TRN-32 — Tool row (R3-proto; DET for status rules):
- glyph by tool (SYS-28: shell `terminal`, read/edit/write `file-text`/`file-pen`/`file-plus`, search `search-code`, web `globe`, skill `book-open`, other `puzzle`);
- name: the step summary (the tool's description), else the tool name; then the target (command, file path, pattern or URL) in mono ink3 when it differs from the name. A leading `cd <path> && ` is removed from a shown command (TURN-R §3);
- **skill load** (SES-55): glyph `book-open` in ink1, name = the skill name at 600, sub-text "skill loaded" or "SKILL.md read" (ink3). Weight is its only landmark cue in the row (SESS-R §7);
- flags: when failed, `circle-alert` + "exit N", "error" or "interrupted" in `--color-error` (SYS-8). Failed = `is_error`, or an exit code other than 0, or `interrupted`, or a step outcome `exit:N` with N ≠ 0 (REVIEW-v4-detail §5.4). When `max_silence_ms ≥ 300,000`: `hourglass` + "no records <duration>" (ink3). When `user_wait` is `ask`: `hourglass` + "asked you" (ink3); when `permission`: `hourglass` + "waited for your approval" (ink3) (DM-40; D54);
- Cost blank (zero by construction, SYS-31), Tokens blank; Time = duration, prefixed by `≈` when `time_prov = derived` with the tooltip "End taken from the next record" (DM-40).

TRN-33 — Agent row (R3-proto `label` agent; TURN-R §3; DM-36). A spawn (`Agent`/`Task`) or `SendMessage` tool call that caused an engagement is shown as an agent row, not a tool row:
- glyph `bot` in `--color-type-team` (SYS-26 turn-blade exception);
- name: the agent name (else its type); several engagements from one call are joined by ", ". Sub-text (ink3): "started" or "messaged", then " · " and the call's description when it has one; then "N rows" (ink3) when its rows are folded, or "rows shown" when open;
- flags: the compact marks of the agent's models;
- Cost = the whole cost of the engagement (SES-12); Time = the engagement's run time (`wall_ms`; DM-36); Tokens = its tokens;
- in a zoom, Cost and Tokens cover only the agent's calls that start inside the range, and the sub-text adds "N of M calls" (TURN-R §4).

TRN-34 — Gap row in the ledger (R3 §2.1 U2; R3-proto `itemRow` gap). Every stretch of 2 min or more inside the turn's span, and every gap that SES-37 attaches to this turn (the gap before the first turn; the gap after this turn's last activity), is a row in time position:
- glyph and label by kind (SES-36 glyphs; "Only agents running" uses `bot`); name = the duration (mono 500 ink1), the label (ink2), the reason (ink3);
- reasons as SES-36, plus: Nothing running inside a turn "no model call, tool or agent was running"; Waiting on you from an agent's call "an agent asked you a question" (`ask`) or "an agent waited for your approval" (`permission`) (D54); Only agents running "N agents running: a, b, c +N" (names of up to 3 engagements running during it);
- the measure-bar slot holds the hatched (or, for agents-only, solid `--st-agents`) bar ∝ duration against the list's largest time; Time = duration; Cost and Tokens blank;
- row fill l2 (R3-proto `.opt-A .row.k-gap`); on the cursor, struct-soft.
  Data: SES-19 (agents-only stretches, the `stretches` rows with state `agents`; D37).

TRN-35 — An agent's rows (R3-proto `toggleAgent`; SESS-R top 5 #1). By default an agent's rows are folded. `l` on an agent row (or the detail's "Show its N rows" button, DET-33) shows them right below it, in As-it-ran order, and moves the cursor to its first row. They sit in a band: indent 20 px, a 1 px `--color-rule` left border, no tinted fill (SYS-2; D30 "no coloured left border" applies to rows). Nested agents nest bands. `h` on a row inside the band folds that agent and puts the cursor on its agent row; `h` on an open agent row folds it. Finders open bands as needed (SES-50). Bands reset when the blade closes; they persist across turn swaps for agents already open. Loading an agent's rows (API-32, paged) shows skeleton rows inside the band; if the request fails the band shows the SYS-56 error card with Retry. At most 500 rows show at once, then a fold row "Show N more" loads the next page (D38 M8).

TRN-36 — Clicking a row makes it the cursor and opens the detail blade (R3-proto `d.i`). Enter on the cursor row opens the detail blade.

TRN-37 — Quiet folds (R3 §2.2; R3-proto `quiet`, `groups`; SESS-R §7 P2). Only in Order "As it ran" (Ledger and Waterfall), only with no search active. A quiet row is a main-thread (depth 0) model-call or tool row that is not a skill load, not failed, has time < 60 s, and, for a model call, cost < 2 % of the turn's cost. A run of 4 or more consecutive quiet rows folds into one fold row, unless the run holds the cursor:
- fold row (36 px): When = the run's first start (`HH:MM`); `list-collapse`; "Show N quiet rows" (struct) + sub-text "C calls · T tools · HH:MM–HH:MM" (ink3); Cost = the run's cost sum (money); Time = last start − first start;
- click (or Enter on it) unfolds that run; it stays unfolded until the turn changes; a finder or `j`/`k` that lands inside a folded run unfolds it.
Agent rows and gap rows break a run.

## 4. Histogram and zoom

TRN-20 — Rule (D33; CONSOLIDATION-NOTES; TURN-R §4). The histogram shows by default when the turn has at least one agent row or its Wall is 30 min or more. Otherwise it is hidden and the meta line shows the "Over time" disclosure, which shows it. `thist` in the URL overrides the rule either way (SES-8). When the rule shows it, there is no toggle (R3-proto).

TRN-21 — Geometry: as SES-29 (104 px, 3 px bins, linear bars, 8 px state lane, skill pins, cursor line, 12 px mono axis labels), over the turn axis (or the zoom), with these differences (R3-proto `drawHist("ht")`; TURN-R §4):
- the turn axis runs from `start_ms` to the later of `end_ms` and the latest `agent_runs[].end_ms` (D41 N1). The part after `end_ms` is the tail zone: an l1 fill with the label "after the turn" in ink3. The turn's Wall and Waiting (SES-21) stay span-based;
- waits of 10 min or more fold to 24 px with `≈`, except inside a zoom (real time);
- the caption's right side reads "Drag across to zoom this turn".

TRN-22 — Bars are stacked by model family (CONSOLIDATION-NOTES "Charts stacked by model: allowed"; TURN-R §6), in the chart ramp of SES-22, for both measures (cost; tokens; cost when the measure is Time). The bars include the calls of every agent the turn caused that start inside the axis, tail zone included; the histogram covers the whole axis, so its total equals the turn's cost (SES-12; D41 N1). A legend under the caption lists each family present: P4 mark in its chart colour + short name + its total for the measure (money for cost, ink2 tokens). Hover on a bin shows a tooltip with the bin's clock range and its total per family.
This is the only chart where cost is not drawn in `--color-money` (TURN-R §6 "Exception"); every cost figure, bar and row still is.

TRN-23 — Overview strip: none in v1 (TURN-R §4 described one; R3 did not keep it). The zoom chip and `0` give the way back.

TRN-24 — Brush zoom (TURN-R §4; R3-proto `hookBrush("ht")`). Dragging across the histogram by 4 px or more sets a zoom range:
- the range may lie in the tail zone of TRN-21; the clock rule applies there too (D41 N1);
- rows: TRN-12 keeps rows that start inside the range, and gap rows that overlap it; the cursor moves to the first visible row when its row is outside;
- figures: the answers use the clock rule (SES-13): "agent calls in range"; agent rows show their in-range part (TRN-33);
- the Waterfall switches to real time while zoomed (TRN-42);
- the meta line shows the zoom chip: `clock` + `HH:MM–HH:MM` (struct on struct-soft) + `x` "Whole turn (0)";
- `0`, the chip's `x`, or Esc with the detail closed (SES-6 step 4) resets it. Changing turn resets it.
**Caveat**, stated in the cost answer's tooltip while zoomed: "In a range, cost counts model calls that started inside it. The turn's total counts the whole cost of the agents it started." (R3 §7).
  Scenario: Given main#2 (2h34m, 16 agents), when the user drags 09:00–09:35, then the chip reads "09:00–09:35", the cost answer shows the in-range sum with "agent calls in range", and Order Cost lists the agent rows by their in-range cost (TURN-R §4: $9.06 in that range).

## 5. Waterfall

TRN-40 — The turn Waterfall is the Waterfall pattern of SES-40 applied to the turn's row list (TURN-R top 5 #1): same rows and order (As it ran), folds, cursor and detail as the Ledger; fold threshold **60 s** (D38 M3: stretches are stored from 60 s, DM-52); the axis is the turn axis of TRN-21, tail zone included (D41 N1).

TRN-41 — Bars on the track (TURN-R §3, corrected for SYS-8 and SES-22): from the row's start to its end; at least 2 px wide. An agent bar that runs past `end_ms` draws in the tail zone of TRN-21 and is not clipped, because the axis reaches the latest agent end (D41 N1).
- model call: its family's chart colour (SES-22);
- tool: ink2. A failed tool keeps ink2; its name is prefixed by `circle-alert` in `--color-error` (SYS-8: error never colours a bar);
- agent: `--st-agents`;
- gap: hatched in its state colour (SES-20).

TRN-42 — Axis: SES-40's Fold gaps (default; waits of 60 s or more, D38 M3) or Real time. While zoomed the axis is the zoom range in real time and "Fold gaps" is disabled with the tooltip "Zoomed: real time" (TURN-R §3).

## 6. The step-through

TRN-50 — `j` / `k` hand-off across turns (R3 top 5 #4; D33; R3-proto `move`). `j` on the last row of the visible list (TRN-12) swaps the next numbered turn that has visible rows into this blade, with the cursor on its first row; `k` on the first row swaps in the previous turn with the cursor on its last row. In place: no new blade, no new history entry (SES-10); the zoom, unfolded quiet runs and "show all" reset; the view, order, measure and search stay; the session rail's open row follows (SES-4); the detail blade, if open, shows the new row. Toast: "Turn 1 → turn 2: <turn text, 70 chars>". On the session's last (first) row: toast "Last row of the session." ("First row of the session.") and nothing moves.

TRN-51 — `J` / `K` move 10 rows and stop at the turn's first or last row; only single steps cross turns (R3 top 5 #4).

TRN-52 — Skill counter (SESS-R top 5 #3; R3 top 5 #4; R3-proto `status`). In the status bar (SES-70), when a skill load at the cursor's own agent level (main thread, or the same agent) lies at or before the cursor in this turn or the previous numbered turn:
- on the load row: `book-open` "**<skill>** loaded here";
- after it: `book-open` "row **N** after **<skill>** loaded" + " in turn K" when the load is in the previous turn + " · +<duration>" (cursor row start − load start). N counts rows at the same level from the load to the cursor, excluding gap rows;
- otherwise nothing.
  Scenario: Given the skill run (c9d0e1f3) with skill-writer loaded in turn 1, when `j` has crossed into turn 2 and the cursor is on the row of R3 frame `u4-4`, then the status bar reads "row 62 after skill-writer loaded in turn 1 · +10m" (R3 frame `u4-4`; figures from that frame).

TRN-53 — `t` / `T` open the next / previous numbered turn at its first row (same in-place swap as TRN-50, no toast). The meta line's turn buttons do the same.

TRN-54 — Scroll: the cursor row scrolls to about 30 % from the top of the list when it would leave the visible area, and is centred after a finder jump or a turn swap (SESS-R §2).

## 7. States

TRN-60 — Opening: the meta line, title and cost figure render at once from the session's turn row; the time answer, legend, chips, histogram and 10 rows show skeletons (SYS-54) until the turn's rows arrive.

TRN-61 — Error: the error card (SYS-56) in the region that failed (answers, histogram, rows); Retry repeats that request.

TRN-62 — Empty: a numbered turn with no rows (no model call, no tool): "No model calls in this turn." (14 ink2) in the row area; answers show `—` cost and the wall. Zoom with no rows: "No rows in this range." Search with no match: TRN-12.

TRN-63 — Large turns: a turn of 1,000 or more rows (main#2 has 1,028 model calls with its agents) renders without blocking input; folded agent bands keep the initial list to the main thread (TURN-R reconciliation).

TRN-64 — Missing data in rows: a null start sorts by `seq` and leaves When blank; null model time, duration or tokens leave their cell blank (`—` for null tokens on a model call, S2 §5); an empty model name shows the "other" mark with the tooltip "Model not recorded" (DET-61).

## 8. Data the turn blade needs (summary for S2/S3)

| Need | Used by | Status |
|---|---|---|
| Rows of a turn: model calls with their steps (DM-46), tool calls, engagements caused (`caused_by`), with start, duration, `time_prov`, cost, tokens, model, summary/snippet, target, tool name, status fields (`exit_code`, `is_error`, `interrupted`, `outcome`), `skill_load`, `max_silence_ms`, `user_wait` | TRN-30–33 | S2 DM-30, DM-36, DM-40, DM-46; shape needs S3 |
| An engagement's rows (paged), on `l` | TRN-35 | needs S3 |
| Gap rows inside the turn span, incl. agents-only | TRN-34 | S2 DM-52, with state `agents` stored too (D37) |
| Per-turn state times over the span; in a zoom, over the range | TRN-4 | needs S2/S3 |
| Histogram bins per model family over the span, incl. caused agents' calls | TRN-22 | needs S3 |
| Clock-rule totals and per-agent in-range cost and call counts for a range | TRN-24, TRN-33 | needs S3 |
| Per-turn model lines, tokens, call count | TRN-5 | S2 DM-47, S3 |

## 9. Resolved questions

| # | Question | Ruling | Applied in |
|---|---|---|---|
| R1 | Show the histogram on every turn? | Only with agents or wall ≥ 30 min (D33; CONSOLIDATION-NOTES) | TRN-20 |
| R2 | Can the cursor cross turns? | Yes, with `j`/`k` only; `J`/`K` stop at the edge (D33; R3) | TRN-50, TRN-51 |
| R3 | Colour of failed tools in the Waterfall | Not error-red: SYS-8 forbids error on bars; the glyph carries it | TRN-41 |
| Q1 | R3 has no message row in the ledger; TURN-R had the ask as the first row. D33 lists "message" as a detail kind. | Open the message detail from the title and from Details (TRN-3, TRN-5); no ledger row, to keep R3's de-cluttered list. | D37 |
| Q2 | Thread lanes (one 8 px lane per agent under the histogram) would show which agent ran in each burst (TURN-R §4 "not built"). | Not in v1. | D37 |
| Q3 | The turn blade has no own search field; `/` returns to the session (R3-proto). | Keep for v1; the status bar shows the active search and Esc clears it. Revisit if users search mostly from inside a turn. | D37 |

## 10. Open questions (with the answer this spec applies)

None. Every question that was open here is resolved in section 9 by D37.
