# Explorer (blade 0): period, filters, day chart, projects, session list (S1a)

Status: draft for implementation, 2026-10-12. Owner: spec writer S1a.
The explorer is the first page and blade 0 of the blade stack (D29). It answers "what did this period cost, on which days, in which projects, and in which sessions", then opens a session (D28 acceptance cases: "Wed Oct 7, Claude Code 1.85B tokens / $550 / 18 sessions: why"; "yesterday's longest sessions: why").

Visual rules, formats, glyphs, patterns (P1–P4, fold row), blade mechanics and keys are in `system.md` (`SYS-<n>`). This file gives the explorer's layout and behaviour. Requirement IDs are `EXP-<n>`.

Sources: decisions D28–D34 (the decision log); `prototype notes (not in this repo)/` — `CONSOLIDATION-NOTES.md`, `REVIEW-v4-explorer.md` (**EXP-R**, its "Final explorer design" section wins over its per-element notes), `REVIEW-v4-overview.md` (**OVR-R**, §r3 wins over earlier sections), `REVIEW-v4-list-r2.md` (**LIST-R**, §r3 wins), `REVIEW-v4.md` (**SYS-R**), and the prototypes `spike-overview/index.html` (chart A + donut, option 1) and `spike-list-r2/index.html` (A, default settings), plus `core.js` (top bar, popovers, palette, blades). Decisions and CONSOLIDATION-NOTES override the reviews.

Backend data that does not exist yet is marked **needs S2/S3** with a proposed field name. This file does not design the API.

## 0. Terms and scope rules

| Term | Meaning |
|---|---|
| **Period** | An inclusive range of local calendar days `[from, to]`. A preset period (e.g. "Last 7 days") is stored by name and resolved against today's date (EXP-14). |
| **Local day** | A calendar day in the request zone (the browser zone, sent as `tz`; interfaces QRY-4) [D50], used for every day bucket and label. |
| **Filters** | The search text plus the provider, model, branch and project filters plus the automated rule. The period is not a filter. |
| **In-scope session** | A session that (a) has at least one owned llm_call (not `duplicate_of`) whose start falls on a day in the period, (b) passes every filter. |
| **Measure** | Cost or Tokens. It drives the day chart and the project panel only (EXP-31). |
| **Automated session** | A session in which no turn has a human ask (D32). |
| **Full session figures** | The session's inclusive totals: `incl_cost_usd`, `incl_tok_total`, `wall_ms`, `active_ms` — never a share of them. |

EXP-1 — Membership. A session is in the period when it has an owned llm_call that starts on a local day in the period, not when it starts in the period. A session that starts on Oct 5 and makes calls on Oct 6 is in "Oct 6 – Oct 12" (OVR-R §6 item 3; LIST-R §8 item 4).
  Scenario: Given session S with calls on Oct 5 and Oct 6 only, when the period is Oct 6 – Oct 12, then S is in the list, and its start time column shows `Mon 21:43` style with the weekday of Oct 5.
  Data: per-session list of active local days (`active_days`, needs S2/S3).

EXP-2 — Full figures in rows and totals. List rows, group headers, the project panel and the header totals use full session figures of in-scope sessions. The header total equals the sum of the visible rows (D32; OVR-R §r3 "Data problem (new)"; `deferred.md` "REVERSED by the maintainer").
  Scenario: Given a $450 session with $300 of calls on Oct 7 and $150 on Oct 8, when the period is Oct 7, then its row shows $450.00 and the header total includes $450.00.

EXP-3 — Day split in the chart only. A day bar is the sum, over sessions that pass the filters, of that session's cost (or tokens) from llm_calls that start on that local day. Bars do not use full session figures, so a day's bar need not equal the sum of the rows listed for that day (D32; `deferred.md` "Day totals must split sessions across days").
  Scenario: Same session as EXP-2: the Oct 7 bar includes $300.00 of it and the Oct 8 bar $150.00.
  Data: per-session per-local-day cost and tokens, by project (`day_split`, needs S2/S3).

EXP-4 — Automated sessions are hidden by default (D32). The rule is defined by DM-25 (D38 L2). Data: `is_automated` per session (needs S2/S3).

## 1. Layout

EXP-5 — At 1440×900 with no blade open, the explorer has 24 px padding and, from top to bottom (EXP-R final design; spike-list-r2 `.top` grid):
1. top bar (52 px, outside the blade; EXP-6);
2. period title (EXP-10);
3. totals line, 8 px below (EXP-12);
4. scope bar, 16 px below: search field, filter tokens, `+ Filter`, `Clear all` (EXP-17 to EXP-26);
5. 32 px below: a two-column region, the day chart (`minmax(0,1fr)`) and the project panel (460 px), 32 px apart (EXP-30 to EXP-46);
6. 32 px below: the session list (EXP-47 to EXP-66).
The page scrolls as one; the list's column header band is sticky (EXP-50).
  Verify: shot at 1440×900 shows items 1–5 and at least 6 list rows without scrolling (OVR-R §3 "Height": chart region about 260 px).

## 2. Top bar and breadcrumb

EXP-6 — The top bar spans the stage above all blades. Left: logo mark + "Profiler" 14/600 ink2, then the breadcrumb (SYS-49). Right: the ⌘K button (28×28, `command` glyph + key cap "⌘K", tooltip "Jump to a session, date or filter") and the ingest freshness text (EXP-8). There is no text input in the top bar (EXP-R "⌘K box").
  Verify: on the explorer, the count of visible text inputs is 1 (the search field).

EXP-7 — At blade 0 the breadcrumb is the single crumb "Sessions" 14/500 ink1, no box, no amber. It never repeats the period (EXP-R "Breadcrumb").

EXP-8 — Freshness: right of the ⌘K button, 14/400 ink3, "Ingested 12 min ago" from `last_ingest_ms` (D5: queries report the last-ingest time; ui-refine §3.6). With `last_ingest_ms: null` it reads "Never ingested". The prototype's "Prototype data" label is not in the product (EXP-R "PROTOTYPE DATA chip"). Tooltip: the absolute local time.
  Data: `/api/ingest/status` `last_ingest_ms`, `store_exists` (exists per D22).

EXP-9 — The logo/"Profiler" is not a control. Clicking the "Sessions" crumb when blades are open closes all blades (SYS-49).

## 3. Period title and totals

EXP-10 — Period title: one line, 20/600 ink1, followed by `chevron-down` 20 px ink2. It is the period control (EXP-R final design, "The title is the control"). Text by period kind:

| Period | Title |
|---|---|
| Today / Yesterday | `Today · Mon Oct 12` |
| Last 7 days, This week, Last week | `Last 7 days · Oct 6 – Oct 12` |
| All time | `All time · Sep 22 – Oct 12` (first to last day with data) |
| Custom range | `Oct 1 – Oct 8` |
| Custom single day | `Wed Oct 7` |

EXP-11 — Clicking the title, or pressing `p`, opens the period popover (EXP-13). The title is a button with `aria-haspopup` and its full text as the accessible name.

EXP-12 — Totals line (EXP-R final design):
`$2,570.00   8.69B tokens   212 sessions   12 automated hidden`
- cost: 20/600 money, tabular, 2 decimals (SYS-29);
- tokens and sessions: figure 20/600 ink1, unit word 14/400 ink3;
- 16 px between items, no "·";
- when the search or any filter narrows the set: `7 of 212 sessions`, where "of 212" is 14/400 ink3 and 212 is the count of sessions in the period that pass only the automated rule;
- `N automated hidden` (14/400 ink3) appears when automated sessions are hidden and N > 0, where N counts automated sessions in the period that pass the other filters;
- cost and tokens are sums of full figures of in-scope sessions (EXP-2). Sessions with unpriced calls follow SYS-32;
- while loading, figures are skeletons (SYS-31).
  Scenario: Given the period Oct 6 – Oct 12, automated hidden, and filter "model: opus 5.5", when 7 of 212 sessions match, then the line reads `$49.71   146M tokens   7 of 212 sessions   12 automated hidden` (example figures).

## 4. Period control

EXP-13 — The period popover (l3, SYS-44), anchored under the title, holds (EXP-R "Period popover"; core.js `PRESETS`):
1. presets, one row each, with the resolved date range right-aligned in mono ink3: Today, Yesterday, Last 7 days, This week (Mon–today), Last week (Mon–Sun), All time;
2. a custom range: two text fields "From" and "To" showing `Oct 6` and `Oct 12` (not native date inputs);
3. a footer, 14 px ink3, naming the request zone, the browser zone (D50, interfaces QRY-4): "Days are in <zone>. A session counts in every day it made a model call."
The active preset row shows `check` in struct and its label ink1 500, with no fill. Choosing a preset applies it and closes the popover.
  Scenario: Given the popover open, when the user clicks "Yesterday", then the popover closes, the title reads `Yesterday · Sun Oct 11`, and the chart band, totals, project panel and list update.

EXP-14 — Presets are relative: "Today" is the current local date, not the last day with data. A preset stays a preset in the URL (EXP-74), so a bookmarked "Last 7 days" resolves against the day it is opened.

EXP-15 — Custom fields accept `oct 6`, `6 oct`, `10-06` (month-day), `10/06`, `2026-10-06`, `today`, `yesterday` and a weekday name (the latest such day up to today). Enter in a field applies the range. If From is later than To after an edit, the other field takes the same day (core.js `dfrom`/`dto` rule). Text that does not parse is not applied and the field shows its previous value again on blur.
  Scenario: Given the To field focused, when the user types `oct 7` and presses Enter with From = Oct 6, then the period is Oct 6 – Oct 7.

EXP-16 — Keyboard: ↑/↓ move between preset rows and fields; Enter applies; Esc closes; focus returns to the title (EXP-R "Period popover").

## 5. Search

EXP-17 — The search field is always visible, first in the scope bar: 280 px wide, 360 px while focused (260 ms width transition, none under reduced motion), 32 px tall, `search` glyph, placeholder "Search sessions" with a `/` key cap at its end in ink3. When it holds text, an `x` button inside it clears it (EXP-R final design, "Search").

EXP-18 — `/` focuses the field from anywhere outside a text field (SYS-50). Esc in the field blurs it and keeps its text.

EXP-19 — Matching: the text is split on whitespace into terms; a session matches when every term is a case-insensitive substring of its searchable text, which is: display title, human-ask snippets, project name, git branch, session id, provider name (core.js `_search`). Search narrows the in-scope set, so it applies to totals, chart, project panel and list. Results update while typing.
  Scenario: Given the term `rbac opus`, a session titled "Fix rbac sync" in project bizdemo that used opus is listed only if "opus" appears in one of the searchable fields; the model is not a searchable field.
  Data: per-session searchable text, including ask snippets (`search_text`, needs S2/S3); server-side matching across the period (needs S3).

## 6. Filters and filter tokens

EXP-20 — `+ Filter` button (`sliders-horizontal` + "Filter", 32 px, edged) opens the filter popover; `f` opens it too. The popover is one column with sections Provider, Model, Branch, Automated; section heads 14/600 sentence case with their glyph; 16 px between sections; no rules between them (EXP-R "Filter popover"; SYS-R §5 #25). Group-by is not in this popover (EXP-50).

EXP-21 — Provider section: one chip per provider present in the period (Claude Code, Codex, omp), each with its mark, name and in-scope session count in ink3. Chips toggle; several may be on. A session matches when its provider is any selected one.

EXP-22 — Model section: a list of models used by sessions that are in scope under every filter except the model filter, each with its count of sessions using it (mono ink3, right-aligned), ordered by count, highest first. The section head reads "Model · sessions using". Models with zero such sessions are hidden behind "Show all (n)". Several models can be selected; clicking a value toggles it (Kai 2026-10-12: provider, model and branch share one multi-select pattern). A session matches when at least one of its owned llm_calls, in any agent, used any selected model.
  Scenario: Given the period Wed Oct 7, when the popover opens, then only models used on Oct 7 are listed, each with its session count.
  Data: per-session list of models (`models`, needs S2/S3); per-value counts under the other filters (needs S3).

EXP-23 — Branch section: a type-ahead field at the top, then branches of sessions in scope under every filter except the branch filter, with counts, ordered by count. Long names are middle-truncated (`conductor/rbac-cont…2026-09-22`), full name in `title`. Multi-select, as for model; a session matches when its branch is any selected one. Zero-count values behind "Show all (n)".
  Data: `git_branch` per session (exists in `sessions`).

EXP-24 — Automated section: a toggle "Hide automated sessions" with the count of automated sessions in the period (EXP-27).

EXP-25 — Filter tokens: the scope bar shows one token per selected filter value, after the search field, in this order: providers, models, branches, projects, automated. Look: 32 px, `--radius-chip`, l3 fill, 14 px ink1, leading identifier, trailing `x` (16 px ink3, ink1 on hover, `aria-label` "Remove <value> filter"):
- provider: provider mark + name; model: `cpu` + short model name; branch: `git-branch` + middle-truncated name; project: 8 px project dot + name; automated: `repeat` + `automated hidden · 12`.
Clicking `x` removes that filter. There is no filter count badge (EXP-R final design, "Filters count badge").
  Scenario: Given project filters bizdemo and proj-b, when the user clicks `x` on proj-b, then only the bizdemo token remains and the list shows only bizdemo sessions.

EXP-26 — `Clear all` (text button, 14 px ink3, struct on hover) appears only when the search text is non-empty or a filter differs from its default. It clears the search text, removes every filter and restores "automated hidden". It does not change the period, measure, sort or grouping (EXP-R scope bar item 6).
  Scenario: Given search `rbac`, model opus 5.5 and automated shown, when the user clicks Clear all, then the field is empty, no model token remains, the automated token reads `automated hidden · 12`, and the totals line returns to `212 sessions` for the same period.

## 7. Hide automated

EXP-27 — Default: automated sessions are hidden, and the scope bar shows the automated token `[repeat] automated hidden · N`, where N is the count in the period under the other filters (EXP-R scope bar item 4). The totals line repeats "N automated hidden" (EXP-12).

EXP-28 — Removing the token (or switching off the popover toggle) shows automated sessions: they join totals, chart, project panel and list, and the token disappears. In the list they carry the `repeat` glyph instead of the provider mark (LIST-R §7). Switching the toggle on again hides them.
  Scenario: Given 12 automated sessions in the period, when the user removes the automated token, then the session count rises by 12 and those rows show `repeat` in the flags lane.

EXP-29 — When N = 0 and automated sessions are hidden, the automated token is not shown; the toggle stays in the filter popover (EXP-24) (Kai 2026-10-12).

## 8. Day chart (A)

EXP-30 — Header row of the chart region: the measure segmented control at the left (EXP-31), then the hint "Click a day · drag across days" 14 px ink3 (spike-overview `Per day` head; EXP-R final design: the measure toggle sits at the chart's top-left).

EXP-31 — Measure control: segments `[circle-dollar-sign] Cost` and `[coins] Tokens`, 32 px, 14 px sentence case; the selected segment has an l3 fill and ink1 500 text, the other ink3 (SYS-4: ink2 is for figures). Default Cost. The measure changes the chart bars, the bar value label, the max rule, the day tooltip, and the project panel's ring, centre figure and legend values. It does not change the totals line, the list's columns or its sort (D32 "cost and tokens measure"; EXP-R "Measure toggle": "Do not tie list sort to the measure"). In Tokens mode, values that were money use ink2 mono instead (OVR-R §3 "Measure toggle").
  Scenario: Given the list sorted by Started, when the user selects Tokens, then bar heights change and the legend shows token figures in ink2, and the list order is unchanged.

EXP-32 — Window: the chart shows one column per local day in its window. The window is 21 days ending at the period end; when the period ends within the last 14 days, the window ends today instead. A period longer than 21 days shows one column per day for the whole period, up to 92 days. For periods over 92 days the chart shows week columns (local-Monday sums of the buckets, interfaces QRY-25), with the same band, label and tooltip rules (D38 M10). Partial weeks at the period edges are clipped to the period: their figures come from the in-range days, and the tooltip names the dates covered (D41 M10). Days in the window outside the period stay visible, so the period can be compared with its neighbours (OVR-R §2 recommendation A).

EXP-33 — Bars: plot height 200 px; each bar is stacked by project colour slot (SYS-11), with the same slot order in every bar; height is linear from 0 to the largest day in the window. Each column has 4 px gaps; an extra 8 px gap precedes each Monday (OVR-R "Day labels"; SYS-R §5 #23).

EXP-34 — Day labels under the bars: the date number only (`7`), 14 px ink3; ink1 for days in the period. The weekday and full date are in the column's tooltip and accessible name ("Wed Oct 7, $549.34") (OVR-R "Day labels"; SYS-R §5 #23).

EXP-35 — Period band: days in the period get an l2 column band, radius 6, behind the bar, and their labels turn ink1. Bars of days outside the period draw at 55 % opacity (OVR-R §3 "Selected range").

EXP-36 — Values: only the highest day inside the period carries a value label above its bar, 12 px mono ink2 (SYS-15 allows 12 px chart numerals). One dashed `--color-rule` line marks the window's maximum day, with its value at the left end, 12 px mono, money in Cost mode, ink2 in Tokens mode. Every other day's value is in its tooltip (OVR-R §r3 "Money"; spike-overview `.maxr`).

EXP-37 — Day tooltip (hover or keyboard focus on a column): `Wed Oct 7` and the day total on the first line; then up to 4 projects with their dot, name and value; then "N more" ink3 when more projects had activity that day (OVR-R §3 "Hover (day)"; spike `tip`).

EXP-38 — Click a day: the period becomes that single day (custom single day). Drag from one column across others: the period follows the pointer live and becomes the dragged range when the pointer is released. A press and release on the same column is a click (OVR-R §1 flows; spike-overview `pointerdown`/`pointerup`).
  Scenario: Given the period Last 7 days, when the user presses on Oct 1 and releases on Oct 8, then the title reads `Oct 1 – Oct 8` and the band covers 8 columns.

EXP-38a — Keyboard (Kai 2026-10-12): the day columns are one group in the tab order; ←/→ move focus between days (showing that day's tooltip); Enter sets the period to the focused day; shift+←/→ extends the period from the focused day.
  Scenario: Given focus on Oct 7, when the user presses shift+→ twice, then the period is Oct 7 – Oct 9.

EXP-39 — The chart honours every filter (search, provider, model, branch, project, automated), not the period. With a project filter on, only the filtered projects' segments are drawn (spike-overview `matrix`). Filters select sessions: with a model filter, bars show the whole day split of each matching session, not only its calls to that model (Kai 2026-10-12).

EXP-40 — Hovering a session row (or moving the list cursor onto it) outlines, in struct 1.5 px with radius 6, every day column in which that session made calls, and draws a 1 px ink1 box around that session's share inside each of those bars (OVR-R top 5 #4, §3 "Hover (session)"). The outline clears when the pointer leaves the row.
  Data: `day_split` per session (EXP-3).

EXP-41 — Hovering a project in the project panel dims every other project's segments in the bars to 0.28 opacity (OVR-R §3 "Hover (project row)").

## 9. Project panel: donut and ranked legend

EXP-42 — Head: "Projects" 14/600 ink1, then the project count in ink3 (OVR-R §r3; spike donut option 1, Q27 = 1 in D32).

EXP-43 — Values: one value per project = the sum of the measure over in-scope sessions of that project, using full session figures (EXP-2). The panel applies the period and every filter **except** the project filter, so other projects remain visible and selectable (spike-overview `projTotals`). Projects are ranked by value, highest first.

EXP-44 — Donut: 168×168 px ring, outer radius 80, inner 52, starting at 12 o'clock, clockwise in rank order. The top 6 projects get one slice each in their slot colour (a top-6 project whose slot is "other" is grey); all remaining projects share one `--color-series-other` slice. The centre shows the total (14/600; money in Cost mode, ink1 mono in Tokens mode) and "N projects" 14 px ink3 below it. Each slice has a tooltip: name, value, share (spike-overview `optDonut`).

EXP-45 — Legend, right of the donut (16 px gap): one 28 px row per top-6 project: 10 px dot, name 14/500 ink1 (ellipsis), value (mono; money in Cost mode, ink2 in Tokens mode), share (`82%`, `<1%`, 40 px column, ink3). Then the fold row `Show N more · $X` (SYS-38), which expands the remaining projects in place, scrolling inside a 196 px cap. Row tooltip: "<name> · N sessions · click to filter, shift-click to add". Every project, including the tail, is nameable and works as a filter (D32).
  Scenario: Given 26 projects in scope, then the legend shows 6 rows and "Show 20 more · $465.12"; clicking it shows all 20 more in a scrolling list.

EXP-46 — Project filter from the panel (D32; OVR-R §1 flows, §r3):
- click a legend row or slice: the project filter becomes exactly that project; clicking the only selected project clears the filter;
- shift-click or ⌘/Ctrl-click: adds the project to, or removes it from, the filter;
- clicking the grey "other" slice expands the tail;
- selected projects: struct-soft fill and struct name in the legend; unselected rows switch to ink3 text (not opacity, SYS-62); unselected slices dim to 0.3 opacity;
- each selected project appears as a scope-bar token (EXP-25);
- Esc, with no popover, palette, focused field or open blade, clears the project filter (OVR-R §1 flows).
  Scenario: Given no project filter, when the user clicks proj-b then shift-clicks proj-c, then two tokens show, the list holds only those projects' sessions, and the other legend rows are ink3.

## 10. Session list (A ledger)

### 10.1 Header and controls

EXP-47 — List head line: "Sessions" 14/600 ink1, the in-scope count in ink3, then at the right the group control "Group: None ⌄" (`layers` + label; options None, Project, Day) (EXP-R final design: Group by lives in the list header; D32: grouping optional). The settings question (§11) may add more controls here.

EXP-48 — Default state: flat (no grouping), sorted by start time, newest first (D32; LIST-R §r3 "Grouping and sort behaviour").

EXP-49 — Columns, left to right, for the flat list (LIST-R §r3 recommended default `grp=none · cols=active,tokens,agents · prov=1 · model=off · bar=left`, widths from spike-list-r2 `gridA`):

| Slot | Head | Width | Content |
|---|---|---|---|
| When | Started | 88 | weekday on change + `13:44` (SYS-34), local start time of the session |
| Project | Project | 136 | 8 px dot + project name in its series colour, 14/500, ellipsis (D32: name in hue) |
| Name | Session | 1fr | display name (SYS-30) |
| Flags | — | 128 | EXP-52 |
| Magnitude | Activity | 200 | activity strip 200×28 (SYS-40) |
| Primary | Cost | 184 | 96 px `--list-bar` track (SYS-39) + cost 14/500 money; 600 when ≥ the 90th percentile of the costs of all in-scope rows (D38 L7) |
| Secondary | Wall | 64 | full wall, mono ink2 |
| Secondary | Active | 64 | active time, mono ink3 |
| Secondary | Tokens | 56 | full tokens, mono ink3 |

Rows are 44 px (P1, SYS-33). No left border, no row tint, no model mark, no error mark (D32).
  Verify: at 1440 px the name column is at least 280 px wide (LIST-R §r3 "about 300 px").

EXP-50 — The column header is a sticky band: l2, 1 px `--color-rule` border, 8 px radius, 40 px tall. Heads are 14/500 ink3; sortable heads go ink1 on hover; the active sort head is ink1 with `arrow-down-narrow-wide` (descending) or `arrow-up-narrow-wide` (ascending). Only the active head shows an arrow (LIST-R §r3 "Header band").

EXP-51 — Sort: Started, Cost, Wall, Active and Tokens are sortable. Clicking the active head reverses the direction; clicking another head sorts by it, descending. Ties are broken by start time, newest first (core.js `computeVisible`). Sort is global across the flat list (D32).
  Scenario: Given the default list, when the user clicks Cost, then rows are ordered by full session cost, highest first; clicking Cost again orders lowest first.

EXP-52 — Flags lane, fixed order, each with a tooltip, each hidden when it does not apply (LIST-R §9 P1 slot 3, §r3):
1. provider mark (tooltip: provider name); for an automated session `repeat` instead (tooltip "Automated · Claude Code");
2. `bot` + count, when the session has 2 or more sub-agents (tooltip "6 sub-agents");
3. `+Nd` in ink2 when the session ends N local midnights after its start (tooltip "Ends Wed 11:41").
  Data: sub-agent count per session (`n_agents`, distinct sub-agent ids, needs S2/S3; `n_engagements` counts engagements, not agents).

EXP-53 — The activity strip's bar scale is shared by every row in the visible list (SYS-40), so a heavy session's bins are visibly taller than a light one's. Midnight ticks mark local midnights.
  Data: `activity_bins` (SYS-40).

EXP-54 — Row interactions:
- click, or Enter on the cursor row: opens the session blade; the explorer becomes the rail (SYS-46, EXP-60);
- hover: l3, the chart footprint (EXP-40);
- `j`/↓ and `k`/↑ with the explorer focused and no blade open: move the keyboard cursor (2 px struct outline); the first press selects the first row; the row scrolls into view; the chart footprint follows the cursor;
- the row of the open session keeps the struct-soft fill and struct name (SYS-35).
  Scenario: Given no blade open, when the user presses j twice then Enter, then the second row's session opens in blade 1.

EXP-55 — The flat list renders every in-scope session with virtual scrolling; it has no fold (LIST-R §r3: "the fold is a prototype limit; the real list virtualises").

### 10.2 Optional grouping

EXP-56 — Group by Project or Day (EXP-47). Group headers use P2 (SYS-37) with the group's totals in the row columns (cost, wall, active, tokens sums) and the group's aggregate activity on the period axis in the Activity column. Day groups have no dot; their name is `Mon Oct 12` (local start day). When grouped by project the Project column is hidden (LIST-R §r3).

EXP-57 — Sort applies within each group; groups are ordered by the same measure and direction: the sum of cost, wall, active or tokens, or, when sorting by Started, the latest start in the group (D32; LIST-R §r3).
  Scenario: Given Group: Project and sort Cost descending, then the project with the highest summed cost comes first and its rows are ordered by cost.

EXP-58 — Each group shows its top 5 rows under the current sort, then the fold row `Show N more · $X` (SYS-38). Projects with exactly one session costing under $1.00 fold into one "Small projects" group, placed last and collapsed by default (LIST-R §9 P2, §r3).

EXP-59 — Clicking a group header collapses or expands its rows (chevron rotates). Groups other than "Small projects" start expanded.

### 10.3 Rail (400 px)

EXP-60 — When one blade is open to the right, the explorer shows the rail: the 400 px standard parent width (the maintainer 2026-10-12), 16 px padding, the same in-scope sessions, order, grouping and folds as the list (LIST-R §5, §r3 "Rail"; SYS-46). The period title, totals, scope bar, chart and project panel are not shown in the rail.

EXP-61 — Rail header: "Sessions" 14/600 and the cursor position `3 / 214` mono ink3, which updates on j/k. Below it, a search field bound to the same search text as EXP-17 (core.js `#rq`). Footer: key hints "j k move · ↵ open · esc back" in 14 px ink3 with 12 px key caps.

EXP-62 — Rail row (compact P1, SYS-36), two lines, 8 px padding:
- line 1: display name (flex, ellipsis) + cost (money; 600 in the top 10 %);
- line 2: When (weekday on change + time) · project dot + name in hue (hidden when grouped by project) · spacer · activity strip 88×16.
Rail group headers are 40 px with name 14/600 and the group cost.

EXP-63 — In the rail, j/k (with the rail focused) moves the cursor and opens that session in blade 1, replacing the open session and closing blades to its right (core.js `moveSel` with blades open). Enter does nothing more. Clicking a rail row does the same as moving the cursor to it.
  Scenario: Given session 3 of 214 open, when the user presses j in the rail, then session 4 opens in blade 1 and the header reads `4 / 214`.

EXP-64 — Cursor and open state can be on the same row: 2 px struct outline for the cursor, struct-soft fill + struct name for the open session (LIST-R §5).

EXP-65 — If the open session leaves the scope (the user changes the period or a filter through ⌘K while it is open), its blade stays open and no rail row shows the open state (core.js `refresh` keeps blades).

EXP-66 — With two or more blades to the right, the rail stays a 400 px blade and moves off-screen left as the strip scrolls (SYS-46). It is reached by scrolling the strip, the "Sessions" crumb, ← or Esc (SYS-47); reaching it does not close blades, except through the crumb or Esc (the maintainer 2026-10-12).

## 11. Settings (open)

The prototype has a gear that opens a settings panel (group by, project colour treatment, strip colour, length and scale, strip and row height, time format, columns, provider icon, model mark, cost bar position, name weight; non-default values in the URL hash). D32: "A settings panel (gear) for tuning stays in the prototype; whether it ships is open." v1 has no settings gear; only the group control (EXP-47) ships, and the recommended defaults above are fixed values in the build (Kai 2026-10-12).

## 12. ⌘K palette

EXP-67 — ⌘K / Ctrl+K opens a centred palette (660 px wide, l3, `--shadow-pop`, `--radius-card`) over a dimmed stage, with a 20 px search input ("Jump to a session, project or date") focused (core.js `openPalette`; EXP-R "⌘K palette").

EXP-68 — Empty input shows three sections, each with a 14/600 sentence-case head (sticky while scrolling): **Dates** (Today, Yesterday, Last 7 days); **Filter by…** (`cpu` `model:`, `git-branch` `branch:`, provider mark `provider:`, project dot `project:`, each with an ink3 example such as `model:opus`); **Largest in this period** (the 6 in-scope sessions with the highest cost) (EXP-R "⌘K palette").

EXP-69 — Typed input shows, in order, only the sections with results:
1. **Filters**, when the input starts with `model:`, `branch:`, `provider:` or `project:`: matching values (substring, case-insensitive) with their session count;
2. **Dates**: forms `oct 7`, `7 oct`, `10/07`, `2026-10-07`, `today`, `yesterday`, weekday names (the latest such day up to today), `this week`, `last week`, `last 7`/`7d`, `all`;
3. **Projects**: up to 4 projects whose name contains the input;
4. **Sessions**: up to 8 sessions from all time matching every term (EXP-19 rule), ranked by the number of terms found in the display name, then an exact project-name match, then newest start (core.js `searchSessions`).
Input that matches both a date and sessions lists dates first. No results: "Nothing matches “<input>”." 14 px ink2.
  Data: all-time session search (needs S3).

EXP-70 — Row looks: 16 px leading glyph by kind (date `calendar`; project its dot; session its provider mark; filter its filter glyph). Date rows: label, sub-line with that day's facts `19 sessions · $559.00 · 1.93B tokens`, and the date range at the right for ranges. Project rows: name, sub-line `N sessions`, cost right. Session rows: display name, sub-line project dot + name in hue, date and time in ink3, cost right in money. The selected row has an l4 fill and a 2 px struct bar at its left edge. Footer: `↑↓ move · ↵ open · esc close` 14 px ink3 with key caps (EXP-R).
  Data: per-day facts for date rows (needs S3).

EXP-71 — Keys: ↑/↓ move the selection (wrapping); Enter runs it; Esc closes; a click outside the palette closes it; pointer movement over a row selects it.

EXP-72 — Actions (EXP-R top 5 #5; core.js `palRun`):
- a date row sets the period and nothing else: search and filters stay; all blades close;
- a filter row adds that value to its filter and closes all blades; period unchanged;
- a project row sets the project filter to that project; if `n_sessions > 0` and all its sessions are automated (`n_automated = n_sessions` on the palette project row; D38 M5; D41 N2), automated sessions are shown; period unchanged;
- a session row opens that session in blade 1. If none of its calls fall in the period, the period becomes the session's first day with a model call (`first_day`: the first local day with `n_llm_calls > 0`; D38 M5), not its start day. If the search or filters hide it, they are cleared (and automated shown if needed), and a toast says "Filters cleared to show this session" with an "Undo" button that restores the previous search, filters and period while the session stays open.
  Scenario: Given model filter opus 5.5 and search `rbac`, when the user picks "oct 7" in the palette, then the period is Wed Oct 7 and the model token and search text remain.

EXP-73 — Toast: bottom centre of the stage, l3, `--shadow-pop`, 14 px ink1 text and a struct "Undo" button; it closes when Undo is used, on Esc, or after 8 s; the timer pauses while the pointer is over it (Kai 2026-10-12).

## 13. URL state

EXP-74 — The explorer's state is in the URL query, so reloading or sharing a link reproduces the view. Parameters with default values are omitted (brief; the prototype's settings hash used the same rule, LIST-R §r3):

| Param | Values | Default |
|---|---|---|
| `period` | `today`, `yesterday`, `last7`, `thisweek`, `lastweek`, `all` | `last7` |
| `from`, `to` | `YYYY-MM-DD` (custom period; replaces `period`) | — |
| `q` | search text | empty |
| `provider` | `claude_code`, `codex`, `omp`; repeatable | none |
| `model` | model id; repeatable | none |
| `branch` | branch name; repeatable | none |
| `project` | project key (the raw cwd, D1); repeatable | none |
| `auto` | `show` | hidden |
| `measure` | `tokens` | `cost` |
| `sort` | `start`, `cost`, `wall`, `active`, `tokens` | `start` |
| `dir` | `asc`, `desc` | `desc` |
| `group` | `project`, `day` | none |

Open blades add their own parameters, defined in `session.md`, `turn.md` and `detail.md` (S1b).
  Scenario: Given the URL `?period=yesterday&sort=wall`, when the page loads, then the title reads `Yesterday · Sun Oct 11` and the list is sorted by wall, longest first (acceptance case 2 of D28).

EXP-75 — A parameter with an unknown value is ignored and its default applies; the URL is rewritten without it. A filter value that matches nothing (e.g. a deleted branch) is kept: its token shows and the no-results state follows (EXP-79).

EXP-76 — Every scope change (period, search, filter, measure, sort, group) updates the URL in place, without adding a history entry. Opening a blade adds a history entry, so browser Back closes the rightmost blade (Kai 2026-10-12).

## 14. States

EXP-77 — First load: totals figures, chart plot area, donut ring and legend rows, and 8 list rows show skeletons (SYS-54). The period title and scope bar render at once from the URL.

EXP-78 — Scope change: the previous content stays in place until the new data arrives, then updates; the list fades from 0.45 to 1 opacity over 120 ms (core.js `refresh`; none under reduced motion). No skeleton flash.

EXP-79 — No results (search or a filter active, 0 in-scope sessions) (EXP-R "Empty and no-result states"):
- the project panel and the list column header are hidden; the chart stays (it is navigation);
- in the list region: `search-x` 24 px ink3, "No sessions match" 20/600 ink1; below, the active tokens repeated (removable); a struct "Clear filters" button (same effect as Clear all); and, when the search text is non-empty, `"<text>" matches N sessions in all time` with a "Show" link that sets the period to All time, or `"<text>" matches 0 sessions in all time`.
  Scenario: Given search `rbacxyz` with 3 matches outside the period, then the line reads `"rbacxyz" matches 3 sessions in all time · Show`, and Show sets the period to All time.
  Data: all-time match count for the search text under the other filters (needs S3).

EXP-80 — Empty period (no in-scope sessions and no search or filter active): `calendar-x` 24 px ink3, "No sessions on Sun Oct 4" (or "No sessions from Oct 1 to Oct 3"), and up to two struct buttons for the nearest earlier and later days with sessions: `Sat Oct 3 · 12 sessions`, `Mon Oct 5 · 9 sessions`; clicking sets the period to that day. The chart stays; the project panel and column header are hidden. Totals read `$0.00   0 tokens   0 sessions` (a real zero, not loading).
  Data: nearest days with sessions (needs S3).

EXP-81 — Error: a failed request shows the error card (SYS-56) in the region it feeds (totals line, chart, project panel or list); other regions keep working. Retry repeats only that request.

EXP-82 — No store (D22): the banner of SYS-57 sits under the top bar. Totals show `0 sessions`; the chart, project panel and list show the empty sentence "No data yet." The period title, scope bar and palette stay usable.

EXP-83 — Server unreachable: every region shows the error card with "Couldn't reach the Botsworth server" and Retry. The product never shows mock data; mock data is for the dev build only (Kai 2026-10-12, narrowing D22).

## 15. Data the explorer needs (summary for S2/S3)

| Need | Used by | Status |
|---|---|---|
| Session: `display_title`, `title_source` (D34 order) | list, rail, palette | needs S2/S3 |
| Session: project key (raw cwd) and short `project_name`; project `color_slot` | everywhere | needs S2/S3 (`project` exists; name and slot do not) |
| Session: provider, start_ms, end/last_ms, `incl_cost_usd`, `incl_tok_total`, `wall_ms`, `active_ms`, `git_branch`, `cost_lower_bound` | list, totals | exist in `sessions` |
| Session: `is_automated` | automated rule | needs S2/S3 (per-turn human-ask flag) |
| Session: `n_agents` (distinct sub-agents) | flags lane | needs S2/S3 |
| Session: `models` used | model filter | needs S2/S3 |
| Session: `active_days` and `day_split` (cost, tokens per local day) | membership, chart, footprint | needs S2/S3 |
| Session: `activity_bins` | activity strip | needs S2/S3 |
| Session: `search_text` incl. ask snippets | search, palette | needs S2/S3 |
| Day × project totals for the chart window under filters | chart | needs S3 |
| Filter values with counts under the other filters | filter popover, palette | needs S3 |
| All-time search count; nearest days with sessions; per-day facts | empty states, palette | needs S3 |
| `last_ingest_ms`, `store_exists` | freshness, no-store banner | exists (D22) |
| `Scope.tz` (request zone; the browser zone) | day buckets and labels | D50 |
| Ingest-running flag; store schema version | SYS-57a banner, v6 banner | S3 (D37) |

## 16. Resolved questions

| # | Question | Ruling | Applied in |
|---|---|---|---|
| Q1 | Which timezone defines a local day? | The request zone: the browser zone, sent as `tz` with every request and named in the period popover footer (D50, which restores the browser-zone ruling of Kai 2026-10-12 and replaces D37's store zone). Re-check D28's Oct 7 figures once `day_split` exists. | §0, EXP-13 |
| Q2 | Chart window placement; periods over 21 days | 21 days ending at the period end, or today when the period ends within the last 14 days; one column per day up to 92 days (Kai 2026-10-12) | EXP-32 |
| Q3 | Chart keyboard access | Yes: one tab group, ←/→, Enter, shift+←/→ (Kai 2026-10-12) | EXP-38a |
| Q4 | Does the settings gear ship? | No gear in v1; only the group control (Kai 2026-10-12) | §11, EXP-47 |
| Q5 | Toast timeout | 8 s, paused on hover (Kai 2026-10-12) | EXP-73 |
| Q6 | Browser Back | Back closes the rightmost blade; scope changes replace the URL (Kai 2026-10-12) | EXP-76 |
| Q7 | Mock data when the server is unreachable | Not in the product; dev build only (Kai 2026-10-12) | EXP-83 |
| Q8 | Single or multi-select filters | Provider, model and branch are all multi-select (Kai 2026-10-12) | EXP-21–23, EXP-25, EXP-72, EXP-74 |
| Q9 | Wall as a measure | Cost and Tokens only (Kai 2026-10-12) | EXP-31 |
| R1 | Automated token at N = 0 | Hidden; the toggle stays in the filter popover (Kai 2026-10-12) | EXP-29 |
| B1 | Blade navigation | Continuous blades: rail at the 400 px standard width, no spine (the maintainer 2026-10-12) | EXP-60, EXP-62, EXP-66 |
| R2 | Model filter and the day split | Confirmed: filters select sessions (Kai 2026-10-12) | EXP-39 |
