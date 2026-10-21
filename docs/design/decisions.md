# Botsworth — Decision records

This file has two numbered sets, and their numbers overlap for D1–D16:

1. **Design records D1–D16** (first part below): the ADR-style records from the
   design phase, 2026-10-12.
2. **Maintainer log D1–D53** (second part): the maintainer's requirements and the
   build-time rulings, in the order they were made. Design records cite its early
   entries as "the maintainer Dn". Everywhere else in the repo, D17 and higher
   refer to this log; D15–D16 next to skills or limits also refer to it.

Log entries name who decided ("maintainer" or "lead", the lead being the agent
that coordinated the build). Codes in parentheses (Q, T, B, F, M, H, L) label
working-note items that are not in this repo; spec ids (DM-, QRY-, API-, CLI-,
SYS-, EXP-, SES-, TRN-, DET-, FE-, TST-) refer to `docs/specs/`.

# Part 1 — Design records

## D1 — One tree, the attribution tree; time carried per op

Context: kickoff §7.1 asks for an operation hierarchy and possibly separate
causal links for async work; §3 forbids summing concurrent child durations
into wall time. A sub-agent starts inside one tool call and finishes long
after it (async launch, A3 §4).
Decision: the only hierarchy is the attribution tree (where tokens and cost
roll up). Every op carries its own `start/end`; container time is derived
by span and interval union, never inherited or summed.
Options: (a) time-containment tree with causal side links — children would
sit under the session, and attributing their cost to the turn that spawned
them would need a second rollup path; (b) two trees — double counting risk
on every §8 query; (c) chosen.
Consequences: a tool call's `wall_ms` includes its spawned agent's life;
the UI must show `dur_ms` (own) and `wall_ms` (subtree) as different
numbers. Re-messaged agents attribute per message (D3).

## D2 — Only leaves own metrics

Context: the prototype's "step disjointness is assumed, not enforced" and
"nested-fork double count" items (todo.md N11, F1-fork) are both cases of a
container carrying its own metric while children also carry theirs.
Decision: `llm_call` and `tool_call` own metrics; `session`, `engagement`,
`turn` own nothing; inclusive = Σ subtree. The schema makes it checkable
(`approaches.md` rollup consistency test).
Options: containers own "unattributed" residue — rejected; residue is
either a leaf we failed to create or a provider ledger difference (S39),
both reported, not stored as a metric.
Consequences: compaction/resume cannot be ops (D8); tool-call token
attribution must be a leaf estimate when added (D10).

## D3 — Engagement is the node; agent is an attribute

Context: one Claude Code agent was messaged 25 times in a real session
(prototype R11); cost must attribute to the turn that sent each message
(decision 20260730). Codex `collab_resume`, omp `hub` show the same pattern.
Decision: each burst caused by one tool call is an `engagement` op under
that tool call; `agent_id` groups engagements of one transcript for the
category view.
Options: whole agent under its spawn call — loses per-message attribution;
whole agent under the session — loses spawn attribution entirely.
Consequences: an extra tree level; "what did agent X cost" is a `group_by
agent_id` query, not a node.

## D4 — Provenance enum per metric group

Context: kickoff §9; A3/A4/A5 availability differs per provider; Codex old
files have no call id.
Decision: `measured | derived | estimated | missing` on tokens, time, cost
(cost uses `cost_basis`), and `natural | derived` on identity. Inclusive
provenance = weakest in subtree; `cost_lower_bound` flag for unpriced leaves.
Options: a single boolean "complete" — hides which lens is weak; per-field
columns for every metric — too wide; chosen groups match the lenses.
Consequences: every API row carries `prov`; UI renders missing as missing.

## D5 — Cost policy

Context: omp reports cost, Claude Code only a session ledger, Codex nothing;
some credentials are subscriptions; the prototype learned that substring
model matching mispriced by 3× and that unknown models must not borrow a
rate.
Decision (revised per the maintainer D6/D8/D9): `reported` when the provider gives
per-call cost (omp); else `computed` from the models.dev snapshot (D16)
with explicit aliases and 5m/1h cache tiers — subscription credentials
included, since the API-equivalent figure is what D8 asks for and `account`
records which plan paid; else `missing` (null, excluded from sums,
ancestors `cost_lower_bound`). Ledger values are checks, never adjustments.
Options: always recompute (overrides omp's provider figures — loses
information); never compute for subscriptions (loses the cross-provider
lens).
Consequences: price table is user-maintained data; price bump re-prices
computed ops only.

## D6 — Call identity and corpus-wide dedup

Context: F1, F5, F6 in `design-model.md`.
Decision: per-provider rules in `design.md` §4.4; corpus-wide unique
`(provider, kind, natural key)` for leaves; later copies `duplicate_of`.
Options: per-session dedup only — overcounts F5 queries by the duplicate
count; drop duplicates silently — hides forks from the session view.
Consequences: the first-ingested file owns the cost; ingestion order
affects which session shows it as own (surface `n_duplicates`); Q7 asks
the maintainer to confirm.

## D7 — Bundle is the ingest unit; atomic replace

Context: sub-agent files appear after the main file; files are live; omp
rewrites line 1 in place; interrupted ingest must not leave half a session.
Decision: discover → group into bundles → detect change by (size, mtime,
tail hash) → re-read whole bundle → replace rows in one transaction.
Options: per-file append-only replay — faster, but correctness then depends
on file-order assumptions across files; left as a spike optimization.
Consequences: refresh cost is proportional to the changed bundles' size,
not the appended bytes; the largest bundle (71 MB + 48 MB) sets the worst
case (prototype: 3.8 s for 71 MB).

## D8 — Markers, not ops

Context: compaction, resume, rollback and model change have timestamps but
no cost or duration; making them ops would violate D2 or force zero-metric
leaves into rankings.
Decision: a `markers` table attached to the enclosing op.
Consequences: timeline shows them as ticks; queries never rank them.

## D11 — Pointers, not stored text

Context: the maintainer D4; the prototype's `blob://` scheme was positional (scope,
record index, JSON path) and re-read JSONL on demand; record indexes depend
on parser filtering and a store rebuild changes `scope`.
Decision: `ptr://<file_id>/<L line | B start+len>[#json_pointer][?h=hash]`
(design.md §4.9). Explicit locator, content hash for drift, file id
independent of the store, no inline threshold.
Options: keep `blob://` as is — breaks across store rebuilds and cannot
detect omp's in-place rewrite; store text in the DB — violates D4 and
doubles disk.
Consequences: one resolver; drift is visible; duplicates read through the
owner's pointer.

## D12 — Project identity

Context: the maintainer D10 after the lossy encoded-dir question.
Decision: project = the session's first `cwd`, applied to every op; the
per-record `cwd` is kept in its own column; no git or worktree folding.
Consequences: `/tmp` worktrees are their own projects; a later alias table
can fold them without re-ingest.

## D13 — Steps layer and action taxonomy

Context: the maintainer R-steps — a turn must read as what the agent did, cheaply.
Decision: deterministic steps per turn (verb, target, outcome, label ≤ 120,
snippet ≤ 160) built from content parts, referencing the owning ops; no LLM;
cost never on steps.
Options: LLM summaries at ingest — cost and latency; render from raw records
at view time — needs the file for every list, defeats D4's "basic view info
in the store".
Consequences: row count grows ~2–3× tool calls; spikes measure it.

## D14 — Account dimension and limit facts

Context: subscription windows belong to the billing account; Codex
`used_percent` already includes omp's usage on the same ChatGPT plan.
Decision: `account` on sessions, calls and facts; `limit_snapshot` and
`limit_hit` facts; windows computed per account at query time (exact for
Codex, anchored/estimated for Claude).
Consequences: Codex CLI and omp usage union correctly; Claude windows are
labelled estimated until a hit anchors them (Q8).

## D15 — Explicit ingest, visible freshness

Context: the maintainer D5.
Decision: `botsworth ingest` only; queries never refresh; every response
carries `freshness` (last ingest, parser version, price fingerprint).
Consequences: a hook or the user owns refresh cadence; stale answers are
visible, never silent.

## D16 — Pricing from models.dev with a fingerprint

Context: the maintainer D6; 5,405 Codex calls have no catalog price.
Decision: dated local snapshot via proj-c's integration; explicit
aliases (D11 list); `cost_basis ∈ reported | computed | missing`; `missing`
is null and excluded, ancestors lower-bound; fingerprint change re-prices
without re-reading files.

## D9 — CLI with JSON and Python API for agents; MCP later

Context: F6 requires efficient structured access; MCP tool definitions cost
tokens in every session; a CLI is testable and already how agents here run
tools.
Decision (the maintainer, 2026-10-12): one query layer, the importable Python
module `botsworth.query`; the `botsworth` CLI with JSON output is argument
parsing over it; the HTTP API for the UI wraps the same functions; no MCP
for now (later: a wrapper over the same five functions).
Consequences: an agent needs shell or Python access; the §5 JSON is the
stable contract for all three surfaces; spikes benchmark through the
Python API so CLI/HTTP overhead is excluded from the numbers.

## D10 — Tool-call token attribution deferred

Context: the prototype's emitting/ingestion split is estimated (bytes/4,
image patch formula) and needed two review rounds to get right.
Decision: V1 tool calls own `dur_ms` and `result_bytes`; tokens stay on the
`llm_call`. Re-entry: a user asks which tool result was expensive to
ingest. When added, it is an `estimated` own metric on the leaf, consistent
with D2.

# Part 2 — Maintainer log

Restated from the working notes, with private details removed. Store paths and
names use today's names (`botsworth`, `BOTSWORTH_DB`); during the build the
tool was called "profiler".

## D1 — Project identity = agent working directory (maintainer, 2026-10-12)
Chosen: the directory the agent ran in, grouped as each agent groups its own
sessions; the key is the raw cwd, not an encoded folder name. Considered: git
repository root with worktrees folded. Reversal: add a mapping layer on top of
cwd if per-repo statistics across worktrees are needed.

## D2 — Lightweight run setup for the design spike (lead, 2026-10-12)
Process only: a one-hour exploratory spike with a single designer agent; the
lead holds final judgment.

## D3 — Agent interface = CLI (JSON) + importable Python API (maintainer, 2026-10-12)
Chosen: a CLI with JSON output and a Python API, both over one query layer;
HTTP serves the UI. No MCP server for now. Reversal: add MCP as a thin
wrapper if an MCP-only client needs it.

## D4 — Store metadata only; content via pointers into source files (maintainer, 2026-10-12)
The database holds metadata, metrics and basic view information. Full prompts,
responses and tool output stay in the agents' session files and are reached
through a pointer URI. Limitation: content is lost when the agent deletes old
session files.

## D5 — Ingest only on explicit `ingest` (maintainer, 2026-10-12)
Considered: refresh before each query; a background watcher. Chosen: an
explicit command, run by hand or from a hook. Incremental (new or grown files)
with cross-file dedup. Queries report the last ingest time.

## D6 — Pricing source = models.dev catalogue (maintainer, 2026-10-12)
Computed costs use models.dev prices. Provider-reported cost (omp) stays
`reported`. Models not in the catalogue stay cost-missing.

## D7 — Benchmark statistics (maintainer, 2026-10-12)
Per project and time window: per-session distributions (median, p90, max) of
cost, tokens by bucket, wall time, model versus tool time; the same by agent
type, model and tool; outliers against the project p90 with the operation
that drove them; commands repeated across sessions.

Also requested at this point: trends over time ("sessions getting slower",
"limits running out") and skill usage analytics (is a skill used, how often,
what it costs). The skill and limit scope was later narrowed by D15 and D16.

## D8 — "Limits" = subscription usage windows (maintainer, 2026-10-12)
Claude 5-hour and weekly windows and ChatGPT plan limits, shown as tokens and
API-equivalent cost per window, plus rate-limit and retry events. Remaining
quota is not in the source data. Narrowed by D16.

## D9 — Codex model names map to OpenAI entries in models.dev; unmatched → no cost (maintainer, 2026-10-12)
No borrowing of a base model's price. Unmatched calls: cost missing, excluded
from cost totals, tokens still counted.

## D10 — Project = the session's first cwd; per-record cwd kept (lead, applying D1)
About one Claude session in six changes cwd mid-session. A per-record project
would split sessions across projects.

## D11 — Codex alias table under the openai provider only (lead, applying D9)
gpt-5-codex → gpt-5, gpt-5.1-codex → gpt-5.1, gpt-5.2-codex → gpt-5.2.
Names with no openai base (gpt-5.1-codex-max, gpt-5.1-codex-mini,
codex-auto-review) stay cost-missing. Aliased calls record `price_alias`.

R-steps — readable steps in each turn (maintainer, 2026-10-12): a turn is a
user ask, the model and tool exchanges, and the answer. Inside it, ordered
display steps (thought, said, ran, read, wrote, edited, searched, fetched,
spawned agent, used skill, answer), built deterministically from tool
arguments with no LLM at ingest; snippets stored, full text by pointer. Run
labels use the agent's own description of the command before falling back to
the command head. Deferred: splitting compound shell commands.

## D12 — Designer open questions closed (lead, following D4, D8)
Subscription calls get list-price cost ("API-equivalent"). omp auxiliary model
usage is included. Tool-call token attribution is deferred. Deleted source
files: ops are kept and marked `source_missing`. Cross-file duplicates: the
first owner keeps the cost; copies are flagged `duplicate_of`.

## D13 — Window estimation and skill attribution (lead; both dropped for V1)
(a) Claude windows estimated from first calls and corrected by limit-hit reset
times — dropped for V1 by D16. (b) Ops after a skill invocation attributed to
the skill until the turn ends — deferred by D15.

## D14 — Cycle rule for ping-pong messaging (designer, accepted by lead)
A reply engagement whose causing tool call has an ancestor engagement of the
same agent is parented under that agent's own spawn call; `caused_by` is kept
with basis `<basis>+cycle`. Bounds the tree depth.

## D15 — V1 skills = searchable invocation events only (maintainer, 2026-10-12)
Agents can query skill-invocation events: sessions that used skill X, when, and
the steps around each invocation. No skill cost or time attribution in V1.

## D16 — No limit-window tracking in V1 (maintainer, 2026-10-12)
"Limits burning fast" means sessions using too many tokens or too much cost.
V1 shows token and cost trends by model, project and session, with drill-down
to the steps and calls that used the most. No 5-hour or weekly window
modelling. Codex limit records are kept as raw events only.

## D17 — `command_norm` (full, normalized) + `command_key` (grouping) (lead, 2026-10-12)
`command_norm` = the full command, normalized (wrapper stripped, leading `cd`
dropped, paths relative). `command_key` = program plus subcommand head with
launcher prefixes (`uv run pytest`). Repeated-command and slow-test queries
group by `command_key` and drill into `command_norm`. One shared
implementation for all adapters.

## D18 — Depth ceiling 32 (lead, 2026-10-12)
Each nested agent adds 4 levels (tool_call → engagement → turn → llm_call), so
depth = 4k + 3 for k nested agents. The deepest real session seen had k = 4
(depth 19). Ceiling 32 is a runaway guard; exceeding it fails the bundle
loudly. Adapters emit `occ_key` (no session id) for store-side copy detection.

## D19 — Resume segments: adapters emit, store unions (lead, 2026-10-12)
Containers carry per-file segments split at resume events; the store computes
wall time as the union across session and engagement files; resume gaps are
idle. A disposal `session_exit` never extends spans.

## D20 — Store location (lead, 2026-10-12; amended)
Precedence: CLI `--db PATH` > `configure(db=)` > env `BOTSWORTH_DB` > env
`BOTSWORTH_HOME` (a folder) > the platform app-data folder. One resolver,
`store.location.resolve_db()`; no caller re-implements it. `ingest` reads the
provider roots by default (`~/.claude/projects`, `~/.codex`, `~/.omp`).

## D21 — Query conventions (lead, 2026-10-12)
An explicit `db=` on a query function (path or connection) outranks D20's
order. The store keeps own and inclusive columns (`tok_total`,
`incl_tok_total`); the wire `Op.tok_total` is inclusive. Display ids are
session-anchored (`<session>:T12.3`), bare only in scoped calls; `E<k>` for
orphan engagements; `/<t>[.<s>]` for nesting; 1-based.

## D22 — Missing store behaviour (lead, 2026-10-12)
`/api/ingest/status` always returns 200, with `store_exists: false` and
`last_ingest_ms: null` when there is no store. Other data routes return 503
"no store — run 'botsworth ingest'"; the CLI exits 2. The UI shows an
empty-state banner.

## D23 — Ownership order uses `first_line_ms` (lead)
A resume or branch copy carries the original's records, so `start_ms` ties
between owner candidates. `Op.first_line_ms` (session only: the first
timestamped record physically in the session's main file) breaks the tie.
Owners rank by `first_line_ms`, then `start_ms`, then bundle id.

## D24 — `busy_sum` from intervals (lead)
Provider-measured durations (omp `duration`, Claude `durationMs`) are shorter
than the record span, so Σ dur_ms could be less than the interval union. The
store computes `busy_sum_ms` from leaf intervals (end − start), so busy ≥
active holds by construction. Adapter `dur_ms` is kept as an informational
provider figure.

## D25 — Agent model choice (maintainer, 2026-10-12)
Process only: which model ran the build's design agents. No product effect.

## D26 — `source_files` and ingest status semantics (lead)
`source_files.path` is absolute for all providers (`src://` pointers stay
root-relative). A file is `live` when its bundle had a partial trailing line or
its mtime is within 10 minutes of the run start, else `settled`;
`source_missing` rows survive reloads. The ingest lock covers the whole run.
Status `files` counts provider source files per provider; `bundles` counts
bundles. `PARSER_VERSION` is one constant, bumped when any adapter's canonical
output changes.

## D27 — Model per agent role (maintainer, 2026-10-12)
Process only: which model tier the build used for design, implementation and
checks. No product effect.

## D28 — Question-first UI; no quota windows (maintainer, 2026-10-12)
"Quota" means usage: tokens and API-price cost over a period the user picks;
no 5-hour windows. The UI is designed around the user's questions. Acceptance
cases: (1) one unusually expensive day — why; (2) yesterday's longest
sessions — why.

## D29 — Explorer-first layout; no analysis in v1 (maintainer, 2026-10-12)
Layout: period chart, filters and session list on one page, with the session
page as a drill-down. No "why it stands out" chips and no generated summary
sentences in v1 — facts only. Drill-downs use blade navigation (each level
slides in from the right).

## D30 — Rows list (maintainer, 2026-10-12)
The session list is rows, not tiles (tiles were hard to read). Filters use
progressive disclosure; no coloured left border on rows; projects stay
prominent.

## D31 — UI v4 system rules (lead, 2026-10-12)
- Icons: Lucide at 16 px, stroke 1.5, in ink3, plus three custom provider marks.
- Hierarchy comes from weight and ink, not new sizes. The 4-size scale stays;
  captions are 14 px sentence case; 12 px only for chart numerals.
- Colours: ink3 #838ba3; `--color-error` #ff6b6b on the alert icon and its text
  only. Edged controls stay on l1 or l2.
- Money always has 2 decimals with tabular figures; cost bars are linear.
- Models are marked by glyph shape plus name, never by project hues; charts
  stacked by model use their own ramp.
- One look per fact across all views; shared patterns for the row, group
  header, magnitude mark, stat strip and model mark.

## D32 — Explorer: overview and list (maintainer, 2026-10-12)
- A day chart (stacked days; out-of-range days dimmed) with a project donut and
  a ranked list beside it. Every project is nameable and works as a filter.
- Day bars split each session's cost by activity day. Rows, group headers, the
  donut and the header total use the full cost of in-scope sessions.
- The session list is flat by default with a global sort. Optional grouping
  (none, project, day) sorts within groups and orders groups by the same
  measure.
- The project shows inline as its name in its hue. No left border. Start time
  is the leading column, with the day shown when it changes.
- Activity strip: length is log(wall), bar height on a log scale, 28 px tall.
- No error marks and no model mark in list rows.
- Automated = no human ask in any turn; hidden by default.

## D33 — Session, turn, detail: three drill-down blades (maintainer, 2026-10-12)
- Session blade: header plus the ordered turn list, with time gaps made clear.
  Model shown on turns and agents.
- Turn blade: Ledger and Waterfall views over one row list. A brushable
  histogram, shown only for turns with agents or wall ≥ 30 min.
- Detail blade for a model call, tool, agent or message, sliding in from the
  right.
- Step cursor: j/k continues across turns, s jumps to skill loads, b to the
  biggest step.

## D34 — Data rules for display (lead and maintainer, 2026-10-12)
- Session title: user rename (Claude custom title, Codex thread name) >
  generated title (Claude ai-title, omp title) > first human ask with ≥ 4
  words that is not boilerplate > "project · id8".
- Harness records (`/clear`, "(no text)", interrupts) fold away and are not
  numbered turns.
- Plugin and agent messages get their own kind.
- Idle time is no-activity stretches split at turn boundaries, not "waiting
  for you". (Amended by D54: a recorded user-wait call is waiting
  on you.)
- Turn cost = its llm_calls plus agents linked by `caused_by` from Agent and
  SendMessage calls.
- Skill load = verb `skill` plus target (a Codex shell read of SKILL.md counts).

## D35 — One CLI for everything (maintainer, 2026-10-12)
A single `botsworth` CLI: `ingest` (and data management), `serve` (HTTP API
and UI), and agent query commands (JSON by default, `--table` for humans). No
GraphQL in v1: `POST /api/query` covers flexible queries.

## D36 — Continuous blades, no spines (maintainer, 2026-10-12)
Miller-column blades: each parent blade compresses to one standard width
(~400 px); the newest takes the rest (min ~640 px); deeper levels slide the
strip left and older blades move off-screen, reachable by horizontal scroll,
the breadcrumb, ←/→ and Esc. One push transition for every path. Replaces the
spine collapse in D33.

## D37 — Spec cross-rulings (lead, 2026-10-12)
- Stretches: every stretch ≥ 60 s is stored with a `state` (`none` or
  `agents`); `idle_stretches` is renamed `stretches`. Gap rows, `w` and the
  "agents" legend jump use them. The gap detail lists the agents that ran in an
  `agents` stretch.
- Turn Wall, Waiting and the turn split use the turn span; the wait before the
  next message is a gap row. DM-51's `[start, next start)` figures are for
  session sums only.
- Time zone: one zone, the store zone (superseded by D50).
- Names: four `title_source` values (rename, generated, first_ask, fallback).
- Ingest: a full rebuild writes a new store file and swaps it in on success,
  so the UI never reads dropped tables. During any ingest the UI shows a
  neutral banner "Ingest running — figures may be partial"; no automatic
  refetch when it finishes. An outdated store shows "Store needs a rebuild."
- Indexes on sessions(op_rowid), ops(parent_rowid, kind, start_ms),
  ops(caused_by); ANALYZE after each ingest. Membership in a period requires a
  day with at least one llm_call.
- The turn data includes the next turn's origin and label and the end time of
  each agent run the turn caused (Waterfall tails).
- Project rules: the FALLBACK-OK and layer-boundary conventions in CLAUDE.md
  apply.

## D38 — Cross-check rulings (lead, 2026-10-12)
- H1: a turn's span is the turn op's stored [start_ms, end_ms], not widened
  over caused engagements (about one in six end later; they become Waterfall
  tails).
- H2 (amends D37): every legend jump goes to the longest row or stretch of that
  state (SES-27, QRY-29; ≥ 2 min for wait states).
- H3: no automatic refetch. The status poll updates only the freshness text and
  the ingest banner, which clears on the first status with `ingest.running =
  false`. The store-change cache clear (FE-7) applies only to requests the user
  causes.
- M1: data-model.md §2.2 is the only DDL source; `sessions.op` is UNIQUE.
- M2: the `find` default limit is 2000.
- M3: the turn Waterfall folds waits ≥ 60 s, matching stored stretches.
- M4: one hit set per finder, shared by the chip, per-turn marks, total and
  key. Waits have a 2-min floor. Agents and failures count at any depth.
- M5: `first_day` = the first local day with an llm_call. Palette project rows
  gain `n_automated`.
- M6: DayBar gains `cost_lower_bound` and `n_unpriced`; day figures show `+`.
- M7: acceptance figures for the expensive-day case use `show_automated = true`.
- M8: each secondary fetch shows skeleton rows and the error card on failure.
  Agent rows fold at 500 with "Show N more".
- M9: the activity strip's length comes from `wall_ms` (D32); bins are scaled
  into it.
- M10: periods over 92 days draw week columns.
- M11: QRY-41 recomputes the attached turn (SES-37). `before_first` does not
  count toward the longest `you` wait.
- M12: `runs` and `run_ms` are per agent row, that is per causing call.
- L1–L9: minor spec fixes, including 12 px allowed for key caps and the
  "final" tag (D31 exception).

## D39 — Content references and minimal storage (lead, 2026-10-12)
- One content-reference scheme:
  `src://<provider>/<relpath>#<locator>[/<json.path>][?h=<hash>]`. Each
  provider resolves it against its own root and record format. It is the only
  way to reach long content.
- Never stored: full prompts, thinking, tool input and output, agent prompts.
  Stored as previews capped at 160 chars: label, step snippet and summary, the
  start of the ask, file path, command preview.
- References are stored compactly and the URI is built at the API boundary.
  Repeated strings are interned where measurement shows a gain.
- Target: the new store for a full corpus is at most half the size of the
  previous schema's. No listing page needs more than one data request.

## D40 — Time gaps as gap rows (maintainer, 2026-10-12)
Gap rows (SES-36) chosen over the other prototype options. The continuous
blades (D36) are accepted as built.

## D41 — Re-check rulings (lead, 2026-10-12)
- M8: when a finder request fails, its chip shows `circle-alert` and no count;
  tooltip "Couldn't load. Click to retry"; a click retries.
- M10: QRY-25/API-11 take `bucket=week` for periods over 92 days and return
  week rows (local Mondays). Partial edge weeks are clipped to the period and
  the tooltip names the dates covered.
- M11: `before_first` is its own label and never folds into `you`. In SES-18
  it is not in the `you` total; the legend and SES-27 count it under `none`.
- M12: DET-31 counts runs per causing call, as DM-36 does.
- N1: the turn axis runs from `start_ms` to max(`end_ms`, the latest agent
  run's end). The part after `end_ms` is a tail zone labelled "after the
  turn". Turn Wall and Waiting stay span-based. The histogram covers the whole
  axis, so its total equals the turn cost.
- N2: EXP-72 and DM-61 tests require `n_sessions > 0`.

## D42 — Architecture rulings, query side (lead, 2026-10-12)
- Tasks by tokens (CLI-20–24): a model call's tokens and cost split evenly
  across the tool calls it issued (`issuing_call_split`), named in a `basis`
  field so groups add up to the total. A call with no tool call counts under
  "no tool".
- Commands: the wire `command` is the stored 160-char preview; `command_src`
  references the full command, which the tool detail loads. `find` matches
  the preview only.
- The ingest writes its pid into `<db>.lock` (QRY-40).

## D43 — Architecture rulings, store side (lead, 2026-10-12)
- Content refs are stored as (`src_files` intern id, locator, hash);
  `src_files` holds (provider, relpath, prefix). Amends D39: `source_files`
  rows are replaced on each bundle reload, so they cannot anchor a ref.
- `op_closure` is removed; nested-set `pre`/`post` replaces it (DM-15).
- Codex `threads.title` is emitted as `title_source = thread_title` and used as
  `first_ask` only when no human ask qualifies.
- omp's derived initial turn (no boundary record) is a harness turn with an
  empty ask.
- QRY-38a resolves a provider request id as the suffix of an llm_call `op_id`
  in the session.
- `ops.command_norm` (D17) is stored whole: the one exception to the 160-char
  rule, because clipping would merge distinct commands.
- File paths are clipped to 160 chars at load.
- Timestamps per record come from the stream and are never stored (DM-40).

## D44 — struct-soft alpha .10 → .08 (lead, 2026-10-12)
ink3 on struct-soft at .10 over l1 measured 4.48:1, below SYS-61's 4.5 floor.
Considered: lighten ink3 (changes D31's ink scale everywhere); ink2 for
open-row secondary text (spec change across lists). Chosen: struct-soft =
rgba(255,178,111,.08), about 4.6:1. Limitation: the open-row fill is slightly
fainter.

## D45 — Cost-bar scale on paged lists; toasts do not block keys (lead, 2026-10-12)
(1) API-12 pages rows, so scaling bars to the visible maximum rescales every
bar when a later page brings a larger cost. `stats.max_cost_usd` is the
maximum over all in-scope rows and SYS-39 uses it. (2) A toast is not a
blocking overlay, so j/k keep working while it shows; Esc still closes it
(EXP-73) when no blocking overlay is open.

## D46 — DM-45 walk ends by revisit detection, not time order (lead, 2026-10-12)
About 1.5 % of linked engagements have a cause that starts later than the
engagement, so a time-precedence assert would reject real bundles. Chosen:
revisit detection (BundleInvalid) plus a kind check (the cause must be a
tool_call). Limitation: why those causes start later is not investigated.

## D47 — Histogram bins carry main-thread cost (lead, 2026-10-13)
SES-13/14/17 split a brushed range's cost into main thread and agents, but
API-31 bins carried only `cost_usd`. Considered: a range endpoint (a round trip
per brush move); computing it client-side (needs every call row). Chosen: each
bin gains `main_cost_usd`; range agents cost = Σ `cost_usd` − Σ
`main_cost_usd`. Additive.

## D48 — A resume gap inside a turn span counts as `none` (lead, 2026-10-13)
Some omp turns span a resume gap, which lies outside the session wall, so the
four span states summed short of `span_wall_ms`. Chosen: inside a main turn's
span, time outside the session wall counts as `span_none_ms`, so DM-53's sum
holds and Wall = end − start. Session-level DM-51 is unchanged. Limitation:
Waiting includes the gap for such turns (very rare).

## D49 — CLI `--project` resolves names; unknown filters are errors; use-case help defines fields (lead, 2026-10-13)
A test agent passed a project's display name to `--project` and got an
all-zero result with no error; only the full key matched. Chosen, CLI only
(API-3 unchanged): each `--project` value resolves to an exact key, else an
exact project name (ASCII case-insensitive), else a unique key suffix on a `/`
boundary; none or several matches exit 1 naming up to 5 candidates. The
resolution lives in the query layer (QRY-20). `slow`, `breakdown` and `tasks`
`--help` define their output fields. Limitation: other filters (`--model`,
`--branch`) still return empty silently.

## D50 — The store holds time only as Unix epoch ms; no zone (maintainer, 2026-10-13)
The maintainer required every stored date and time to be Unix epoch, with no
time-zone handling in the store. Chosen: drop `meta.tz`, `bucket_day` and the
day and week rows; keep epoch-aligned 15-minute buckets, fine enough that any
zone's local midnight falls on a bucket edge. A local day is summed at query
time from the buckets in `[local midnight, next local midnight)`. The zone is
a request parameter (`Scope.tz`, HTTP `tz`, CLI `--tz`). Supersedes D37's
time-zone line.

Agent time format (same day): CLI output keeps each `_ms` field and adds an
ISO-8601 sibling with offset in the request zone (`start_ms` → `start`), and
each duration gains a readable sibling (`2h 15m`). `--from/--to` accept
YYYY-MM-DD, an ISO time or epoch ms; days use the machine zone unless `--tz`;
every output states its zone. A CLI render step, not a wire change.

## D51 — Label the header's whole-session figures against the day bar (lead, 2026-10-13)
The explorer header (EXP-2: full figures of sessions active in the period) and
a day bar (EXP-3: calls that started that day) show different costs for the
same day, with nothing on screen saying why. Considered: change the figures
(reverses D32); a caveat chip (D29 forbids). Chosen: copy only. The header
figures carry "Whole sessions active in this period"; the day-bar tooltip's
cost line reads "Calls on <day>". Limitation: the difference is explained, not
quantified.

## D52 — No streams folder; the store is a disposable cache over the agent logs (maintainer, 2026-10-13)
No on-disk intermediate streams beside the store. A full rebuild from the
agent logs is one command. Ingest leaves no files except the store and what
the running app needs. When an agent deletes a log, losing its data is
accepted. Why: the streams repeated facts already in the store at greater
size.

## D53 — Repository history (maintainer, 2026-10-13)
Process only: the repository was published as a single commit.

## D54 — Waiting on you from recorded calls; states with substates (maintainer, 2026-10-21)
Amends D34's idle-time rule. A tool call that waited on the user is time
waiting on the user, not tool or agent time: a question tool (`ask`), or a
permission prompt that the user approved or rejected (`permission`, from the
tool result's `permissionDecision` or the rejection text). Together with the
existing next-turn rule (`reply`), these are substates of the one `you`
state. `none` has the substates `before_first` and `none`. Every instant of
the wall gets one (state, substate) from one ordered rule list in the store,
used at load and at query time. Anything recorded as running beats a wait on
the user. "Waiting on you" is still never inferred from idle time alone.
Why: in the analysis store 69 h of 125 h of main-thread tool time was a
question or a prompt waiting on the user, and the turn Waiting figure left it
out. Limits: approvals before Claude Code 2.1.293 and all Codex/omp
approvals leave no record; a permission wait includes the run after
approval. Evidence and design: tasks/2026-10-21-session-analysis (not in the
repository). The CLI `waits` lists and `longest_wait` use the same wait set as
the `waits` finder: stretches plus main-thread user-wait calls, so a long
question the main thread asked shows in the CLI as it does in the UI.
