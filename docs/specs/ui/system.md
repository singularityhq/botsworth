# UI system: shared visual and interaction rules (S1a)

Status: draft for implementation, 2026-10-12. Owner: spec writer S1a.
Scope: the rules every view obeys (explorer, session, turn, detail). Page behaviour is in `explorer.md` (this writer) and `session.md`, `turn.md`, `detail.md` (S1b). Where a page spec and this file disagree, this file wins unless the page spec names the exception and its source.

Sources, in priority order: the decision log (D28–D34), `prototype notes (not in this repo)/CONSOLIDATION-NOTES.md`, then `REVIEW-v4.md` (the system review, abbreviated **SYS-R** below), `REVIEW-v4-list-r2.md` §9 and §r3 (**LIST-R**), `REVIEW-v4-session-r2.md` §7 (**SESS-R**), `REVIEW-v4-explorer.md` (**EXP-R**), `REVIEW-v4-overview.md` (**OVR-R**), `tokens.css` and `core.js`/`core.css` in the same folder (the prototype).

Requirement IDs are `SYS-<n>`. Each states an observable result. "Verify" lines say how a reviewer or test checks it.

## 0. Terms

Model terms (session, engagement, turn, llm_call, tool_call, op, inclusive cost) are those of `docs/design/design.md` §4. Terms new to the UI:

| Term | Meaning |
|---|---|
| **Blade** | A full-height panel in the horizontal stack. Blade 0 is the explorer; each drill-down (session, turn, detail) adds one blade to the right (D29, D33). |
| **Strip** | The horizontal row of blades (Azure-style continuous blades, Miller columns). It scrolls horizontally (the maintainer 2026-10-12). |
| **Parent blade** | Any blade that is not the newest. Every parent blade has the one standard width, 400 px. |
| **Rail** | The explorer's session list in its 400 px parent-blade form (LIST-R §5; width from the maintainer 2026-10-12). |
| **Focused blade** | The one blade that receives keyboard input. |
| **Lead** | The one text element on a line that carries ink1 at weight 500 or 600 (SYS-R summary rule 1). |
| **Zero by construction** | A value that cannot exist for the item (cost of a step with no model call). Distinct from a measured zero. |

## 1. Tokens

### 1.1 Surfaces

SYS-1 — The app uses exactly these surface colours (tokens.css):

| Token | Hex | Use (SYS-R §3) |
|---|---|---|
| `--color-ground` | #080e1a | app background behind blades |
| `--color-l1` | #0e1522 | blade and panel surface |
| `--color-l2` | #121a29 | inset wells (code, asks), chart selection band, sticky list header band, group header band |
| `--color-l3` | #172032 | pointer hover on rows, selected segment of a segmented control, popover surface |
| `--color-l4` | #1c2438 | key caps and tooltips only |
| `--color-hair` | rgba(255,255,255,.06) | hairline between regions |
| `--color-rule` | #27304a | borders of bands and containers |
| `--color-rule-soft` | #1a2236 | hairline between list rows |

SYS-2 — One panel uses at most two layers: l1 plus one of l2 or l3. Cards inside cards are not allowed (SYS-R §3).
Verify: no element with an l2 or l3 background contains another element with a different layer background, except popovers and tooltips.

SYS-3 — Elevation (shadow) appears only on blades and floating layers: `--shadow-pane` on resting blades, `--shadow-pane-focus` on the one focused blade, `--shadow-pop` on popovers, the palette and tooltips (SYS-R §3; tokens.css).

### 1.2 Ink scale

SYS-4 — Text uses three inks (D31; SYS-R §2):

| Token | Hex | Role |
|---|---|---|
| `--color-ink1` | #eaecf5 | content, the line lead, titles |
| `--color-ink2` | #aab0c8 | secondary figures only (a number that is not the lead), and the one step-quieter icon beside ink1 text |
| `--color-ink3` | **#838ba3** | metadata, units, captions, column heads |

The prototype's `tokens.css` still holds ink3 = #949cb6. The build uses #838ba3 (D31).
Verify: computed `--color-ink3` is `#838ba3`.

SYS-5 — ink2 and ink3 are never the only difference between two levels of text. A level change uses ink1 versus ink3, or a weight change (SYS-R §2 "measured").

### 1.3 Colour axes

Each axis has one meaning. A colour from one axis never carries another axis's meaning (D31; SYS-R §2 "Colour axes").

SYS-6 — Navigation and selection: `--color-struct` #ffb26f, `--color-struct-soft` rgba(255,178,111,.08) (D44), `--color-struct-line` rgba(255,178,111,.34). Used for hover, focus, selected items, and links. Never on data values.

SYS-7 — Money: `--color-money` #ff8fa8 on cost figures, cost bars and cost labels only. `--color-money-bar` #393455 is the opaque fill of a bar that sits behind a money figure. The list cost bar (a separate bar beside the figure, never under it) uses `--list-bar` #c46f88 (LIST-R §9 P3; 5.20:1 on l1). Tokens, durations and counts are never money-coloured.

SYS-8 — Error: `--color-error` #ff6b6b (D31). It colours only the `circle-alert` glyph and the status text beside it ("exit 1", "interrupted", "3 errors"). It never colours a figure, bar or fill, and never shares a cell with a cost figure. Error is always glyph plus text; colour is the third cue (SYS-R §2 "Error token").

SYS-9 — Kind: `--color-type-user` #66d3d4, `--color-type-team` #c6aee8, `--color-type-sys` #62b9f4. Used only as kind marks; inside the turn blade the who-glyph may take this tint (SYS-R §5 #14). Assistant output is never teal.

SYS-10 — Projects: `--color-series-1` … `--color-series-6` (#65a7fa, #f0834e, #54c8b1, #ad87ed, #81ce70, #dd71c8) and `--color-series-other` #878d93. A project colour appears only on: the project dot, the project's bar or slice segments, and the project name text. Never on icons (SYS-R §1 rules), never as a row border or row tint (D30, D32).

SYS-11 — A project keeps the same colour slot when the period or filters change. Slots 1–6 go to the six projects ranked highest by a stable rank; every other project uses `--color-series-other` (OVR-R §6 item 5). Data: the slot comes from the backend as a per-project field (`color_slot`, needs S2/S3). Ranking: cost over the last 30 days ending at the latest ingested day, recomputed at ingest, so colours change at most once per ingest and never on a period change (Kai 2026-10-12).

SYS-12 — Models are never marked by a project colour. A model is identified by its shape plus its name (D31; CONSOLIDATION-NOTES). Charts stacked by model use a separate ramp that does not reuse any series colour; the legend carries the shape (CONSOLIDATION-NOTES). The ramp is defined in `session.md` SES-22 (D37).

SYS-13 — Control edge: `--color-edge` #5c6888 outlines inputs and unselected toggles. Edged controls sit only on l1 or l2, because the edge fails 3:1 on l3 and l4 (D31; SYS-R §2).

## 2. Typography

SYS-14 — Families: `--font-sans` 'IBM Plex Sans Variable'; `--font-mono` 'IBM Plex Mono' (tokens.css). Mono is used for figures that align in columns (cost, tokens, durations, clock times), ids, commands, paths and code. Mono is not used for sentences or labels (SYS-R §2 "Mono").

SYS-15 — Exactly four sizes (D31; SYS-R §2):

| Token | px / line height | Use | Weight |
|---|---|---|---|
| `--text-display` | 30 / 36 | the one hero figure set per blade | 500, tabular |
| `--text-lead` | 20 / 26 | blade titles, the explorer period title and totals, group header names | 600 |
| `--text-body` | 14 / 20 | everything else, including captions and secondary text | 400 meta, 500 line lead, 600 section heads |
| `--text-label` | 12 / 16 | mono numerals in charts (axis ticks, bar values, max-rule figure) and key caps only; the one word allowed is the TRN-31 "final" tag (D31 exception, D38 L3). Never other words. | 400 |

Verify: no computed font size outside {30, 20, 14, 12}; every 12 px element is mono and contains only digits, currency, units of a chart numeral, or a key cap, except the TRN-31 "final" tag (D38 L3).

SYS-16 — Hierarchy comes from weight and ink, not new sizes. Each line of text has one lead (ink1, 500 or 600); everything else on the line is ink3 400, except secondary figures in ink2 (SYS-R summary rules 1–2).

SYS-17 — No uppercase plus letter-spacing on text people read. Captions and section heads are 14/600 ink1 sentence case, followed by a count in ink3 ("Projects 26"). The brand wordmark is the only exception (SYS-R §2 "Caps").

SYS-18 — All figures use tabular numerals (`font-variant-numeric: tabular-nums`) (D31).

SYS-19 — Prose wells (asks, assistant text) have a maximum measure of 76 ch (SYS-R §2).

## 3. Spacing, density and shape

SYS-20 — Spacing is on a 4 px grid with these steps (SYS-R §3):

| Step | px | Between |
|---|---|---|
| tight | 4 | icon and badge; the two lines of one row |
| inner | 8 | items inside a group (chips, tokens of a meta line). Use an 8 px gap, not "·" |
| group | 16 | label and control; stat and stat; row padding |
| section | 32 | sections inside a blade; above a list |
| panel | 24 | blade inner padding (rail: 16) |

SYS-21 — The "·" separator appears only inside running text where an 8 px gap would be ambiguous. It is ink3.

SYS-22 — Heights: full list row 44 px (36 px dense); rail row two lines with 8 px padding; transcript step row collapsed 32 px; controls 32 px (28 px in the rail); section head 20 px plus 8 px below; icon-only controls at least 28×28 px (SYS-R §1, §3; LIST-R §9 P1).

SYS-23 — Radii: exactly four, `--radius-pane` 16, `--radius-card` 10, `--radius-control` 7, `--radius-chip` 4 (tokens.css). Group header bands and the sticky column header use 8 px, as rendered in the prototype (LIST-R §9 P2, §r3); this is the only non-token radius and is recorded here as an exception.

## 4. Icons

SYS-24 — Icon library is Lucide (`lucide-react`) at 16 px with a 1.5 stroke (`absoluteStrokeWidth`), plus three custom provider marks drawn on the same 24 grid with a 1.5 stroke, round caps and joins, line only: Claude Code = 6-ray asterisk; Codex = hexagonal knot of 3 interlocked strokes; omp = "π" inside a 20 px circle. No brand logos, no brand colours. Do not mix in another icon set (D31; SYS-R §1).

SYS-25 — Sizes: 16 px inline with 14 px text; 20 px beside 20 px titles; 24 px only in empty states. Icon-to-text gap 6 px; at 16 px, `vertical-align: -3px` against 14 px Plex (SYS-R §1 rules).

SYS-26 — Icon colour is the ink of the text beside it, one step quieter: beside ink1 text the icon is ink2, otherwise ink3. Exceptions: struct on a hovered, focused or selected control and on inline navigation links (`arrow-up-right`); `circle-alert` in `--color-error`; the turn blade's who-glyph may take a `--type-*` tint. Icons are never money-coloured and never project-coloured (SYS-R §1 rules).

SYS-27 — An icon replaces a text label only when the same label would repeat on every row of a list and the icon has an `aria-label` and a tooltip (provider, tool kind, agent count). Everywhere else, and always in filters, menus and headers, the icon is paired with text. No icon is decoration: a section head gets an icon only if the same kind appears as a blade title or a list row kind (SYS-R §1 rules).

SYS-28 — Glyph map. One glyph per concept; two kinds that can appear in the same list never share a glyph (SYS-R §1 glyph map rev 2, with the explorer's reconciliation in EXP-R "Icons").

| Concept | Glyph |
|---|---|
| Provider: Claude Code / Codex / omp | custom marks (SYS-24) |
| Project | filled dot, 8 px in rows, 10 px in headers, project colour. `folder` is not used |
| Session | `rows-3` |
| Human ask (user turn) | `message-square-text` |
| Slash-command turn | `square-slash` |
| System or plugin message | `plug` |
| Agent-to-agent message | `messages-square` |
| Resume after usage limit (turn) | `rotate-ccw` |
| Gap row: waiting on you, nothing running, before the first message | `hourglass` (D38 L4) |
| Gap row: usage limit | `timer-off` (D38 L4; not used by any other turn-list row) |
| Gap row: waiting for an agent's message | `ear` (D38 L4; not used by any other turn-list row) |
| Folded rows ("N more", harness records, extra projects) | `list-collapse` |
| Session with no qualifying ask (fallback name) | `circle-dashed` |
| Biggest-step finder | `crosshair` (D37) |
| Keys overlay | `keyboard` (D37) |
| Scripted prompt (fallback name origin) | `terminal` |
| Assistant text; final answer = same glyph plus the word "final" | `message-square` |
| Thinking | `brain` |
| Model call | `arrow-left-right` |
| Sub-agent | `bot` |
| Main thread | `user-round` |
| Message to an existing agent (SendMessage) | `send` |
| Agent spawn call | `git-fork` |
| Tool: shell | `terminal` |
| Tool: read / edit / write | `file-text` / `file-pen` / `file-plus` |
| Tool: code search | `search-code` |
| Tool: web | `globe` |
| Tool: skill | `book-open` |
| Tool: MCP, other, unknown (plus the tool name) | `puzzle` |
| Error, failed exit, interrupted (plus text) | `circle-alert` |
| Not in data | `info` |
| Wait / no activity | `hourglass` |
| Model | `cpu` |
| Branch or worktree | `git-branch` |
| Automated session | `repeat` |
| Cost / tokens / wall / active | `circle-dollar-sign` / `coins` / `timer` / `activity` |
| Cache | `database` |
| Date / time | `calendar` / `clock` |
| Empty period | `calendar-x` |
| Search field / no match | `search` / `search-x` |
| Filter / group by | `sliders-horizontal` / `layers` |
| Remove filter token, close blade | `x` |
| Command palette | `command` |
| Expand or collapse (rotates 90°) | `chevron-right` |
| Collapse a section | `chevron-up` |
| Dropdown | `chevron-down` |
| Open in next blade, row end | `chevron-right` |
| Open in next blade, inline link (struct) | `arrow-up-right` |
| Sibling stepper | `chevron-left` / `chevron-right` |
| Copy, then confirmed for 1.2 s | `copy` → `check` |
| Sort direction on the active column | `arrow-down-narrow-wide` / `arrow-up-narrow-wide` |
| Active preset in a menu | `check` |

Not used: `sparkle`, `folder`, `bot-off`, `wrench`, `layers` for folds. A new custom glyph needs a sign-off line added to this table.

## 5. Formats: one look per fact

SYS-29 — Every fact has one look in every view (D31; SYS-R §4). The formats:

| Fact | Format | Look | Examples |
|---|---|---|---|
| Cost | `$` + en-US thousands separators + **exactly 2 decimals**, at every magnitude | mono tabular, `--color-money`. 500 when it is the line lead; 600 for the top 10 % of all in-scope rows (D38 L7) | `$2,123.30`, `$549.34`, `$0.06` |
| Tokens | below 1,000: the integer. Otherwise divide by 10³ / 10⁶ / 10⁹ and suffix `k` / `M` / `B`; show 0 decimals when the scaled value ≥ 100, 1 decimal when ≥ 10, else 2 decimals; strip trailing zeros | mono tabular, ink2 (ink3 when a third column) | `290`, `97k`, `12.4M`, `8.69B`, `1.5B` |
| Duration | `<1s`; `Ns` below 60 s; `Nm` below 60 min (rounded); `NhMMm` below 48 h (minutes zero-padded); `NdHHh` at 48 h and above. No spaces | mono tabular. Wall ink2; active ink1 only as a hero | `45s`, `13m`, `1h05m`, `2d03h` |
| Clock time | 24 h `HH:MM`, no seconds (seconds only inside the transcript) | mono, ink1 500 as a row's leading time, otherwise ink3 | `13:44` |
| Time range | `HH:MM–HH:MM`, with `+Nd` in ink2 when the end is N local midnights later | mono | `21:43–11:41 +2d` |
| Date | `Ddd Mmm D` | sans ink3 | `Mon Oct 12` |
| Date range | `Mmm D – Mmm D` (en dash with spaces) | sans | `Oct 6 – Oct 12` |
| Percent share | integer `N%`; `<1%` below 1 %. Always beside its absolute value | sans ink3 | `82%`, `<1%` |
| Provider | the provider mark, 16 px ink3, `aria-label` with the full name. Full name only in filters, tooltips and blade meta | glyph | — |
| Project | 8 px dot plus the name. Name ink2 500 in rows, or in its series colour where a view shows "name in hue" (explorer list, D32); ink1 600 in headers | sans | `● bizdemo` |
| Model | model mark (SYS-41) | — | `◆ opus 5.5` |
| Session name | 14/500 ink1. Fallback name kinds in SYS-30 | sans | — |

Source of the money rule: D31 ("money always 2 decimals"), `deferred.md` "Money format (the maintainer)". This overrides SYS-R §4, which used 0 decimals at $100 and above. The token and duration rules are the prototype's `tokf` and `dur` functions (spike-overview/template.html), which implement SYS-R §4.

SYS-30 — Session display name and its look (D34 order; LIST-R §9 P1 slot 2):
1. user rename (Claude custom-title, Codex thread_name), else
2. generated title (Claude ai-title, omp title), else
3. the first human ask with at least 4 words that is not boilerplate: 14/500 ink1;
4. otherwise the fallback `project · id8`: `circle-dashed` + mono 14/400 ink3, so it reads as unnamed.
Levels 1–3 render alike (14/500 ink1). There is no command-label level (D37).

Data: the backend supplies `display_title` and `title_source ∈ {rename, generated, first_ask, fallback}` (D37; S2). The UI never builds a name from raw text itself. The name is one line with an ellipsis; the full text is in `title`.

SYS-31 — Missing and zero values (SYS-R §4):
- Zero by construction: the cell is blank. "—", "–", "$0.00" and "(no text)" never appear for it.
- A measured zero that rounds to zero after a model call: `$0.00`.
- While loading: figures show a skeleton, never `$0.00` or `0` (EXP-R "Empty and no-result states").
- Not in data: one ink3 line with `info`, once per panel.

SYS-32 — A cost that excludes unpriced model calls (`cost_lower_bound = true`, design §4.7) is followed by `+` in ink3 (`$12.40+`), with the tooltip "Excludes N calls to unpriced models" (Kai 2026-10-12). Data: N per item (needs S2/S3).

## 6. Shared patterns

These are the "consistent repeating patterns" (D31). A view that shows a row, a group, a magnitude, a stat line or a model uses these patterns and does not render its own variant (SESS-R §7).

### 6.1 P1 Row

SYS-33 — A row is one line, 44 px tall (36 px dense), `padding: 0 16px`, `column-gap: 16px`, laid out as a grid with a fixed slot order (LIST-R §9 P1):

| # | Slot | Width | Content |
|---|---|---|---|
| 1 | When | 88 px | start time; the weekday only when it changes from the row above (SYS-34) |
| 2 | Kind icon + name | `minmax(0,1fr)` | 16 px kind glyph only when the kind varies within the list, then the name, one line, ellipsis, full text in `title` |
| 3 | Flags | 120–128 px | fixed order; each flag hidden when its value is 0; each has a tooltip |
| 4 | Magnitude mark | 200–248 px | P3 |
| 5 | Primary figure | 184 px | cost bar + figure, right-aligned |
| 6+ | Secondary figures | 56–64 px each | Wall, Active, Tokens, in that order, right-aligned |

A view may insert one context column after slot 1 (the explorer list inserts Project, 136 px; LIST-R §r3). Columns that do not apply are dropped from the right end; columns are never reordered. Verify: the same slot holds the same fact in every row list in the app.

SYS-34 — Timetable rule for slot 1: the weekday (`Mon`, 14/400 sans ink3, in a 32 px box) appears only when the row's local start day differs from the row above. Under a single-day period no weekday appears. When the list is not sorted by start, consecutive rows rarely share a day, so most rows show it; this is correct (LIST-R §2).

SYS-35 — Row states (LIST-R §9 P1; SYS-R §3):
- rest: l1; rows separated by a 1 px `--color-rule-soft` hairline, no zebra;
- pointer hover: l3 fill;
- keyboard cursor: 2 px struct outline at −2 px offset;
- open (its blade is showing): struct-soft fill and the name in struct;
- cursor and open can show on the same row.
No coloured left border in any state (D30, D32).

SYS-36 — Compact row (rail and other parent blades, 400 px): two lines, 8 px padding. Line 1: name (flex) + primary figure. Line 2: When + view-specific meta + spacer + magnitude mark at the compact size (LIST-R §9 P1 "Compact"; §r3 "Rail"). The explorer's rail content is in `explorer.md`.

### 6.2 P2 Group header

SYS-37 — A group header is a filled band that its rows hang from (LIST-R §9 P2):
- band: l2, 1 px `--color-rule` top and bottom, 8 px top radius, 52 px tall (40 px in the rail), sticky at the top of its scroll container;
- left: `chevron-right` 16 px ink2 (rotated 90° when open) · 10 px dot in the series colour (project groups only) · name 20/600 ink1 (14/600 in the rail) · count 14/400 ink3;
- right: the group's totals in the same grid columns as its rows: primary figure 14/600 money; secondaries mono (first ink2, rest ink3);
- magnitude column: the group's aggregate activity on the period axis (248×32 or the view's strip width, log bar heights scaled to the group's own maximum, ink3, night bands and day lines);
- body: rows sit in a container with a 1 px `--color-rule` border on left, right and bottom and an 8 px bottom radius. No indentation.

### 6.3 Show N more (fold row)

SYS-38 — A list shows its top items and puts the rest one click away (SYS-R §3 density; LIST-R §9 P2):
- the fold row is 36 px: `chevron-right` + "Show N more" in struct text; the sum of the hidden rows' primary figure right-aligned in money (or ink2 when the primary figure is tokens);
- clicking expands in place; the row then reads "Show top N" and its sum is removed;
- the default visible count is 5 for groups of rows and 6 for the explorer's project legend (OVR-R §r3; spike donut), and each view states its own N;
- a fold inside a fixed-height region scrolls internally, capped at 196 px (OVR-R §r3).
Verify: a group of 12 rows shows 5 rows and "Show 7 more · $X" where $X equals the sum of rows 6–12.

### 6.4 P3 Magnitude marks

SYS-39 — Bar, for a single figure (LIST-R §9 P3; D31):
- linear scale from 0 to the list's maximum, everywhere (D31: "cost bars linear everywhere"). For a paged list the maximum is over all in-scope rows (API-12 `stats.max_cost_usd`, D45), so bars do not rescale when a later page loads;
- right-anchored against its figure, in a separate track left of the figure (96 px in the explorer list, 64 px elsewhere). No figure sits on the bar;
- 10 px tall, radius 2; cost bars in `--list-bar`;
- no minimum: a bar under 1 px is not drawn.

SYS-40 — Activity strip, for something that spans time (session, turn, agent) (LIST-R §3, §9 P3; D32):
- length ∝ log10(`wall_ms`): 1 min (and below) = 16 px; 48 h (and above) = the full strip width; linear in log10 between. The length comes from `wall_ms` (D32), never from `last_ms − first_ms` (D38 M9);
- the strip is divided into equal activity bins along its length: the DM-44 bins (which cover first→last record, resume gaps included) are scaled into this length (D38 M9). Each bin's bar height = log(1 + tokens in the bin) / log(1 + the largest bin among **all in-scope rows**, D38 L7), minimum 2 px for any non-empty bin, empty bins draw nothing;
- bars ink2, ink1 on row hover; a 1 px `--color-rule` baseline under the full length; a 1.5 px ink3 tick at each local midnight the span crosses, placed proportionally on the first→last span (D38 M9);
- size 200×28 px in the explorer list (§r3 default), 248×28 px where the slot is 248, 88×16 px in parent blades (rail included);
- an item with no time span (one model call, one command) uses a bar (SYS-39) against duration or tokens instead.
Data: per-item activity bins (tokens per bin, bin count or bin width) from the backend (`activity_bins`, needs S2/S3).

### 6.5 P4 Model mark

SYS-41 — A model mark is a 10 px shape, a 4 px gap, then the short name in 14/400 mono ink2 (SESS-R §7 P4; CONSOLIDATION-NOTES). Shapes: ◆ opus, ● sonnet, ▲ haiku, ■ fable, ⬡ gpt, small ● other. The shape is ink2 (one colour; no family hue, CONSOLIDATION-NOTES). Compact form: the shape only, with the full model id in the tooltip. Short names: `opus 5.5`, `sonnet 5`, `haiku 4.5`, `fable 5.1`, `gpt 5.4`. Family is matched by model-name prefix; anything else (e.g. `deepseek`, `mimo`, `codex-*`) is "other" (LIST-R §r3 "Not built"). The mark is off in the explorer list (D32).

### 6.6 Stat strip

SYS-42 — A blade's stat strip is one display figure (cost, 30/500 money, tabular), then one 14 px line of `icon + mono figure + word` items in a fixed order: tokens · active of wall · agents · skill loads · errors (errors only when > 0) (SESS-R §7 "Stat strip"). Captions are 14 px sentence case ink3. A per-model line under it, when present, is a run of P4 marks followed by `calls · cost · model time · tok/s`. The explorer header uses its own 20 px totals line (EXP-R final design; SYS-R §5 accepted "Header totals at 20/600"), specified in `explorer.md`.

### 6.7 Tooltips and popovers

SYS-43 — Tooltips: l4 surface, `--shadow-pop`, `--radius-control`, 8×10 px padding, 14 px text, figures tabular; shown on hover after the pointer rests and on keyboard focus; never contain the only copy of information needed to act, except the per-day breakdown and the weekday of a chart column (OVR-R §4).

SYS-44 — Popovers (period, filter, list settings): l3 surface, `--shadow-pop`, `--radius-card`. Section heads 14/600 sentence case (SYS-R §5 #25). Open on click or key; close on Esc, on outside click, or after a choice that ends the task (a preset). On close, focus returns to the control that opened it.

## 7. Blade navigation

SYS-45 — Stack. Blade 0 is the explorer. Opening an item adds a blade to the right; opening an item from a blade that is not the rightmost first closes every blade to its right, then adds the new one (D29, D33; core.js `openSession`, `openDetail`).

SYS-46 — Strip widths (the maintainer 2026-10-12, replacing the prototype's rail-and-spine layout). On a stage of width W with 12 px gaps:
- 1 blade: the explorer takes W;
- 2 or more blades: every parent blade is 400 px (the standard width; the explorer's rail form is this width); the newest blade takes the rest of the visible width, minimum 640 px;
- when the blades no longer fit, the strip scrolls so the newest blade is fully visible at the right; older blades move off-screen to the left at their standard width. No blade is ever collapsed or shown as a spine.
Verify at 1440×900: list → session gives 400 + 12 + rest; list → session → turn shows the session (400) and turn (rest, ≥ 640) with the list off-screen left, reachable by scrolling left.

SYS-47 — Reaching older blades (the maintainer 2026-10-12): by horizontal scroll of the strip (trackpad, shift+wheel, scrollbar), by a breadcrumb crumb (SYS-49), by ←/→ (moves focus one blade and scrolls it into view), and by Esc (closes the newest blade and slides the strip back). Scrolling alone never closes a blade.

SYS-48 — Focus. Exactly one blade is focused. It carries a 1 px `--color-struct-line` frame and `--shadow-pane-focus`; others carry `--shadow-pane` and no frame (REVIEW-v4-detail §2.11; REVIEW-v4-turn-r2). A pointer press inside a blade focuses it. Opening an item from a parent blade closes every blade to its right first (SYS-45).

SYS-49 — Breadcrumb (top bar). At blade 0 alone it reads "Sessions", 14/500 ink1, no box. With blades open: `Sessions › <session name> › <turn label> › <detail label>`; separators `chevron-right` 14 px ink3; ancestors ink3 (ink1 and underline on hover, struct on focus); the last crumb ink1 500, never amber; no pill, no border. Each crumb truncates at 44 characters with the full text in `title`. Clicking a crumb closes every blade to its right (EXP-R "Breadcrumb"; SYS-R appendix).

SYS-50 — Keys (global; EXP-R "Focus, hover, keyboard"; D33; core.js `keydown`). Keys other than ⌘K/Ctrl+K do nothing while a text field has focus, except Esc.

| Key | Effect |
|---|---|
| ⌘K / Ctrl+K | open the palette; again closes it |
| `/` | focus the search field (the rail's search when the rail is showing) |
| `p` | open the period popover |
| `f` | open the filter popover |
| `j` / ↓, `k` / ↑ | move the cursor in the focused blade (explorer: `explorer.md` EXP-54, EXP-63; session and turn: S1b) |
| Enter | open the item under the cursor in the next blade |
| ← / → | move focus to the blade on the left / right |
| Esc | close, in this order, the first that applies: the palette; an open popover; blur a focused text field; the rightmost blade (when more than one blade is open); on the explorer alone, clear the project filter (explorer.md EXP-46) |
| `s`, `b` | step cursor to the next skill load / the biggest step (D33; S1b owns) |

Key hints appear in tooltips, not as visible chrome, except in the palette footer and on the finder chips, which keep their key caps (`session.md`; D37).

## 8. States, motion, readability

### 8.1 Focus and hover

SYS-51 — Every interactive element shows a 2 px struct outline with a 2 px offset on `:focus-visible` (rows: −2 px offset inside the row). The ring shows for keyboard focus only, not after a mouse click. At most one focus ring and one focus glow are visible at a time (SYS-R §3; EXP-R "Focus").

SYS-52 — Pointer hover on rows uses l3; on text buttons, struct text; on icon buttons, l3 fill and struct icon. Hover is never the only way to reach information needed to act, except as stated in SYS-43.

SYS-53 — Selection (persistent) uses struct-soft fill plus struct text on the selected item. Other items in the same set switch their text to ink3 (not opacity) when a selection narrows the set. Graphics (chart segments, slices) may dim by opacity (OVR-R top 5 #5; §r3).

### 8.2 Loading, empty, error

SYS-54 — Loading: skeleton rows of the row's height with l2 fills; figures show skeletons, never zeros. The skeleton has a slow pulse, which is off under reduced motion. No "Loading…" text (EXP-R "Empty and no-result states"; ui-refine §3.6).

SYS-55 — Empty: one 14 px ink2 sentence (plus a 24 px ink3 glyph and a struct action when one exists), centred in the region. No empty chart axes or column heads above an empty list (EXP-R).

SYS-56 — Error (a request failed): an l2 card with `circle-alert` 16 ink1 (not error-red: this is a load failure, not a data fact), title 14/500 ink1 "Couldn't load <noun>", the server's `error` message in mono ink2, and a "Retry" control (ui-refine §3.6). Retry repeats the request.

SYS-57 — No store (D22): when `/api/ingest/status` returns `store_exists: false`, or any data route returns 503, a full-width banner sits under the top bar: l2, `database` 16 ink2, "No Botsworth store yet." ink1, "Run" ink2, the code chip `botsworth ingest` with a copy button, "then reload." Neutral colours only (ui-refine §3.6). Each data region shows its empty state. A store at schema v6 shows the same banner with the text "Store needs a rebuild." and the `botsworth ingest` chip (D37).

SYS-57a — Ingest running (D37): while an ingest runs, a full-width neutral banner (same look as SYS-57) reads "Ingest running — figures may be partial". The UI does not refetch when the ingest finishes (D38 H3): the status poll updates only the freshness text and this banner, and the banner clears on the first `/api/ingest/status` response with `ingest.running = false`. A full rebuild writes a new store and swaps it in on success, so the UI never reads dropped tables.
  Data: an ingest-running flag on `/api/ingest/status` (S3).

### 8.3 Motion

SYS-58 — Durations and easings (core.css; ui-refine §3.7):
- blades: one push transition for every path (open, close, crumb, Esc, Back): the strip slides and blades resize together in 280 ms, `cubic-bezier(.22,1,.36,1)`; the explorer cross-fades between its full and rail forms within the same 280 ms (the maintainer 2026-10-12);
- popovers and the palette: 200 ms `cubic-bezier(.2,.9,.25,1.04)` for transform, 140 ms opacity;
- colour and background changes 150 ms `--ease-standard` `cubic-bezier(.22,.61,.36,1)`; chevron rotation 150 ms;
- chart bars animate height 520 ms on data change.
Motion explains a change; no motion loops.

SYS-59 — Under `prefers-reduced-motion: reduce`, all transitions and animations are off: blades and the strip move to their final layout at once, and skeletons do not pulse.
Verify: with reduced motion set, opening a session changes the layout in a single frame.

### 8.4 Readability floors

SYS-60 — Text is 14 px or larger. 12 px is allowed only for SYS-15's label uses, including key caps and the TRN-31 "final" tag (D31 exception, D38 L3; refine-ui §4).

SYS-61 — Contrast (WCAG 2.2 AA, measured with `refine-ui/scripts/check_contrast.py`): text ≥ 4.5:1 against its actual background in every state (rest, hover l3, selected struct-soft, l2 bands); essential graphics and state cues ≥ 3:1. Measured passes for reference: ink3 #838ba3 on l1/l2/l3/l4 = 5.38/5.13/4.80/4.55; money on l1 8.48; struct on struct-soft 9.26; error on l1 6.59; `--list-bar` on l1 5.20 (SYS-R §2; LIST-R §6). Decorative context (night bands, day lines, baselines) may be below 3:1 when the same information is also given as text (LIST-R §6).

SYS-62 — Text is never dimmed by opacity: ink1 at 45 % opacity on l1 is about 4.0:1 and fails. Dim text by switching to ink3 (OVR-R top 5 #5).

## 9. Resolved questions

| # | Question | Ruling | Applied in |
|---|---|---|---|
| Q1 | Ranking window for project colour slots | Cost over the last 30 days, recomputed at ingest (Kai 2026-10-12) | SYS-11 |
| Q2 | Mark for a lower-bound cost | `$12.40+` with the tooltip "Excludes N calls to unpriced models" (Kai 2026-10-12) | SYS-32 |
| Q3 | Model-ramp colours for charts stacked by model | The ramp is `session.md` SES-22 (D37) | SYS-12 |
| D37 | Title levels, new glyphs, finder key caps, ingest and v6 banners | Applied as ruled (D37) | SYS-28, SYS-30, SYS-50, SYS-57, SYS-57a |
| B1 | Blade navigation model | Azure-style continuous blades (Miller columns): parent blades at one 400 px width, newest takes the rest (min 640 px), older blades scroll off left; no spines, no collapse; one 280 ms push transition, instant under reduced motion (the maintainer 2026-10-12) | SYS-46–48, SYS-58, explorer EXP-60, EXP-66 |
| Q4 | The prototype token comment allows 12 px uppercase captions, which SYS-15 forbids | Update the comment in the frontend `theme.css` when the tokens move (Kai 2026-10-12) | SYS-15 |

## 10. Not covered here
- Page layouts and behaviour: `explorer.md`, `session.md`, `turn.md`, `detail.md`.
- The API and field names: `data-model.md` (S2), `interfaces.md` (S3).
