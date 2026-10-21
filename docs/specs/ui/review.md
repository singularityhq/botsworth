# Review: where the time went, by cause (D56)

The Review view answers the operator's question "Where did this session lose time, and what should I change?"
from stored facts. Its data is QRY-42 (`Review`) over API-33. Direction and pick:
`tasks/2026-10-21-elevate/ELEVATE.md`; contract: `tasks/2026-10-21-elevate/SPEC.md`.

## 1. Causes

REV-1 — Cause labels, used in the UI everywhere a cause appears (bars, cards, the header claim, the project review):

| Cause | Label | Short | Fill |
|---|---|---|---|
| `stalled` | Stalled | stalled | `--color-error`, hatched (SES-20 hatch) |
| `tests` | Tests | tests | `--st-tool`, solid |
| `polling` | Polling | polling | `--st-tool`, hatched |
| `you` | Waiting on you | waiting on you | `--st-you`, hatched |
| `message` | Waiting for an agent's message | waiting on agent | `--st-agentmsg`, hatched |
| `limit` | Usage limit (inferred) | usage limit | `--st-limit`, hatched |
| `working` | Working | working | `--st-model`, solid |
| `idle` | Nothing running | nothing running | `--st-none`, hatched |

Hatching keeps its SES-20 meaning (nothing ran) except for `polling`, where a command ran that only waited; its
legend tooltip says so ("a sleep or polling command ran"). Labels and shorts equal SES-15's for the same state, so a cause has one name across the header, the ledger and
Review. Colour is never the only cue: every bar segment of 6 % or more has its short label in a caption line above the
bar, starting at the segment's left edge and cut to its width (the label never covers the fill), and the rest are
named in the legend under the bar.

## 2. Session

REV-2 — Header claim. In SES-26 line 1, after "**B** nothing running", when `lost_ms` ≥ 60 s: " · " and a button
"**<lost>** lost" (figure mono 500 in `--color-error`, word ink2; tooltip "Open Review (r)"). It opens Review. When
`lost_ms` < 60 s nothing is added. The figure equals Review's `lost_ms`.

REV-3 — The view. The session blade's view segment is "Ledger | Waterfall | Review". `r` opens Review, or closes it
back to the last of Ledger or Waterfall; `v` keeps toggling Ledger and Waterfall (from Review it opens the last
of them). URL `sview=review` (SES-8). Review replaces the turn list area under the finder line; the header and the
finder line stay. Esc (SES-6, before step 8): closes Review back to the list. In Review there is no turn cursor: `j`, `k` and Enter do
nothing. A finder key or a search jump sets the view to Ledger and jumps there, as an evidence link does.

REV-4 — Layout, top to bottom, padding 16 px 24 px, max text width 760 px:
1. **Claim** (20/600 ink1), two sentences. First: "<wall> wall. <lost> was lost to stalls." when `lost_ms` ≥ 60 s,
   else "<wall> wall. Nothing was lost to a stall." Second: "The largest cause was <label, lower case> (<ms>)." for the
   cause with the most `ms` other than `working`; omitted when every other cause is under 2 min.
2. **Bars** (REV-5).
3. **Cards** (REV-6), one per finding, in `findings` order. No findings: `check` 24 px ink3 and "No cause other than
   work took 2 min or more." (14 ink2).
4. **Footnote** (12 ink3): "Measured from the log. Only stalls count as lost: a finished task waited for an agent
   that was not running. Tests and polling count only while nothing else on that thread ran."

REV-5 — Bars. Two 16 px bars on one scale (the wall = full width): "As it ran <wall>" (all eight causes in the REV-1
order, zero parts omitted) and, when `lost_ms` ≥ 60 s, "Without the stalls <wall − lost>" (the same causes without
`stalled`, so it is shorter by exactly the lost time). Each caption is 14 px: label ink2, figure mono ink1 500. 2 px
gaps between segments; a segment under 2 px is drawn at 2 px. Segment tooltip "<label> <duration> (<share> %)".
Under the bars, a legend of the causes present with their durations, largest first. No clock axis: the bars show
amounts, not when (pick review).

REV-6 — Cards. A card is a 2-column grid (120 px figure column, text), l2 surface, `--radius-card`, 16 px padding,
12 px between cards:
- figure: the finding's `ms` (mono 20/500; `--color-error` when `lost`, else ink1); under it "lost" (12, error) or
  nothing;
- heading (14/600 ink1), a claim:
  - `stalled`: "Stalled <ms>: finished tasks waited for <threads> while nothing ran" (threads joined with ", ", at most
    3, then "+N"; when `threads` is empty, "for an agent");
  - `tests`: "Tests held the session for <ms>";
  - `polling`: "Polling loops held the session for <ms>";
  - `you`: "Waited <ms> on you";
  - `message`: "Waited <ms> for an agent's message";
- facts (14 ink2):
  - `stalled`: "<n> stalls of 2 min or more.";
  - `tests`: "<test_runs.n> test runs: <n_failed> with failures, <n_passed> passed, <n_no_result> with no result.
    <n_background> ran in the background." (zero clauses omitted);
  - `polling`: "<n> sleep or polling commands.";
  - `you`: the `you` substates of the session's `time_split` over 0 s, in SES-15 order and wording, each with its
    duration, the first letter upper case ("Asked you a question 12m · the next turn is your message 1h06m");
  - `message`: "<n> waits of 2 min or more. Botsworth does not store message text, so it cannot say why.";
- evidence (14, struct link): "Longest: HH:MM–HH:MM, <dur> · turn K" (K the turn number, as in SES-51 toasts) (no "· turn" when `turn` is null; no evidence line when `longest` is null, QRY-42).
  Clicking it opens the row like a finder jump (SES-51: the turn opens, the row is the cursor, its detail opens),
  and the session view is set to Ledger, so Esc from the turn returns to Ledger, not Review;
- change (14 ink1, after a 2 px struct-soft rule and the word "Change" in 12 ink3), fixed text:
  - `stalled`: "Wake an agent when its background task finishes, or check sessions where nothing has run for 15 min.";
  - `tests`: "Run the tests a change touches; run the whole suite once at the end.";
  - `polling`: "Use background notifications instead of sleep and polling loops.";
  - `message`: "Not a tooling fix: look at what the other session was doing.";
  - `you`: no change line.

REV-7 — States. Loading: the claim, bars and three cards show skeletons (SYS-54). Error: the SYS-56 card with retry in
the Review area; the header claim is omitted until the request succeeds. A Codex or omp session has no stalled part
and no stalled card (no zero card). When `n_unmeasured` > 0 the footnote adds: "Stalls are not measured for Codex and omp
sessions: their logs do not record when a background task finishes."

REV-8 — Phone: none in this slice. The blade strip gives the newest blade at least 640 px (SES-2, binding), so
no session view has a layout under 640 px; a phone layout is a change to SES-2 first.

REV-9 — Truth. Every figure on Review is a field of the API-33 response or of the session's `time_split`; the UI
adds no figure of its own except `wall − lost` and shares.
  Scenario: Given session 536dca47 on the real store, when the user presses `r`, then the claim reads "22h03m wall.
  1h36m was lost to stalls." (figures as ingested 2026-10-21; the stall row itself is 1h35m, the rest are stalls under
  2 min), the second bar is 1h35m shorter, and the first card is the
  stalled card whose evidence link opens the 10:20 gap row.

## 3. Project (slice 3)

REV-20 — Scope review. Reserved for elevate slice 3 (J3): the same claim, bars and cards over `scope_review` for the
explorer's current scope, with each card listing its top sessions (`sessions`) as links that open the session in
Review. Specified when slice 3 starts.
