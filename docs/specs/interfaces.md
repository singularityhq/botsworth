# Spec S3 — Interfaces: query layer, HTTP API, CLI, frontend wiring

Status: draft for implementation, 2026-10-12. Owner: spec writer S3.
Scope: everything between the store (S2, `docs/specs/data-model.md`) and its consumers: the Python query layer, the HTTP API that the v4 UI calls, the `botsworth` CLI for agents, and the frontend's typed client and data fetching. Page behaviour is in `docs/specs/ui/` (S1a `system.md`, `explorer.md`; S1b `session.md`, `turn.md`, `detail.md`).

Reader: a coder who has not seen the design conversation. Every requirement is numbered (`QRY-`, `API-`, `CLI-`, `FE-`, `TST-`), states observable behaviour, and cites its source. Gaps are in §9 with a recommended answer.

S1b status: rechecked against `session.md`, `turn.md` and `detail.md` (SES-, TRN-, DET- ids) and Kai's cross-rulings D37 on 2026-10-12. §8.2 maps every S1b data need to a field.

Status tags on endpoints and commands: **[exists]** unchanged from today (`evidence/spec-inventory.md` §2, §3), **[changed]** exists with a different shape or behaviour, **[new]**.

## 0. Terms

Model terms (session, engagement, turn, llm_call, tool_call, step, event, own, incl, `time_prov`) are those of `docs/design/design.md` §4. S2 terms (main turn, turn kind, human ask, automated session, request zone, local day, stretch, attributed turn) are those of `data-model.md` §0. Explorer terms (period, filters, in-scope session, measure, full session figures) are those of `explorer.md` §0. New terms:

| Term | Meaning |
|---|---|
| **Scope** | The explorer's period plus its filters, as one value passed to every explorer query (QRY-3). |
| **Day** | A local day in the request zone (`Scope.tz`, QRY-3), written `YYYY-MM-DD` on the wire. |
| **Store version** | The tuple (`schema_version`, `last_ingest_ms`, `price_fingerprint`) read from `meta` [D50]. Two responses with the same store version were computed from the same data. |
| **Wire type** | A JSON shape named in this spec (for example `SessionRow`). The same name is the Python dataclass (QRY) and the TypeScript interface (FE). |
| **Session key** | The session op's `op_id` (`claude_code/<uuid>/session/-`). Endpoints and commands also accept the bare `session_id` or a unique prefix of at least 8 characters (QRY-6). |

## 1. Shared rules

### 1.1 Identity and ids

QRY-1 — Ids on the wire are the stable store ids: `op_id` for ops, `step_id = <turn_id>#<ordinal>` for steps (design §5.2), session key for sessions. Display ids (`T12`, `T12.3`) are extra fields, never the only id (design §6.1a; D21).

QRY-2 — Display turn numbers come from `ops.display_ix` (DM-24): main turn with `display_ix = n` has display id `T<n>`. Harness turns have no display id (`display_id: null`) and are reachable only by `op_id`. This changes today's positional numbering (outline.py `_display_turn`); `outline`, `steps` and every display-id resolver switch to `display_ix` in the same change (S2 Q1 ruling).
  Scenario: Given main turns [human, harness `/clear`, human], when `outline <session>` runs, then the rows read `T1`, (no id, the harness turn is omitted from the outline), `T2`, and `show <session>:T2` resolves to the third turn op.

### 1.2 Scope, days and time zone

QRY-3 [D50] — `Scope` = `{from_day: Day|null, to_day: Day|null, q: str, providers: [str], models: [str], branches: [str], projects: [str], show_automated: bool, tz: str}`. `from_day`/`to_day` are inclusive; both null means all time; exactly one null is a 400 (`QueryError`). `from_day > to_day` is a 400. Defaults: empty lists, `q = ""`, `show_automated = false` (EXP-4, EXP-27). [D50] `tz` is an IANA zone name; it is the zone for every Day, week and hour edge of the request. An unknown `tz` is a 400 (`QueryError`, HTTP `bad_request`). Absent → the zone of the machine answering the query (`FALLBACK-OK: D50`; the UI and the CLI always send one, so the default matters only for bare `curl`).

QRY-4 [D50] — Days are computed in the request zone `Scope.tz` (QRY-3), never in UTC and never in the server process's zone unless `tz` is absent. A local day is the epoch range `[local midnight, next local midnight)`; a local week starts on local Monday; a local hour uses local-hour edges; all are sums of 15-minute buckets (DM-41). The store holds no zone and responses do not name one (`freshness` has no `tz`, QRY-17). The frontend sends the browser's zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`) with every explorer request and resolves presets ("Today", "Last 7 days") to days in that zone (FE-9). The period popover names that zone (replaces D37's store-zone ruling).
  Scenario: Given `tz = Asia/Kolkata`, when `day_series` covers 2026-10-07 with provider `claude_code`, then that day reads tokens 1,847,233,004, cost 549.89 (rounded), 18 sessions (DM-41 acceptance; D28 case 1). The scope has `show_automated = true` (D38 M7). With `tz = UTC` on the same store the day reads different figures, and no re-ingest happens in between.

QRY-5 [D50] — Membership (EXP-1, DM-42): a session is in the period when it has at least one `rollup_time` row with `n_llm_calls > 0` and `bucket_start_ms` in the epoch range from local midnight of `from_day` to local midnight after `to_day`, in `Scope.tz`. The `n_llm_calls > 0` condition is required: some rows are `model IS NULL` companion rows of tool activity with `n_llm_calls = 0` (3,297 of the 7,300 v7 day rows, measured 2026-10-12), and a day with only tool activity does not make a session a member, and EXP-1 counts only owned llm_calls (D38 L1). With an all-time scope, a session is in scope when it has any such row. Sessions with no owned llm_call (98 today, S2 §1) are never in an explorer scope; see Q5.

QRY-6 — Session lookup accepts the session op_id, the bare `session_id`, or a prefix of `session_id` of at least 8 characters. A prefix that matches more than one session raises `QueryError` naming up to 5 matches (400 / exit 1). No match raises `NotFound` (404 / exit 1).

### 1.3 Filters and the automated rule

QRY-7 — Filter semantics (explorer.md EXP-19, EXP-21–23, EXP-27; D32):
- `providers`: session `provider` is any listed value.
- `models`: the session has at least one owned llm_call, in any agent, whose `model` is any listed value. Read from `rollup_time` rows of the session (`model` column), not from ops.
- `branches`: session `git_branch` equals any listed value.
- `projects`: session `project` (raw cwd, D1) equals any listed value.
- automated: when `show_automated = false`, sessions with `sessions.is_automated = 1` (DM-25) are excluded.
- `q`: split on whitespace into terms; a session matches when **every** term is a case-insensitive substring of at least one of its search fields: `name` (QRY-13), the labels of its human-ask main turns (`turn_kind = 'human'`), `projects.name`, `git_branch`, `session_id`, and the provider key and display name (`claude_code`/"Claude Code", `codex`/"Codex", `omp`/"omp"). Matching uses `instr(lower(field), lower(term))`, so `%` and `_` are literal; case folding is ASCII only (SQLite `lower`).
  Different filter kinds combine with AND; values within one kind combine with OR.
  Scenario: Given `q = "rbac opus"` and a session named "Fix rbac sync" that used opus, when the scope is evaluated, then the session matches only if "opus" is a substring of one of its search fields; the model is not one (EXP-19).

QRY-8 — "Except own" counts (EXP-22, EXP-23, EXP-43): a facet or the project panel is computed with the scope minus that one filter kind (the model facet ignores `models`; the project panel ignores `projects`). The period and the automated rule always apply, except the automated count itself, which counts automated sessions under all other filters (EXP-12, EXP-27).

### 1.4 Cost and token rules

QRY-9 — Full figures (D32; EXP-2; DM-42): list rows, group headers, the project panel, the header totals and the palette's session rows use the session's full `sessions.incl_cost_usd` and `incl_tok_total`. A session's full figures never change with the period.

QRY-10 [D50] — Day split (D32; EXP-3; DM-42): a day bar and a palette date row use Σ `rollup_time.cost_usd` / `tok_total` of the buckets inside that local day's epoch range in `Scope.tz`. Therefore a day bar may differ from the sum of the full costs of the sessions active that day.

QRY-11 — Null cost (design §4.7, invariant 6; SYS-32): sums skip null `cost_usd`. Every cost on the wire carries `cost_lower_bound: bool` (true when any contributing owned llm_call has null cost) and `n_unpriced: int` (count of such calls; from Σ `rollup_time.n_unpriced` for sessions and periods, from `ops` for turns and calls). A cost is `null` only when the item has no owned llm_call at all; it is `0` when every call was priced at 0. Tokens follow the same rule (`null` when no call has tokens).
  Scenario: Given a session with three calls costing $1.00, null and $2.00, then its row shows `cost_usd: 3.0, cost_lower_bound: true, n_unpriced: 1`.

QRY-12 — Duplicates (design §4.4): every leaf-level sum, count, histogram bin and finder hit uses owned leaves only (`duplicate_of IS NULL`). Turn cost is the attributed cost (`ops.attr_cost_usd`, DM-45); agent rows inside a turn use leaves with `attr_turn_rowid` = that turn (QRY-34).

### 1.5 Names and colours

QRY-13 — Session name (D34; DM-10; SYS-30): `name` = `sessions.display_title` when not null; otherwise `<projects.name> · <first 8 chars of session_id>`. `title_source` is S2's value: `rename | generated | first_ask | fallback`. The UI applies SYS-30's looks by `title_source` (`fallback` gets the unnamed look). SYS-30's "command label" level is dropped (D37).

QRY-14 [D50] — Project name and colour slot (DM-60; SYS-11): `project_name` = `projects.name`. `color_slot` (1–6 or null) is computed at query time from the store and the request zone: rank projects by Σ `rollup_time.cost_usd` over the 30 local days in `Scope.tz` ending at the local day of `meta.last_ingest_ms` (the epoch range from local midnight of the first of those days to local midnight after the last; all providers, automated included); ranks 1–6 get slots 1–6; ties break by project key ascending; every other project gets null (the "other" series). The slot is the same in every response with the same store version and the same `tz`; two zones can give different slots only through the window edges (a project near a rank boundary). Measured: the 30-day project ranking from `rollup_time` runs in 2 ms once `sessions(op_rowid)` is indexed (§4).
  Scenario: Given the same store, when the period changes from Last 7 days to All time, then every project's `color_slot` is unchanged (SYS-11).

### 1.6 Sort and pagination

QRY-15 — Session sort keys (EXP-51): `start` → `sessions.first_ms`; `cost` → `incl_cost_usd`; `wall` → `wall_ms`; `active` → `active_ms`; `tokens` → `incl_tok_total`. Direction `desc` (default) or `asc`. Nulls sort last in both directions. Ties: `first_ms` descending, then session op_id ascending, so the order is total and stable across pages.

QRY-16 — Pagination of the session list is by offset: `offset` (default 0) and `limit` (default 200, maximum 1,000); the response carries `total` (the in-scope count before paging). Offset is chosen over a cursor because the virtual list (EXP-55) and the rail's `3 / 214` position (EXP-61) need random access and a total. Pages are consistent while the store version is unchanged; a client that sees a different `freshness.last_ingest_ms` between pages refetches from offset 0 (FE-7). Other lists in this spec are bounded and not paged (§4 sizes).

### 1.7 Freshness envelope

QRY-17 [D50] — Every view result carries `freshness: Freshness` = `{last_ingest_ms: number|null, parser_version: string, price_fingerprint: string, schema_version: number, api_version: 1}`. This extends today's `Freshness` (types.ts) with `schema_version` and `api_version` [changed]; D50 removes `tz` (the store has no zone, and the client knows its own) (D5: queries report the last-ingest time).

## 2. Query layer (`botsworth.query`)

QRY-20 — One module is the query layer (design §5). New view functions live in `botsworth.query.views` and are re-exported from `botsworth.query`. HTTP handlers and CLI commands only parse arguments, call one function, and serialise its result with `dataclasses.asdict` plus JSON encoding; they contain no SQL and no aggregation (design §6.2, §6.3; D3; D35).

QRY-21 — Every function takes `db: Db = None` (path or open connection; D20/D21 resolution unchanged), opens the store read-only, and returns a frozen dataclass whose field names and nesting equal the wire type of the same name in §3. Field order in the dataclass is the JSON key order.

QRY-22 — Store state checks run first in every view function: no file → `QueryError("no store at <path>; run 'botsworth ingest'")` subclass `NoStore`; `meta.schema_version` ≠ the code's `SCHEMA_VERSION` → `SchemaMismatch("store schema <n>, botsworth needs <m>; run 'botsworth ingest'")` (DM-1, DM-3). Both map to HTTP 503 and CLI exit 2 (D22).

### 2.1 Functions

| Function | Params | Returns | Used by |
|---|---|---|---|
| `period_summary` | `scope` | `PeriodSummary` | API-10, CLI `period` |
| `day_series` | `scope`, `window_from: Day`, `window_to: Day`, `bucket: day|week = day` (D41 M10) | `DaySeries` | API-11, palette date rows |
| `list_sessions` | `scope`, `sort`, `dir`, `offset`, `limit`, `group: none|project|day`, `group_key: str|None`, `group_limit: int = 5` | `SessionPage` | API-12, CLI `sessions` |
| `facets` | `scope` | `Facets` | API-13 |
| `palette` | `text`, `scope`, `limit_sessions = 8`, `limit_projects = 4` | `PaletteResult` | API-14, CLI `search` |
| `session_detail` | `session` | `SessionDetail` | API-20, CLI `session` |
| `session_turns` | `session`, `min_gap_ms = 120000` | `TurnList` | API-21, CLI `turns` |
| `turn_rows` | `turn`, `from_ms`, `to_ms` | `TurnRows` | API-22, CLI `turn` |
| `agent_rows` | `engagement`, `offset`, `limit` | `RowPage` | API-32 |
| `histogram` | `session` or `turn`, `bins`, `from_ms`, `to_ms` | `Histogram` | API-31 |
| `resolve` | `session`, `id` | `Resolved` | API-29 |
| `detail` | `op` | `Detail` (union by `kind`) | API-24, CLI `detail` |
| `find` | `session`, `kind`, `measure`, `text`, `limit = 2000` | `FindResult` | API-25, CLI `find` |
| `ingest_state` | — | `IngestState` | API-30 |
| `project_slow`, `session_breakdown`, `top_tasks` | §5.2 | `SlowReport`, `SessionBreakdown`, `TaskGroups` | CLI `slow`, `breakdown`, `tasks` (§5.2) |
| `query`, `show`, `timeline`, `steps`, `source`, `outline`, `sessions`, `projects`, `availability` | as today (design §5, §5.2) | as today | [exists]; `steps`/`outline` change per QRY-2 |

QRY-23 — `period_summary(scope)`: totals of in-scope sessions with full figures (QRY-9); `n_sessions` (in scope), `n_sessions_period` (in period, passing only the automated rule; EXP-12 "of 212"), `n_automated_hidden` (automated sessions in the period under the other filters, 0 when `show_automated`); `projects` = per-project full-figure sums under the scope minus `projects` (QRY-8), ranked by cost descending then key; `first_day`/`last_day` = the first and last Day (in `Scope.tz`) with any in-scope-eligible bucket in the store (the "All time · Sep 22 – Oct 12" title, EXP-10); and `empty` (QRY-24).
  Invariant: `totals.cost_usd` = Σ `projects[].cost_usd` when no project filter is set; = Σ over `list_sessions` rows (all pages) of `cost_usd` always.

QRY-24 — `empty` is null unless `n_sessions = 0`. Then it holds `all_time_matches` (count of sessions matching `q` and the filters with an all-time period; null when `q` is empty; EXP-79) and `prev_day`/`next_day` (`{day, n_sessions}` for the nearest earlier and later Day with in-scope sessions under the filters, or null; EXP-80).

QRY-25 [D50] — `day_series(scope, window_from, window_to)`: one `DayBar` per Day (local day in `Scope.tz`) in the window, including days with no activity (zeros, not omitted). It applies every filter and ignores the scope's period (EXP-39). A window over 400 days is a 400. With `bucket = week` (required by the UI for periods over 92 days), one bar per local Monday in `Scope.tz`, summed from the buckets inside `[local Monday, next local Monday)`; a partial week at either edge of the window is clipped to the window, so its figures are the sums of its in-window days, and the bar carries `from_day`/`to_day` naming the dates covered (D41 M10). `by_project` lists every project with activity that day, each with `project`, `project_name`, `color_slot`, `cost_usd`, `tok_total`, ordered by `color_slot` (nulls last) then cost descending. `n_sessions` = distinct in-filter sessions with a bucket of `n_llm_calls > 0` inside that local day (QRY-5; D38 L1).
  Invariant: for one session, Σ over all Days of its day split = its `incl_cost_usd` (DM-42; S2 §1 "Cost totals").

QRY-26 — `list_sessions`: `SessionRow`s of in-scope sessions, sorted (QRY-15) and paged (QRY-16). With `group = project|day` and no `group_key`, it returns `groups` (every group, with summed figures) and each group's first `group_limit` rows under the sort; groups are ordered by the same measure and direction (sum of the sort field; for `start`, the latest `first_ms` in the group) (EXP-56, EXP-57). With `group_key`, it returns the flat paged rows of that one group ("Show N more", SYS-38). Day groups use the local day of `first_ms`. `stats.max_activity_tok` = the largest activity bin among **all** in-scope rows (the shared strip scale, SYS-40, EXP-53); `stats.cost_p90` = the 90th percentile of in-scope rows' `cost_usd` (nearest-rank, nulls excluded; EXP-49 weight rule).

QRY-27 — `facets(scope)`: `providers`, `models`, `branches`: every value in the store with `n` = in-scope sessions under the scope minus that facet's own filter (QRY-8), ordered by `n` descending then value. Values with `n = 0` are included (the UI folds them, EXP-22). `branches` omits null branches. `automated.n` = automated sessions in the period under the other filters (EXP-24).

QRY-28 — `palette(text, scope)` (EXP-69, EXP-70, EXP-72):
- `filter_values`: when `text` starts with `model:`, `branch:`, `provider:` or `project:`, the matching facet values (substring of the rest, case-insensitive) with their `facets` counts; otherwise empty.
- `projects`: up to 4 projects whose `project_name` contains `text`, each with `n_sessions`, `n_automated` (its automated sessions, DM-60; D38 M5) and full cost over all time.
- `sessions`: up to 8 sessions from **all time**, ignoring the scope's period and filters, matching every term of `text` (QRY-7 `q` rule). Rank: number of terms found in `name` descending, then exact `project_name` match first, then `first_ms` descending. Each hit carries `in_period` (has a bucket in the scope's period, QRY-5) and `in_filters` (passes the scope's filters and automated rule), so the UI can apply EXP-72 without a second request, plus `first_day`, the first local Day (in `Scope.tz`) with a bucket of `n_llm_calls > 0` (not the session's start day; D38 M5). EXP-72 uses it.
Date parsing for date rows is client-side (FE-10); their facts come from `day_series`.

QRY-29 — `session_detail(session)` returns the session header (§3.6; SES-24–28).
- `cost.main` = Σ cost of owned llm_calls with `agent_id IS NULL`, and `cost.agents` = total − main (SES-14).
- `cost.unattributed` = Σ cost of leaves with `attr_turn_rowid IS NULL` (DM-45; SES-28 "Agent work with no turn").
- `time_split` holds the session's `time_split` rows with scope `session` (DM-51; D54): one part per (state, substate) with time above 0. The parts sum to `wall_ms`. `none`/`before_first` is never folded into `you` (D41 M11).
- `longest` maps each of the seven states to its longest jump target, or null:
  - `model`: the longest main-thread llm_call; `tool`: the longest main-thread tool_call with `user_wait` NULL;
  - `you`: the longest of the `you` stretches and the main-thread tool_calls with `user_wait` set (D54);
  - `agents`, `agentmsg`, `limit` and `none`: the longest stretch with that state.

  Each target is `{row: RowRef, turn, dur_ms}`. A target exists only at 2 min or more; otherwise it is null (SES-27; D37). Every legend jump goes to the longest row or stretch of its state; no jump uses a "largest share" turn (D38 H2).
- `models`: per QRY-33.
- `n_agents` = the number of distinct `agent_id` values over the session's engagements.
- `n_skill_loads` = steps with `skill_load` set, plus slash `skill_invocation` events that have no step (DM-30; SES-55).

QRY-30 — `session_turns(session, min_gap_ms)` returns every main turn and every gap row between turns, in time order (SES-34–37). Time order is `start_ms`, ties by `seq`, or by `pre` when `seq` is NULL (Codex ops carry no `seq`; the DM-24 rule, FX2-BE).
- Harness turns are included with `kind = 'harness'` so the UI can fold them (D34; SES-35).
- Turn order is `start_ms`, then `seq`. Turns with null `start_ms` (198 today, S2 §5) sort by `seq` among their neighbours.
- Turn figures use the **turn span** `[start_ms, end_ms]` (D37; SES-21):
  - `wall_ms` = `span_wall_ms` (DM-53);
  - `time_split` = the turn's `time_split` rows with scope `span` (DM-53);
  - `waiting_ms` = `wall_ms` − span `model` − span `tool`, the time when no main-thread model call or tool was working (SES-21; D38 L2; D54: time waiting on the user counts here, not as tool);
  - `cost_usd` = `attr_cost_usd` (SES-12);
  - `cost_main` = Σ cost of the turn's own main-thread llm_calls;
  - `models` = the main-thread models, each with its cost, most costly first (SES-34 item 4);
  - `n_agents` = the number of agent rows (QRY-34), `n_skill_loads` (SES-55), and `n_failed` (failed tool rows, QRY-37);
  - `agent_runs` = `{engagement, agent_name, start_ms, end_ms}` for each engagement whose `caused_by` tool_call has `attr_turn_rowid` = this turn. This is the DM-55 join, and these are the same engagements as the turn's agent rows (QRY-34). They draw the Waterfall agent tails (SES-42; D37).
- Gap rows are stretches of state `you`, `agentmsg`, `limit` or `none` with `dur_ms ≥ min_gap_ms` (default 120000, SES-16) that end where a main turn starts or that lie before the first numbered turn (SES-36). `agents` stretches are never gap rows here (SES-19).
- Invariant: Σ turns' `cost_usd` + `session.cost.unattributed` = the session's `cost.total` (DM-45).

  Scenario: Given session a11ce5e5, then a 1h02m gap row with `state = 'limit'` sits directly above the resume turn, and its `next_turn.label` starts with "I hit my usage limit" (SES-17).

QRY-31 — `turn_rows(turn, from_ms=None, to_ms=None)` returns the Ledger and Waterfall rows of one main turn, at depth 0, ordered as it ran (TRN-30–34; DM-46):
- one `CallRow` per owned llm_call whose parent is this turn. It carries its own think, say and answer steps (`steps`) for the row name and the "final" tag (TRN-31);
- one `ToolRow` per tool_call of those calls that caused no engagement (TRN-32);
- one `AgentRow` per tool_call (spawn or SendMessage) that caused one or more engagements (TRN-33; QRY-34). Engagements caused by a later turn's SendMessage appear in that later turn (DM-45 walk);
- one `GapRow` per stretch (any state) of 60 s or more that SES-37 attaches to this turn, with its `state` and `substate` (QRY-41). The UI applies its 2 min threshold (SES-16);
- with `from_ms`/`to_ms` (a brushed zoom, TRN-24), `range` holds the clock-rule figures (SES-13): `cost`, `cost_main`, `cost_agents`, `time_split` over the range (computed by the DM-50 function, so a range equal to the span gives the stored span split), and per agent row `{row, cost_usd, tok_total, n_calls_in_range, n_calls}`. Without a range, `range` is null.
- Invariant: Σ `CallRow.cost_usd` + Σ `AgentRow.cost_usd` = the turn's `attr_cost_usd`. Both sides skip nulls; tool rows and gaps carry no cost.

QRY-32 — `agent_rows(engagement_or_row, offset, limit)` returns the rows inside one agent row (TRN-35). These are the call, tool, nested-agent and gap rows of the row's engagements, as it ran, each with `depth` = the nesting level (1–3; deeper rows report 3). Pages use `offset` (default 0) and `limit` (default 500, max 2000), with a `total`.

QRY-32a — `histogram(scope_op, bins, from_ms, to_ms)` covers the turn histogram (TRN-21–22) and the session's "Over time" chart (SES-29). `scope_op` is a main turn or a session. Bins are `bins` (1–1000, default 600 for a session and 120 for a turn) equal bins over `[from_ms, to_ms]`. The default range for a session is its first-to-last record. For a turn it is `[start_ms, max(end_ms, latest agent_runs[].end_ms)]` (D41 N1): the part after `end_ms` is the tail zone, the response carries `tail_from_ms` = `end_ms` (null when there is no tail), and Σ bin `cost_usd` over the default range = the turn's `attr_cost_usd`. Turn Wall and Waiting stay span-based (QRY-30).
- Each bin sums owned leaves **by start time**:
  - turn: the leaves attributed to it (`attr_turn_rowid`), so caused agents are included (TRN-22);
  - session: all of the session's leaves.
- Bin fields:
  - `cost_usd` and `tok_total`;
  - `by_model` (`{model: [cost_usd, tok_total]}`; turn only);
  - `state_ms` (a `TimeSplit`): the DM-50 (state, substate) time inside the bin, for the state lane (SES-29), computed by the same function as the stored figures (D54).
- `marks` holds `turn_starts` (`[{turn, display_id, start_ms}]`; session only) and `skill_loads` (`[{row, start_ms, skill}]`) (SES-29 marks).
- `stretches` holds every stretch of 60 s or more in the range, as `Stretch` (QRY-41). The UI folds waits of 10 min or more on the axis.

QRY-33 — Model lines (DM-47; SES-28; TRN-5) are computed per model over the item's owned llm_calls:
- `n_calls`;
- `cost_usd`, with the lower-bound fields;
- `model_ms` = Σ `dur_ms`;
- `speed_tok_s` = Σ `out_tok` / Σ `dur_ms` × 1000, over calls with `dur_ms > 0` and non-null `out_tok`. It is null when no call qualifies;
- `speed_basis` = the weakest `time_prov` among those calls;
- `ttft_ms_mean` = the mean of the non-null `ttft_ms` values, or null.

Lines are ordered by cost, descending.

QRY-34 — An agent row (TRN-33; SES-12; DM-36; DM-45) has one causing tool_call C and its engagements E1…En (`caused_by = C`).
- `row` = E1's op_id. This is the SES-9 row id; any Ei resolves to this row.
- `mode` = `messaged` when C is a SendMessage, otherwise `started`.
- `description` = C's step summary.
- `agent_names`: one per engagement.
- `cost_usd`, `tok_total` and `n_llm_calls` are the **whole** cost of the engagements: the sums over owned leaves whose DM-45 walk passes through E1…En. Nested agents are included. A `+cycle` reply caused by a SendMessage in a later turn is not included; it belongs to that turn's row.
- `run_ms` = the union of the Ei intervals (DM-36), and `runs` lists them. Both are per agent row, that is per causing call C. A SendMessage in a later turn is a separate row with its own `runs` (D38 M12).
- `models` = the models of the included leaves.
- `n_rows` = the depth-1 row count for "N rows" (TRN-33).
- `n_errors` = the number of failed tool rows among the included leaves.

  Scenario: Given the 78-turn session, turn 35, then the turn's `cost_main` = 0.88 and its agent rows sum to 25.11 (TRN-4 scenario).

QRY-35 — `detail(op)` returns one of the following kinds (DET-10–50):
- `kind = 'call'` (an llm_call):
  - model; `start_ms`, `dur_ms`, `time_prov` and `ttft_ms`;
  - tokens (`in`, `cache_read`, `cache_write`, `out`, `reasoning` and `context_tokens`);
  - the cost split (`cost_in_usd`, `cost_out_usd`, `cost_cache_read_usd`, `cost_cache_write_usd`, `cost_usd` and `cost_basis`);
  - `speed_tok_s` with its basis, `stop_reason`, `error_kind` and `turn_cost_usd` (for "N% of the turn's cost", DET-10);
  - `sections`: the call's content blocks in order, each `{part: 'thinking'|'text'|'tool_use', src, snippet, bytes}` (DET-13). `tool_use` entries link to their `ToolRow` or `AgentRow`;
  - `position`.
- `kind = 'tool'` (a tool_call):
  - the `ToolRow` fields, including `command` (the 160-char preview), `command_src` (the reference to the full command, which the detail blade loads through `/api/source`; D42), `file_path`, `target` and `status` (QRY-37);
  - `input_src` and `output_src` (DET-23, DET-24) and `neighbours` (the 2 rows before and the 2 rows after it);
  - `position`.
- `kind = 'agent'` (any op_id of an agent row's engagements):
  - the `AgentRow` fields and `turn_cost_usd` (DET-30);
  - `runs`, each with `ask_src` = the `src` of its `caused_by` tool_call for `part=input` (DET-32);
  - `top_calls`: the 5 costliest llm_calls of the included leaves, as `CallRow`s (DET-34);
  - `other_calls` = `{n, cost_usd}` for the rest. The 5 rows plus `other_calls.cost_usd` equal the row's cost;
  - `focus_engagement`.
- `kind = 'message'` (a turn op):
  - `turn_kind`, `turn_origin`, `label`, `display_id` and `text_src` (the turn's `src` for `part=text`);
  - `text_bytes` for the character count (TRN-5).

Any other op kind is a 400. A gap has no endpoint: the UI builds its detail from data it already has (D37). The stretch's `running` list gives DET-43, `next_turn` gives DET-41 and DET-42, and the turn or session wall gives DET-40.

QRY-36 — `position` on a detail is `{session, turn, display_id, row, depth}`. With it the UI can place the cursor and open agent bands (D33; SES-50).

QRY-37 — Tool status and failure (DET-R §5 item 4; TRN-32):
- `status` is the first that applies:
  1. `exit:<n>` from `exit_code`, when it is not null;
  2. `interrupted`, when `ops.interrupted = 1`;
  3. the step's `outcome`, when it is `error`, `interrupted` or `exit:<n>`;
  4. `ok`, when the outcome is `ok`;
  5. null otherwise. Never default to ok. This order is the source for DET-20 (D38 L8).
- `failed` = `is_error`, or an exit code other than 0, or `interrupted`, or a step outcome `exit:<n>` with n ≠ 0.

QRY-38 — `find(session, kind, measure, text, limit)` returns hits over the whole session, in session order. Session order is turns by time, then rows as they ran inside each turn, with an agent's rows after its agent row. Harness turns are skipped (SES-51). The UI walks the hits from its cursor and wraps.

| `kind` | Hits | Order |
|---|---|---|
| `biggest` | every depth-0 row of every numbered turn whose `measure` value is above 0. `cost` and `tokens` cover call and agent rows; `time` covers call, tool, agent and gap rows. Tool rows with `user_wait` set or `max_silence_ms ≥ 300000` are left out of `time` (SES-52) | value, descending |
| `waits` | gap rows of every state with `dur_ms` of 2 min or more, plus main-thread tool rows with `user_wait` set and `dur_ms` of 2 min or more (SES-53; D38 M4; D54) | session order |
| `failures` | failed tool rows (QRY-37) at any depth (SES-54) | session order |
| `skills` | skill-load rows; a slash turn with an event and no step is a hit on the turn (SES-55) | session order |
| `agents` | agent rows at any depth (SES-56) | session order |
| `search` | rows whose stored text contains every term of `text` (QRY-7 matching). The fields are the row name, sub-text and target, `tool_name`, `command`, `file_path`, step `summary` and `snippet`, the agent name, and the turn's label (SES-32). `command` is the stored 160-char preview, so search matches the preview only, never the full command (D42) | session order |

Each finder has one hit set (D38 M4): the chip count, `per_turn`, `total` and the key all read it. `waits` has the 2 min floor, and `agents` and `failures` count at any depth. Each hit is `{row: RowRef, turn, display_id, depth, label, value, start_ms}`. `total` counts all hits, and `hits` holds the first `limit` hits. `per_turn` maps each turn to its hit count; the session list shows it as `search N` (SES-34 item 4).

QRY-38a — `resolve(session, id)` (SES-9) accepts any of these ids:
- an op_id;
- a provider request id (matched as the suffix of an llm_call `op_id` within the session; `occ_key` is a hash, DM-14);
- a `tool_use` id;
- an engagement op_id;
- `gap-<start_ms>`.

It returns `{row: RowRef, turn, depth}`. An id with no matching row raises `NotFound`. `RowRef` = `{type: 'call'|'tool'|'agent'|'gap'|'turn', id}`: the op_id for ops (a message is not a row; its detail is encoded as `RowRef` type `'turn'` with the turn's op_id, so a reload reopens it; D38 L6), `gap-<start_ms>` for gaps, and E1's op_id for agent rows.

QRY-41 — Stretches (D37; D54; S2 table `stretches`). A `Stretch` has these fields:
- `start_ms`, `end_ms`, `dur_ms`, `state` and `substate` as stored (DM-50, DM-52: `agents`, `you` with `reply`/`ask`/`permission`, `agentmsg`, `limit`, `none` with `before_first`/`none`), and `turn` (the attached turn). QRY-41 recomputes `turn` by SES-37 (the latest numbered turn, never a harness turn) and does not read `stretches.turn_rowid` (D38 M11). QRY-41 does not compute labels: the load decided them (DM-50 rule 5);
- `next_turn` = `{turn, kind, origin, label, display_id}` from the DM-55 join. That join reads `turn_kind`, `turn_origin` and `label` from the session's next main turn by `start_ms` (lowest `seq` at a shared start). `next_turn` is set only when that turn's `start_ms` = the stretch's `end_ms`; otherwise it is null (SES-15; DET-41; D37);
- `running` (`agents` stretches, and `you` stretches from an agent's call): up to 8 engagements whose run overlaps the stretch, each `{row, agent_name, start_ms, end_ms, cost_usd}`, with the whole engagement cost (DET-43; TRN-34). It is `[]` for other stretches.

The limit label is an inference from the next turn's text (SES-17). The wire carries the label and the turn text; the UI writes "(inferred)".

QRY-42 [D56] — Review. `session_review(session)` and `scope_review(scope)` return the DM-56 causes and the findings built on them. Both read only stored rows (`time_split` scope `session`, `stretches`, `ops`, `events`); nothing is estimated.
```ts
type Cause = 'stalled' | 'tests' | 'polling' | 'you' | 'message' | 'limit' | 'working' | 'idle'
interface CausePart { cause: Cause; ms: number }               // all eight, in this order; Σ ms = wall_ms
interface Evidence { session: string; session_id: string; row: RowRef | null; turn: string | null; display_id: string | null;
                     start_ms: number; end_ms: number; dur_ms: number }
interface Finding { cause: 'stalled' | 'tests' | 'polling' | 'you' | 'message'; ms: number; lost: boolean;
                    n: number;                                  // stalled, you, message: stretches of ≥ 2 min; tests, polling: tool_calls of that class
                    longest: Evidence | null;                   // stalled, you, message: the longest stretch (row = gap RowRef); tests, polling: the longest candidate tool_call of that class (row = tool RowRef), its run to bg_end_ms when it ran in the background, else dur_ms. For a main background test run, row = the gap RowRef of the run's first `tool`/`test` stretch (the "Background command" row that shows the run; DM-52), or the tool RowRef when the run has no stretch. A main-transcript background run of class wait is no candidate: DM-50 counts it as tool/background (working), not polling. dur_ms is the call's whole run, so a call that overlapped work DM-50 ranks higher can exceed the finding's ms (18 of 1,244 findings on the real store, 2026-10-21, all agent calls but one)
                    threads: string[];                          // stalled only: the agent names (agent_name, else agent_id; "main" for the main thread) whose notices fall in the stall stretches; else []
                    sessions: { session: string; session_id: string; name: string; ms: number }[] }  // scope only: the 3 sessions with the most ms of the cause; [] for a session
interface Review { wall_ms: number; lost_ms: number; n_sessions: number;
                  n_unmeasured: number;                         // sessions whose provider records no task notices (codex, omp; DM-41): stalls are not measured there
                  causes: CausePart[]; findings: Finding[]; test_runs: TestRuns }
```
- `findings`: one per cause among `stalled`, `tests`, `polling`, `you`, `message` with `ms` ≥ 120,000; lost findings first, then by `ms` descending, then by the cause order. `you` findings carry the split in the session's `time_split`; the UI reads it there. A main-thread wait on the user (`AskUserQuestion`, a permission prompt) lies inside a tool_call and is no stretch (DM-52), so a `you` finding can have `ms` ≥ 2 min with `n` 0 and `longest` null; the UI then shows no evidence line (REV-6).
- For a scope, `wall_ms`, `causes` and `test_runs` are sums over the in-scope sessions (QRY-9 full sessions), so `wall_ms` can exceed the clock time of the period; `longest` is the longest over all in-scope sessions.
- `n` counts different things by cause: stretches of ≥ 2 min for `stalled`, `you` and `message`; tool_calls of the class for `tests` and `polling`. The `tests` cause is wall time held by tests (each instant once); `test_runs.run_ms` sums each run, and runs overlap, so it can be larger.
- Invariants: Σ `causes.ms` = `wall_ms`; `lost_ms` = the `stalled` part; `test_runs` equals CLI-20/CLI-21's block for the same sessions.
  Scenario: Given the DM-56 test session, `session_review` → `lost_ms` 95 min, findings in the order `stalled` (lost), `tests`, `you`, `polling`; `causes` sum to the wall.

QRY-39 — `ingest_state()` (D22, D26): never raises for a missing store. Returns `store_state` = `missing | schema_mismatch | ready`, `freshness` (with `last_ingest_ms: null` when missing), `sources` and `last_run` as today, and `ingest` = `{running: bool, started_ms: number|null, pid: number|null}` from the lock file (QRY-40).

QRY-40 — Ingest-in-progress signal: after taking the lock (`store/ingest.py` `_lock`), the ingest writes `{"pid": <pid>, "started_ms": <ms>}` into `<db>.lock` and truncates it on release. `ingest_state` reads the file without locking; `running` = the file holds a pid and that process exists (`os.kill(pid, 0)` succeeds). Probing the flock is not allowed: a probe that takes the lock for an instant can make a starting ingest fail with `IngestLocked`.

## 3. HTTP API

### 3.1 Conventions

API-1 — Server: stdlib `ThreadingHTTPServer`, started by `botsworth serve` (CLI-10). All API paths start with `/api/`. Every other path serves the UI assets (API-41). Path ids are percent-encoded by the client (`encodeURIComponent`; op ids contain `/` and `#`) and decoded per segment (server.py contract, unchanged).

API-2 — Query parameters: lists repeat the key (`?provider=codex&provider=omp`); booleans are `0|1`; days are `YYYY-MM-DD`; integers are base-10. An unknown parameter is ignored. A malformed value (bad day, `limit` over its maximum, unknown `sort`) is a 400 naming the parameter.

API-3 [D50] — Scope parameters (explorer endpoints), mapping 1:1 to `Scope` (QRY-3) and to the explorer URL (EXP-74): `from`, `to` (Day; both absent = all time), `tz` (IANA zone name; absent → the answering machine's zone; unknown → 400 `bad_request` JSON; the UI always sends the browser zone, so `tz` is not part of the explorer URL), `q`, `provider`*, `model`*, `branch`*, `project`*, `auto` (`show` sets `show_automated`; absent = hidden). The frontend sends days, never preset names (FE-9).

API-4 — Errors are JSON `{"error": "<message>", "code": "<code>"}` with `content-type: application/json`, never HTML (design §6.3; D22). `code` [new, additive] ∈ `bad_request` (400), `not_found` (404), `source_missing` (404), `method_not_allowed` (405), `no_store` (503), `schema_mismatch` (503), `internal` (500). A 500 logs the traceback to stderr and does not stop the server.

API-5 — Store states on data routes (D22; DM-3; QRY-22): no store file → 503 `no_store`; schema version differs → 503 `schema_mismatch`; store present but empty → 200 with zero figures and empty lists. `/api/ingest/status` always answers 200 (API-30).

API-6 [D50] — Caching. Every 200 JSON response from a data route carries `Cache-Control: no-cache` and a weak `ETag: W/"<api_version>.<schema_version>.<last_ingest_ms>.<price_fingerprint>"`. The zone is not in the tag because it is in the query string, and the URL already varies by zone. A request whose `If-None-Match` equals the current tag gets 304 with no body. Exceptions: `/api/ingest/status` and `/api/source` send `Cache-Control: no-store` and no ETag (status changes without an ingest; source files change on disk). While an ingest is running (QRY-40), data routes send `no-store` and no ETag, because bundles commit before `last_ingest_ms` changes.
  Scenario: Given an unchanged store, when the client repeats `GET /api/explorer/summary?from=2026-10-06&to=2026-10-12` with the previous ETag, then the response is 304.

API-7 — Every 200 body is an object with `freshness` (QRY-17) at the top level, except the asset routes.

### 3.2 Endpoint index

| Method | Path | Function | Status | UI consumer |
|---|---|---|---|---|
| GET | `/api/ingest/status` | `ingest_state` | [changed] | freshness, banners (EXP-8, SYS-57) |
| GET | `/api/explorer/summary` | `period_summary` | [new] | title, totals, project panel, empty states |
| GET | `/api/explorer/days` | `day_series` | [new] | day chart, palette date rows |
| GET | `/api/explorer/sessions` | `list_sessions` | [new] | session list, rail, palette "Largest" |
| GET | `/api/explorer/facets` | `facets` | [new] | filter popover, palette `model:` etc. |
| GET | `/api/palette` | `palette` | [new] | ⌘K palette |
| GET | `/api/sessions/{session}` | `session_detail` | [new] | session blade header |
| GET | `/api/sessions/{session}/turns` | `session_turns` | [new] | session blade turn list with gap rows |
| GET | `/api/sessions/{session}/find` | `find` | [new] | finder keys and chips |
| GET | `/api/turns/{turn}/rows` | `turn_rows` | [new] | turn blade Ledger and Waterfall |
| GET | `/api/turns/{turn}/histogram` | `histogram` | [new] | turn histogram and brush |
| GET | `/api/sessions/{session}/histogram` | `histogram` | [new] | session "Over time" |
| GET | `/api/sessions/{session}/resolve` | `resolve` | [new] | `step` URL ids (SES-9) |
| GET | `/api/ops/{engagement}/rows` | `agent_rows` | [new] | an agent's rows (`l`) |
| GET | `/api/ops/{op}/detail` | `detail` | [new] | detail blade |
| GET | `/api/sessions/{session}/review` | `session_review` | [new] (API-33) | session Review (REV-3) |
| GET | `/api/explorer/review` | `scope_review` | [new] (API-33) | project review (REV-20) |
| GET | `/api/source` | `source` | [changed] | lazy full text |
| POST | `/api/query` | `query` | [exists] | none in v4 (agents, D35) |
| GET | `/api/ops/{op}` | `show` | [exists] | none in v4 |
| GET | `/api/ops/{op}/timeline` | `timeline` | [exists] | none in v4 |
| GET | `/api/ops/{op}/outline` | `outline` | [changed] (QRY-2) | none in v4 |
| GET | `/api/turns/{turn}/steps` | `steps` | [exists] | none in v4 |
| GET | `/api/sessions` | `sessions` | [changed] | none in v4 |
| GET | `/api/projects` | `projects` | [changed] | none in v4 |
| GET | `/api/availability` | `availability` | [exists] | none in v4 |

The v4 UI no longer calls `POST /api/query`, `/timeline`, `/outline` or `/steps`; they stay for agents and scripts (D35: no GraphQL; `POST /api/query` covers flexible queries). The current views that use them (HotspotTable, Icicle, Timeline, Usage, Outline, Projects, Skills) are replaced by the v4 blades (D29, D33).

### 3.3 Shared wire types

```ts
type Day = string                         // 'YYYY-MM-DD', local day in the request zone (Scope.tz, D50)
type Provider = 'claude_code' | 'codex' | 'omp'
type Provenance = 'present' | 'derived' | 'estimated' | 'missing'
type TitleSource = 'rename' | 'generated' | 'first_ask' | 'fallback'
type TurnKind = 'human' | 'message' | 'harness'

interface Freshness { last_ingest_ms: number | null; parser_version: string; price_fingerprint: string;
                      schema_version: number; api_version: 1 }   // D50: no tz
interface Money { cost_usd: number | null; cost_lower_bound: boolean; n_unpriced: number }   // QRY-11
type WallState = 'model' | 'tool' | 'agents' | 'you' | 'agentmsg' | 'limit' | 'none'
type Substate = 'ask' | 'permission' | 'reply' | 'test' | 'wait' | 'background' | 'stalled' | 'before_first' | 'none' | null  // DM-50 [D54, D55, D56]
interface TimeSplit { parts: { state: WallState; substate: Substate; ms: number }[] } // DM-51, DM-53; SES-15 order
interface ModelLine extends Money { model: string; n_calls: number; model_ms: number;
                      speed_tok_s: number | null; speed_basis: Provenance; ttft_ms_mean: number | null }
interface ProjectRef { project: string; project_name: string; color_slot: 1|2|3|4|5|6 | null }
```

`Money` fields are flattened into the object that uses them (no nested `money` key). Every number on the wire is a JSON number; ms are epoch milliseconds or durations in ms; money is USD, unrounded.

### 3.4 Explorer

API-10 — `GET /api/explorer/summary?<scope>` [new] → `PeriodSummary`:
```ts
{ freshness: Freshness,
  first_day: Day | null, last_day: Day | null,            // data extent, all time (EXP-10)
  totals: Money & { tok_total: number | null; n_sessions: number; n_sessions_period: number;
                    n_automated_hidden: number },
  projects: (ProjectRef & Money & { tok_total: number | null; n_sessions: number })[],  // all, ranked by cost
  empty: null | { all_time_matches: number | null;
                  prev_day: { day: Day; n_sessions: number } | null;
                  next_day: { day: Day; n_sessions: number } | null } }
```
Both measures are returned, so the measure toggle (EXP-31) needs no request. The client takes the top 6 by the selected measure for the donut and legend (EXP-44, EXP-45).
  Scenario (EXP-2): a $450 session with $300 of calls on Oct 7 and $150 on Oct 8, period Oct 7 only → `totals.cost_usd` includes 450.0.

API-11 — `GET /api/explorer/days?window_from=&window_to=&bucket=day|week&<scope minus from/to>` [new; `bucket` default `day`, D41 M10] → `DaySeries`:
```ts
{ freshness: Freshness,
  days: { day: Day; from_day: Day; to_day: Day;   // day bars: both = day; week bars: covered dates after clipping (D41 M10)
          cost_usd: number; cost_lower_bound: boolean; n_unpriced: number;   // D38 M6
          tok_total: number; n_sessions: number;
          by_project: (ProjectRef & { cost_usd: number; tok_total: number })[] }[] }
```
The client computes the window (EXP-32) and sends it. `cost_usd` here is a sum of priced calls (0 when none). `n_unpriced` = Σ `rollup_time.n_unpriced` of the buckets inside the day, and `cost_lower_bound` is true when it is above 0; day figures show `+` (SYS-32; D38 M6). Same scenario: the Oct 7 bar includes 300.0 of that session and Oct 8 150.0 (EXP-3).

API-12 — `GET /api/explorer/sessions?<scope>&sort=&dir=&offset=&limit=&group=&group_key=&group_limit=` [new] → `SessionPage`:
```ts
interface SessionRow extends ProjectRef, Money {
  session: string; session_id: string; provider: Provider;
  name: string; title_source: TitleSource;                     // QRY-13
  start_ms: number; end_ms: number; ends_after_days: number;   // local midnights crossed (EXP-52 "+Nd")
  tok_total: number | null; wall_ms: number; active_ms: number;
  is_automated: boolean; n_agents: number; git_branch: string | null;
  activity: { bin_ms: number; tok: number[] };                 // DM-44 (SYS-40)
  day_split: { day: Day; cost_usd: number; tok_total: number }[]  // every active day (EXP-40 footprint)
}
interface SessionGroup extends Money {
  key: string; name: string; color_slot: 1|2|3|4|5|6 | null;   // project key or Day
  n_sessions: number; tok_total: number | null; wall_ms: number; active_ms: number;
  period_activity: { bin_ms: number; start_ms: number; tok: number[] };  // SYS-37 group strip
  rows: SessionRow[] }                                         // first group_limit rows
{ freshness: Freshness, total: number, offset: number, limit: number,
  stats: { max_activity_tok: number; max_cost_usd: number; cost_p90: number | null },  // max_cost_usd: over all in-scope rows (D45)
  rows: SessionRow[],            // flat mode, or one group's page when group_key is set
  groups: SessionGroup[] | null }  // grouped mode without group_key
```
`period_activity` [D50] uses local-hour sums of the buckets when the period is 14 days or shorter, else local-day sums, over the period (in `Scope.tz`) (all time: the data extent). Defaults: `sort=start`, `dir=desc`, `offset=0`, `limit=200` (max 1000), `group` absent (flat), `group_limit=5` (max 50). The rail (EXP-60) uses the same endpoint and parameters; the palette's "Largest in this period" uses `sort=cost&limit=6` (EXP-68).

API-13 — `GET /api/explorer/facets?<scope>` [new] → `Facets`:
```ts
{ freshness: Freshness,
  providers: { value: Provider; n: number }[],
  models: { value: string; n: number }[],
  branches: { value: string; n: number }[],
  automated: { n: number } }
```

API-14 — `GET /api/palette?text=&<scope>` [new] → `PaletteResult`:
```ts
{ freshness: Freshness,
  filter_values: { kind: 'model'|'branch'|'provider'|'project'; value: string; label: string; n: number }[],
  projects: (ProjectRef & Money & { n_sessions: number; n_automated: number })[],   // D38 M5
  sessions: (Pick<SessionRow, 'session'|'session_id'|'provider'|'name'|'title_source'|'project'|'project_name'
             |'color_slot'|'start_ms'|'cost_usd'|'cost_lower_bound'|'n_unpriced'|'is_automated'>
             & { first_day: Day; in_period: boolean; in_filters: boolean })[] }
```
Empty `text` returns empty lists (the empty-input sections come from API-12 and fixed date presets, EXP-68).

### 3.5 Ingest status

API-30 [D50] — Ingest and readers (D37): a full rebuild (schema change; D50's v8 is one, so a v7 store is rebuilt on the first ingest) writes a new store file and renames it over the old one on success, so a reader never sees dropped tables; each request opens the store anew (QRY-21) and picks up the swapped file. An incremental ingest commits bundle by bundle (WAL), so figures may be partial while it runs.

`GET /api/ingest/status` [changed] → `IngestStatus`, always 200 (D22):
```ts
{ store_exists: boolean,                      // kept for compatibility; = store_state !== 'missing'
  store_state: 'missing' | 'schema_mismatch' | 'ready',
  freshness: Freshness,                       // last_ingest_ms null when missing
  ingest: { running: boolean; started_ms: number | null; pid: number | null },
  sources: { provider: Provider; root: string; files: number; bundles: number; live: number;
             last_ingest_ms: number | null; rejected: number }[],
  last_run: { started_ms: number; ended_ms: number; bundles_changed: number; rejected: unknown[] } | null }
```
`sources` has one entry per provider in `source_files`: `root` is the provider folder the last ingest scanned, `files` counts its `source_files` rows, `bundles` its distinct bundles, `rejected` its bundles with a `reject_reason` (D52).
With `store_state` other than `ready`, `sources` is `[]` and `last_run` is null (DM-3). With `missing`, `schema_version` is the code's. [D50] `freshness` has no `tz`.

### 3.6 Session blade

Shared row types (the session, turn and detail endpoints all use them):
```ts
type RowRef = { type: 'call' | 'tool' | 'agent' | 'gap' | 'turn'; id: string }       // QRY-38a
interface NextTurn { turn: string; kind: TurnKind; origin: string; label: string; display_id: string | null }
interface Stretch { start_ms: number; end_ms: number; dur_ms: number;
  state: 'agents' | 'tool' | 'you' | 'agentmsg' | 'limit' | 'none'; substate: Substate;  // DM-52 [D54]; `tool` only with `background` [D55] or, for a background run, `test` [D56]
  turn: string | null; next_turn: NextTurn | null;
  running: { row: string; agent_name: string | null; start_ms: number; end_ms: number; cost_usd: number | null }[] }  // QRY-41
```

API-20 — `GET /api/sessions/{session}` [new] returns `SessionDetail`:
```ts
{ freshness: Freshness,
  session: ProjectRef & {
    session: string; session_id: string; provider: Provider; name: string; title_source: TitleSource;
    cwd: string; git_branch: string | null; harness_version: string | null;
    entrypoint: string | null; originator: string | null;                  // DM-26
    start_ms: number; end_ms: number; ends_after_days: number; is_automated: boolean;
    n_turns: number; n_harness_turns: number;
    cost: Money & { main: number | null; agents: number | null; unattributed: number | null },  // SES-14, SES-28
    tok_total: number | null; wall_ms: number; active_ms: number;
    time_split: TimeSplit;                                                  // SES-15/16 seven states with substates
    longest: Record<'model'|'tool'|'agents'|'you'|'agentmsg'|'limit'|'none',
                    { row: RowRef; turn: string; dur_ms: number } | null>,       // SES-27
    n_agents: number; n_skill_loads: number; n_failed: number; n_llm_calls: number;
    models: ModelLine[] } }
```

API-21 — `GET /api/sessions/{session}/turns?min_gap_ms=` [new] returns `TurnList`:
```ts
interface TurnRow extends Money {
  type: 'turn'; turn: string; display_id: string | null; kind: TurnKind; origin: string;
  label: string; start_ms: number | null; end_ms: number | null;
  wall_ms: number | null; time_split: TimeSplit | null; waiting_ms: number | null;   // span (D37; SES-21)
  cost_main: number | null; tok_total: number | null; n_llm_calls: number;
  models: { model: string; cost_usd: number | null }[];                              // main thread, by cost
  n_agents: number; n_skill_loads: number; n_failed: number;
  agent_runs: { engagement: string; agent_name: string | null; start_ms: number; end_ms: number }[] }  // SES-42 tails
interface GapRow extends Stretch { type: 'gap'; id: string }                         // id = gap-<start_ms>
{ freshness: Freshness, items: (TurnRow | GapRow)[] }
```
`min_gap_ms` defaults to 120000 (SES-16), and its minimum is 60000 (the stored floor). Harness turns have `display_id: null`. Their span fields are null when their start is null.

API-25 — `GET /api/sessions/{session}/find?kind=&measure=&text=&limit=` [new] returns `FindResult`:
```ts
{ freshness: Freshness, kind: string, total: number,
  hits: { row: RowRef; turn: string; display_id: string | null; depth: number;
          label: string; value: number | null; start_ms: number | null }[],
  per_turn: Record<string, number> }
```
`kind` is required (QRY-38). `measure` is `cost`, `time` or `tokens` (default `cost`) and applies to `biggest` only. `search` requires `text`. `limit` defaults to 2000 (max 10000), so a finder normally has every hit in one response. Turn-level `b` in the session blade ranks `TurnRow`s on the client (SES-52).

API-29 — `GET /api/sessions/{session}/resolve?id=` [new] returns `{freshness, row: RowRef, turn: string, depth: number}` (QRY-38a; SES-9).

API-33 [D56] — `GET /api/sessions/{session}/review` returns `{freshness} & Review` (QRY-42 `session_review`); `GET /api/explorer/review?<scope>` returns `{freshness} & Review` (QRY-42 `scope_review`, API-3 scope parameters). Unknown session → 404 `not_found`.

API-31 — `GET /api/sessions/{session}/histogram?bins=&from_ms=&to_ms=` [new] and `GET /api/turns/{turn}/histogram?…` [new] both return `Histogram` (QRY-32a):
```ts
{ freshness: Freshness, start_ms: number, bin_ms: number, tail_from_ms: number | null,   // turn only; null for sessions (D41 N1)
  bins: { cost_usd: number; main_cost_usd: number; tok_total: number; state_ms: TimeSplit;   // main_cost_usd: owned main-thread model calls starting in the bin (D47, SES-13)
          by_model?: Record<string, [number, number]> }[],     // turn only: [cost_usd, tok_total]
  marks: { turn_starts?: { turn: string; display_id: string | null; start_ms: number }[];
           skill_loads: { row: RowRef; start_ms: number; skill: string }[] },
  stretches: Stretch[] }
```
The histogram's visibility rule (TRN-20) is applied by the UI.

### 3.7 Turn blade

API-22 — `GET /api/turns/{turn}/rows?from_ms=&to_ms=` [new] returns `TurnRows`:
```ts
interface CallRow extends Money { type: 'call'; id: string; depth: number; start_ms: number; end_ms: number;
  dur_ms: number | null; time_prov: Provenance; model: string | null;
  tok: { in: number|null; cache_read: number|null; cache_write: number|null; out: number|null;
         reasoning: number|null; total: number|null };
  ttft_ms: number | null; stop_reason: string | null; error_kind: string | null; is_final: boolean;
  steps: { step_id: string; verb: 'think'|'say'|'answer'; summary: string; snippet: string }[] }  // TRN-31 name
interface ToolRow { type: 'tool'; id: string; depth: number; step_id: string | null; tool_name: string | null;
  verb: string; summary: string; target: string;
  command: string | null;        // stored 160-char preview (D39; D42)
  command_src: string | null;    // content reference to the full command; null when the tool has none (D42)
  file_path: string | null;
  start_ms: number; end_ms: number | null; dur_ms: number | null; time_prov: Provenance;
  status: string | null; failed: boolean; exit_code: number | null; is_error: boolean; interrupted: boolean;
  skill_load: string | null; skill_via: 'tool' | 'read' | 'slash' | null;   // "skill loaded" / "SKILL.md read"
  user_wait: 'ask' | 'permission' | null; max_silence_ms: number | null; result_bytes: number | null; src: string }
interface AgentRow extends Money { type: 'agent'; id: string; depth: number; caused_by: string;
  mode: 'started' | 'messaged'; description: string | null; agent_names: string[];
  agent_type: string | null; tok_total: number | null; n_llm_calls: number; n_tool_calls: number;
  n_errors: number; models: string[]; run_ms: number; n_rows: number;
  runs: { engagement: string; agent_name: string | null; start_ms: number; end_ms: number; wall_ms: number }[] }
{ freshness: Freshness,
  turn: TurnRow & { cost: Money & { main: number | null; agents: number | null }; models_full: ModelLine[];
                    text_src: string; text_bytes: number | null; n_turns_in_session: number },
  rows: (CallRow | ToolRow | AgentRow | GapRow)[],          // As it ran, depth 0
  range: null | { from_ms: number; to_ms: number; cost: Money; cost_main: number | null; cost_agents: number | null;
                  time_split: TimeSplit;
                  agents: { row: string; cost_usd: number | null; tok_total: number | null;
                            n_calls_in_range: number; n_calls: number }[] } }   // TRN-24, TRN-33
```
`{turn}` must be a main turn; any other op is a 400. `from_ms`/`to_ms` must be given together.

API-32 — `GET /api/ops/{engagement}/rows?offset=&limit=` [new] returns `{freshness, total, offset, limit, rows: (CallRow | ToolRow | AgentRow | GapRow)[]}`. These are the rows of the agent row that contains `{engagement}` (QRY-32; TRN-35).

### 3.8 Detail blade

API-24 — `GET /api/ops/{op}/detail` [new] returns `Detail`, the QRY-35 union, with `kind` as the discriminant:
```ts
interface Position { session: string; turn: string | null; display_id: string | null; row: RowRef; depth: number }
type Detail =
  | ({ freshness: Freshness; kind: 'call'; position: Position; turn_cost_usd: number | null;
       cost_in_usd: number|null; cost_out_usd: number|null; cost_cache_read_usd: number|null;
       cost_cache_write_usd: number|null; cost_basis: string | null; context_tokens: number | null;
       speed_tok_s: number | null; speed_basis: Provenance;
       sections: { part: 'thinking' | 'text' | 'tool_use'; src: string; snippet: string;
                   bytes: number | null; row: RowRef | null }[] } & Omit<CallRow, 'type'>)
  | ({ freshness: Freshness; kind: 'tool'; position: Position; input_src: string; output_src: string;
       neighbours: { before: (CallRow|ToolRow|AgentRow|GapRow)[]; after: (CallRow|ToolRow|AgentRow|GapRow)[] } }
     & Omit<ToolRow, 'type'>)
  | ({ freshness: Freshness; kind: 'agent'; position: Position; turn_cost_usd: number | null;
       focus_engagement: string;
       runs: { engagement: string; agent_name: string | null; start_ms: number; end_ms: number;
               wall_ms: number; ask_src: string | null }[];
       top_calls: CallRow[]; other_calls: { n: number; cost_usd: number | null } } & Omit<AgentRow, 'type'|'runs'>)
  | { freshness: Freshness; kind: 'message'; position: Position; turn: string; display_id: string | null;
      turn_kind: TurnKind; turn_origin: string; label: string; text_src: string; text_bytes: number | null }
```

### 3.9 Lazy full text

API-26 — `GET /api/source?src=&part=&offset=&limit=` [changed] returns `SourceResponse`: `{freshness, text, total_bytes, drift, file, locator, block}`.
- Two changes:
  - The frontend client now passes `part` (`input|output|thinking|text`). The server already accepts it (spec-inventory §2).
  - The new field `block` is the JSON `type` of the content block at the pointer (for example `thinking`, `redacted_thinking`, `reasoning`, `text`, `tool_result`) or null. DET-64 needs it to tell "redacted" from "empty"; DET-63 needs it to recognise Codex reasoning.
- For a Codex reasoning item, `part=thinking` returns the plaintext summary when one exists and `""` otherwise. The encrypted content is never returned (DET-63).
- `limit` defaults to 65536; the max is 1048576. The detail blade asks for 262144 on a section's first fetch (DET-65).
- A missing source file returns 404 `source_missing` (design §9).
- Command output head and tail (DET-24, DET-65): request `offset=0`. When `total_bytes` is larger than the bytes returned, request the tail with `offset = total_bytes − 16384`.
- The agent ask and the turn text use `part=input` on `ask_src` and `part=text` on `text_src`.

  Scenario: Given a tool call whose result is 11 KB, when the client requests `output_src` with `part=output`, then `text` holds the whole output, `total_bytes` is its size, and `block = 'tool_result'`.

### 3.10 Changed listings kept for agents

API-27 — `GET /api/sessions` [changed]: adds `offset` (default 0) and returns `total` beside `sessions`; `limit` keeps default 200, max 1000. Rows gain `name`, `title_source`, `project_name`, `is_automated` (from S2 columns). This fixes "caps at 200 with no total" (brief).

API-28 — `GET /api/projects` [changed]: rows gain `project_name` (DM-60), `n_automated`, and `color_slot` (QRY-14). `n_sessions` and `n_automated` count only sessions with `n_llm_calls > 0` (DM-60 counting rule, the DM-54 membership rule); a project with `n_sessions = 0` is still listed (D41 N2).

### 3.11 Assets

API-41 — Non-`/api/` GET paths serve the built UI: an existing file under the asset directory is served with its content type; any other path returns `index.html` (SPA fallback). Files under `assets/` (hashed names) get `Cache-Control: public, max-age=31536000, immutable`; `index.html` gets `no-cache`. With no asset directory, non-API paths return 404 JSON `{"error": "UI assets not found; build the frontend (make build)", "code": "not_found"}` and the API keeps working.

## 4. Performance

Measured on the real store (`~/.old-profiler/profiler.sqlite`, schema 6, 1.57 GB, 566,761 ops, 1,622 sessions) with `sqlite3 -readonly` and read-only `botsworth.query` calls, 2026-10-12, warm page cache, on the maintainer's machine. No ingest was run. v7 columns (`attr_turn_rowid`, `display_ix`; D38 L2) do not exist yet, so turn-attribution timings use today's `parent_rowid` and `caused_by` paths as the stand-in.

| Query shape (today's tables) | Rows | Time |
|---|---|---|
| Day × project series, 21-day window, `rollup_time` ⋈ `sessions ON op_rowid` | 149 | **1.20 s** (plan scans `sessions` per row: no index on `sessions.op_rowid`) |
| Same, aggregated by root first, then joined | 149 | 0.002 s |
| In-period sessions (7 d) via day rows, full-figure totals (v7 measurement; D50 re-measures on 15-minute buckets) | 187 sessions | < 0.001 s |
| Same, all time | 1,524 | 0.044 s |
| Donut by project (7 d) | 43 | 0.001 s |
| Session page sorted by cost (7 d) | 5 | 0.001 s |
| Models per session, all time, from `rollup_time` | 1,524 | 0.003 s |
| Distinct agents per session, all sessions | 441 | 0.167 s |
| All-time search, main-turn labels `instr` | 45 sessions | 0.027 s |
| Largest session b2c3d4e6 (8,386 calls): per-model lines | 3 | 0.085 s |
| Same session: failures / longest tools / step search | 111 / 20 / 2,722 | 0.095 / 0.005 / 0.062 s |
| Largest turn (omp, 207 calls): tool_calls under its calls, planner picks `ops_kind_cost` | 319 | **0.91–2.35 s** |
| Same, forced to `parent_rowid` index (`+kind`) | 319 | 0.001 s |
| c3d4e5f7 `main#2`: calls, steps, caused engagements | 178 / 290 / 46 | 0.013 / 0.013 / 0.046 s |
| `source()` at line 6,065 of a 13.4 MB JSONL | 1 | 0.016 s |
| `sessions(limit=200)` (today's API) | 200 | 0.111 s |
| All-scope top-20 llm_calls by cost / tool_calls by dur | 20 | 0.38 / 0.16 s |

### 4.1 Targets

API-50 — Latency targets, p95, warm cache, real store, measured at the HTTP layer (request to last byte):

| Endpoint | Target | Basis |
|---|---|---|
| `/api/ingest/status` | 50 ms | meta reads and the lock file |
| `/api/explorer/summary` | 150 ms (all time: 300 ms) | day-row membership < 50 ms; search adds ≤ 30 ms per term |
| `/api/explorer/days` (≤ 92 days) | 150 ms | root-first aggregation 2 ms |
| `/api/explorer/sessions` (200 rows, bins and day split) | 200 ms | sort < 50 ms; per-page agents and day split by root |
| `/api/explorer/facets` | 200 ms | three "except own" scopes |
| `/api/palette` | 150 ms | all-time search 27 ms |
| `/api/sessions/{s}` | 200 ms on b2c3d4e6 | model lines 85 ms |
| `/api/sessions/{s}/turns` | 150 ms (216 turns, the store maximum) | indexed by root |
| `/api/turns/{t}/rows` | 200 ms on the largest turn (540 calls is the store maximum) | parent index 1 ms per level |
| `/api/turns/{t}/histogram`, `/api/sessions/{s}/histogram` | 150 ms; 400 ms on b2c3d4e6 (8,386 calls, state lane computed per request) | needs `ops_attr_turn`; not measured |
| `/api/ops/{e}/rows` | 200 ms per 500 rows | parent index |
| `/api/sessions/{s}/resolve` | 50 ms | `ops_op_id`, `owner_key` |
| `/api/ops/{op}/detail` | 100 ms (agent kind: 200 ms) | point lookups |
| `/api/source` | 100 ms for files ≤ 20 MB | 16 ms measured |
| `/api/sessions/{s}/find` | 300 ms on b2c3d4e6 | step search 62 ms |
| `POST /api/query` all-scope hotspots | no target in v4 | 5.8–8.2 s today (deferred.md T8); the v4 UI does not call it |

### 4.2 Indexes and rollups S2 must provide

API-51 — Indexes and rollups that the measurements above require. This list gives index names and the measurement behind each. The DDL is only in data-model.md §2.2 (D38 M1):
1. `sessions_op` (UNIQUE) — the day series join drops from 1.20 s to 2 ms.
2. `ops_parent_kind_start` — children by kind under a turn or call; today the planner picks `ops_kind_cost` and scans every tool_call (0.91–2.35 s).
3. `ops_caused_by` (predicate `caused_by IS NOT NULL`, as in data-model.md §2.2; D38 M1) — agent rows and agent details.
4. `ANALYZE` (or `PRAGMA optimize`) at the end of every ingest — the store has no `sqlite_stat1` today, which is why the planner chooses the low-selectivity `kind` indexes.
5. `rollup_bucket` (D50; replaces `rollup_day_llm`), the index for membership (QRY-5) and day sums. It is defined in data-model.md §2.2.

Already in S2 and required here: `ops_attr_turn` (histogram, agent rows, finders), the `stretches` root index (gaps, D37), `steps_skill_load` (skill finder), `sessions_automated_time` (automated rule), the `projects` table (names), `sessions.activity_*` (strips), and `rollup_time.n_unpriced` (exists today).

Not required: a stored `search_text` (query-time search measured at 27 ms), a stored `n_agents` (per-page computation; 167 ms for all 1,622 sessions at once), a stored `color_slot` (QRY-14, 2 ms).

API-52 — Payload bounds (estimated from row sizes, not measured): a 200-row session page with 96-bin strips is about 150 KB of JSON; a turn's rows payload on the largest turn (540 calls, ~6,000 steps) is about 1.5 MB. Responses over 32 KB are sent with `Content-Encoding: gzip` when the request's `Accept-Encoding` includes `gzip`.

## 5. CLI (`botsworth`)

CLI-1 — One entry point `botsworth` (pyproject `[project.scripts]`; D35). Subcommands: `ingest`, `serve`, `paths` (prints the resolved data folder and store file), and the agent query commands below. The separate `botsworth-http` entry (server.py `main`) is removed; its options move to `serve`.

CLI-2 — Global flags: `--db PATH` (D20 precedence unchanged). Output: **JSON by default** for every read command; `--table` prints a human table of at most 2 KB with a `... N more lines (use --json)` tail (D35). This changes today's default, which is a table for every read except `query` (cli/main.py:216) [changed]. `--json` stays accepted as a no-op.

CLI-3 — JSON output of a command equals the body of the matching HTTP endpoint (same function, same dataclass, same keys; QRY-20), printed as one line, UTF-8, followed by a newline. Agents may rely on this equality.

CLI-4 — Exit codes:

| Code | Meaning | stderr |
|---|---|---|
| 0 | success (including empty results) | nothing |
| 1 | the query layer rejected the request: bad argument value, unknown or ambiguous id (`NotFound`, QRY-6), source file missing, ingest already running (`IngestLocked`) | `error: <message>` |
| 2 | usage error (argparse), no store (D22), schema mismatch (QRY-22) | `error: <message>` naming `botsworth ingest` where relevant |
| 3 | `serve` could not bind its port | `error: port <n> in use` |
| 130 | interrupted (Ctrl-C) | nothing |

### 5.1 Commands

| Command | Arguments | Function / output | Status |
|---|---|---|---|
| `ingest` | `--provider` (repeatable), `--force`, `--reprice`, `--rebuild` | pipeline run; JSON run report. `--rebuild` builds a new store from the provider logs and renames it over the old one (D52); not with `--provider`, exit 2. [changed] `--stream` is removed. | [exists] |
| `serve` | `--host` (default 127.0.0.1), `--port` (default 8420), `--ui DIR`, `--open` | starts the HTTP server (CLI-10) | [new] |
| `status` | — | `ingest_state` | [new] (today only HTTP) |
| `period` | scope flags (CLI-5) | `period_summary` | [new] |
| `days` | scope flags, `--window-from`, `--window-to` | `day_series` | [new] |
| `sessions` | scope flags, `--sort`, `--dir`, `--offset`, `-n/--limit` (default 20), `--group` | `list_sessions` | [changed]: today `-n` only |
| `facets` | scope flags | `facets` | [new] |
| `search` | `TEXT`, scope flags | `palette` | [new] |
| `session` | `SESSION` | `session_detail` | [new] |
| `turns` | `SESSION`, `--min-gap` (duration, default 2m) | `session_turns` | [new] |
| `turn` | `TURN` (op id or `<session>:T12`), `--from-ms`, `--to-ms` | `turn_rows` | [new] |
| `detail` | `ID` (op id, step id or display id) | `detail` | [new] |
| `find` | `SESSION`, `--kind`, `--measure`, `--text`, `-n` | `find` | [new] |
| `outline`, `query`, `top`, `children`, `show`, `steps`, `timeline`, `source`, `stats`, `outliers`, `trend`, `limits`, `skills` | as today (spec-inventory §3) | as today | [exists]; `outline`/`steps`/`show` display ids per QRY-2 [changed] |
| `slow`, `breakdown`, `tasks` | §5.2 | §5.2 | [new] |

CLI-5 [D50] — Scope flags (shared): `--tz ZONE` (IANA name; unknown → exit 2); `--from DAY --to DAY` or `--since <n>d|w` (days back from today in the CLI zone, inclusive of today; `--since 1d` = today; the CLI zone is `--tz` or, when absent, the machine's zone, and the CLI sends it as `tz`) [D50] or neither (all time); `--q TEXT`; `--provider`, `--model`, `--branch`, `--project` (each repeatable); `--automated` (include automated sessions). `--since` with `--from`/`--to` is a usage error (exit 2). The `<n>h` form of today's `--since` remains only on the existing commands that take it.
  Scenario: Given the machine zone Asia/Kolkata on 2026-10-12 (or `--tz Asia/Kolkata`), when an agent runs `botsworth sessions --since 7d --sort cost -n 5`, then stdout is one JSON object with `total` = the in-scope count for 2026-10-06 – 2026-10-12 and 5 `rows` ordered by full cost, and the exit code is 0.

CLI-10 — `serve`: binds `--host:--port`; prints `botsworth UI on http://<host>:<port>` to stderr; serves the API and the UI assets. Asset directory, first found: `--ui DIR`, env `BOTSWORTH_UI_DIR`, the package's bundled `botsworth/http/ui/` (the build copies `frontend/dist` there), the repo's `frontend/dist`. With none found, it logs one warning and serves the API only (API-41). `--open` opens the browser after binding. `serve` never runs ingest (D5) and holds no write connection.

CLI-11 — Stability promise: within `api_version` 1, fields are only added, never renamed, removed or changed in meaning or unit; agents must ignore unknown fields. A breaking change bumps `api_version` (in `freshness`) and is listed in the release notes. `--table` output, stderr text and the order of keys are not stable. Command names and flags listed here are stable within `api_version` 1.

### 5.2 Use-case commands

Added by the M2 architect, 2026-10-12 (PLAN.md E2; APPROVAL: overnight build). The §5.1 commands answer the maintainer's three agent questions only through several calls and client-side arithmetic: no command gives a project's time split, its slowest tools, or tokens grouped by task. These three commands answer each question in one call. They return facts only: numbers, ids, labels and stored previews. They return no generated prose, no ranking words ("bottleneck", "anomaly") and no causes. Each is one query-layer function in `botsworth.query.views.usecases`, re-exported from `botsworth.query`. Their JSON output is `dataclasses.asdict` of the returned dataclass (CLI-3 applies with the Python function in place of an HTTP endpoint; there is no HTTP route in v4).

| Command | Arguments | Function / output | Question |
|---|---|---|---|
| `slow` | scope flags (CLI-5), `--compare` / `--no-compare` (default compare), `-n` (default 10, max 100) | `project_slow(scope, compare, n)` → `SlowReport` | "Why was project X slow last week?" |
| `breakdown` | `SESSION`, `-n` (default 10, max 100) | `session_breakdown(session, n)` → `SessionBreakdown` | "What are the slowest parts of session S? Where did the time and tokens go?" |
| `tasks` | scope flags, `--by command_key\|command_class\|tool\|verb\|skill\|agent_type\|slash` (required), `--measure tokens\|cost\|time\|count` (default tokens), `-n` (default 20, max 500) | `top_tasks(scope, by, measure, n)` → `TaskGroups` | "Which common tasks use the most tokens?" |

CLI-20 — `project_slow(scope, compare, n)`. The sessions are the in-scope sessions (QRY-3–QRY-8). Every figure uses full sessions (QRY-9): a session that crosses the period boundary counts whole, the same rule as the explorer list.
- `current`: a `SlowBlock` = `{n_sessions, wall_ms, active_ms, time_split: TimeSplit, n_llm_calls, n_tool_calls, tok_total, cost: Money}`. `time_split` is Σ of the sessions' DM-51 parts per (state, substate), so it sums to `wall_ms`.
- `previous`: the same block for the period of the same number of days that ends the day before `from_day`, under the same filters. It is null when `compare` is false or the scope is all time.
- `sessions`: the `n` sessions with the largest `wall_ms`, each `{session, session_id, name, project_name, start_ms, wall_ms, active_ms, time_split, cost: Money, tok_total, longest_wait: Wait | null}`. `longest_wait` is the session's longest wait (below) of state `you`, `agentmsg`, `limit` or `none` [D54] (not `agents` or `tool`/`background`, which are running time [D55]).
- `tools`: owned tool_calls of the sessions with `user_wait` NULL, grouped by (`tool_name`, `command_key`). `command_key` is null for tools without a command. Each group is `{tool_name, command_key, n, n_sessions, dur_ms, p50_ms, p90_ms, max_ms, n_derived, n_no_records}`, where `dur_ms` = Σ `dur_ms`, `n_derived` = calls with `time_prov = 'derived'`, and `n_no_records` = calls with `max_silence_ms ≥ 300000` (DM-40). Percentiles are nearest-rank over non-null `dur_ms`. The `n` groups with the largest `dur_ms` are listed.
- `slowest_tools`: the `n` owned tool_calls with the largest `dur_ms` and `user_wait` NULL, each `{row: RowRef, session, turn, display_id, tool_name, command_key, summary, dur_ms, time_prov, max_silence_ms}`. `summary` is the stored step summary (a 160-char preview, D39).
- `user_waits`: `{ask: {n, dur_ms}, permission: {n, dur_ms}}` over tool_calls with that `user_wait` (D54).
- `test_runs` [D55]: `TestRuns` = `{n, run_ms, n_failed, n_passed, n_no_result, n_background}` over the owned tool_calls with `command_class = 'test'` (DM-28). `run_ms` = Σ of each call's run: `bg_end_ms − start_ms` for a background run with `bg_end_ms`, else `dur_ms`. `n_failed` = runs with `tests_failed > 0`; `n_passed` = runs with `tests_failed = 0`; `n_no_result` = runs with both counts NULL (DM-29); `n_background` = runs with `bg_end_ms`. The groups of `tools` stay keyed by `command_key`.
- `waits` [D54]: the `n` longest waits across the sessions, as `Wait` plus `session` and `display_id` of the wait's turn, ordered by `dur_ms` descending, then session, then `start_ms`. A wait is a stretch of 2 min or more (every state), or a main-thread tool_call with `user_wait` set and `dur_ms` of 2 min or more; per session this is the hit set of the `waits` finder (QRY-38). `Wait` has the `Stretch` fields plus `row: RowRef | null`. For a stretch, `row` is null. For a call, `row` is the tool row, `start_ms`, `end_ms` and `dur_ms` are the call's, `state` is `you`, `substate` is its `user_wait`, `turn` is its attributed turn, `next_turn` is null and `running` is empty.
- `models`: `ModelLine[]` over the sessions' owned llm_calls (QRY-33).
- `review` [D56]: `scope_review(scope)` (QRY-42) without `freshness`, over the `current` sessions.

  Scenario: Given the fixture project with two sessions in the period (walls 60 min and 30 min, a 25-min `none` stretch before a human ask, and one 40-min derived Bash call with a 30-min silence), when `profiler slow --project <p> --from D --to D` runs, then `current.wall_ms` = 5,400,000, `current.time_split` sums to it, `current.time_split` has a `you`/`reply` part that includes 1,500,000, and `slowest_tools[0]` is the Bash call with `time_prov = 'derived'` and `max_silence_ms = 1,800,000`.

CLI-21 — `session_breakdown(session, n)`. One session (QRY-6 lookup):
- `session`: `{session, session_id, name, project_name, provider, start_ms, end_ms, wall_ms, active_ms, cost: Money, tok_total, time_split}`. The values equal `session_detail`'s (QRY-29).
- `turns_by_time` and `turns_by_tokens`: the `n` numbered turns (harness turns excluded) with the largest `wall_ms` and the largest `tok_total`, as `TurnRow` (QRY-30).
- `rows_by_time`, `rows_by_cost` and `rows_by_tokens`: the first `n` hits of `find(session, 'biggest', measure)` for `time`, `cost` and `tokens` (QRY-38), with the same hit shape.
- `tools`: the CLI-20 tool groups over this session only.
- `agents`: the `n` agent rows (QRY-34, any depth) with the largest `cost_usd`, each `{row, turn, display_id, depth, agent_names, cost_usd, cost_lower_bound, n_unpriced, tok_total, run_ms, n_llm_calls}`.
- `waits` [D54]: the `n` longest waits of the session (CLI-20), as `Wait`. Their `dur_ms` values are the `n` largest values of `find(session, 'waits')`.
- `test_runs` [D55]: the CLI-20 `TestRuns` over this session only.
- `review` [D56]: `session_review(session)` (QRY-42) without `freshness`.
- `--table` [D56]: CLI-20 and CLI-21 print `review` first (wall, lost, then one line per finding: cause, ms, n, longest), so the 2 KB table (CLI-2) shows it.
- `models`: the session's `ModelLine[]`.
- Invariant: every figure equals the one returned by `session_detail`, `session_turns` or `find` for the same session. This command composes those functions; it does not recompute their figures.

CLI-22 — `top_tasks(scope, by, measure, n)` groups the in-scope sessions' owned leaves by a task key. Each group is `{key, n, n_sessions, tok_total, cost_usd, cost_lower_bound, n_unpriced, dur_ms, p50_ms, p90_ms, share, example: {row: RowRef, session}}`. The response is `{freshness, by, measure, basis, scope_totals, unassigned, total_groups, groups}`. Groups are ordered by `measure` descending, then `key` ascending (`count` orders by `n`). `share` = the group's `measure` value ÷ `scope_totals` of the same measure, or null when that total is 0 or null. `basis` names how tokens reach a group (D42):

| `by` | Items counted (`n`) | Tokens and cost | `dur_ms` | `basis` |
|---|---|---|---|---|
| `tool`, `command_key`, `command_class` [D55] | owned tool_calls (`command_key` and `command_class` groups skip null keys) | the issuing llm_call's (the tool_call's parent) `tok_total` and `cost_usd`, divided evenly among that call's owned tool_calls | Σ tool `dur_ms` | `issuing_call_split` |
| `verb` | the steps whose `op_id` is an owned tool_call, keyed by `steps.verb` | as `tool`, per tool_call | Σ tool `dur_ms` | `issuing_call_split` |
| `skill` | skill loads (QRY-29 `n_skill_loads` rule) keyed by skill name | the attributed turn's `attr_tok_total` and `attr_cost_usd` (DM-45), divided evenly among the distinct skills loaded in that turn | the turn's `span_wall_ms`, divided the same way | `turn_split` |
| `agent_type` | engagements keyed by `agent_type` (null → `"(none)"`); owned llm_calls not under an engagement form the group `"(main)"` | Σ of owned llm_calls whose nearest engagement ancestor is in the group (nested agents count in their own group only) | Σ engagement `wall_ms` (`"(main)"`: null) | `nearest_engagement` |
| `slash` | main turns with `turn_origin` `slash` or `builtin:*`, keyed by the first word of `label` | the turn's `attr_tok_total` and `attr_cost_usd` | Σ `span_wall_ms` | `turn_attr` |

- `scope_totals` = `{tok_total, cost: Money, dur_ms}` over all owned llm_calls of the in-scope sessions (`dur_ms` = Σ session `wall_ms`). `unassigned` = `{tok_total, cost_usd}` = the part of `scope_totals` in no group (for `issuing_call_split`, an llm_call that issued no tool_call counts in the group `"no tool"`, so `unassigned` is 0 there; D42). For the `issuing_call_split` and `nearest_engagement` rules, Σ groups + `unassigned` = `scope_totals` (a partition). For `turn_split` and `turn_attr`, it is ≤.
- `total_groups` counts every group. `groups` holds the first `n`.
- A `by` value outside the table is a usage error (exit 2).

  Scenario: Given a call with 1,000 tokens that issued two Bash `uv run pytest` calls and one Read, when `botsworth tasks --by command_key` runs, then the `uv run pytest` group holds 666.67 tokens from that call (2 × 1,000 ÷ 3), the 333.33 tokens that went to the Read (Read has no `command_key`) are in `unassigned`, and `basis` = `issuing_call_split` (D42). Under `--by tool` the Read group holds them, and a call that issued no tool sits in `"no tool"`.

CLI-23 — Output size: each use-case command's JSON for `-n 10` on the real v7 store is under 64 KB, so an agent reads it in one tool call. With `--table` it prints the CLI-2 table.

CLI-24 — Latency on the real v7 store, p95 warm: `slow --since 7d --project <largest project>` ≤ 1.5 s; `breakdown b2c3d4e6` ≤ 1.5 s; `tasks --since 30d --by command_key` ≤ 2 s. Measured by Q4 (TST-5). No target in `make check`.

TST-7 — Use-case tests on the TST-1 fixture, one per rule: CLI-20's scenario; `previous` is null for all time; calls with `user_wait` set are not in `tools` or `slowest_tools`; CLI-21's invariant against `session_detail`, `session_turns` and `find`; CLI-22's scenario; the partition for `issuing_call_split` and `nearest_engagement`; `skill` splits a turn that loaded two skills evenly; `slash` keys `/mad:bootstrap ARGS` as `/mad:bootstrap`; `--by bogus` → exit 2; the JSON output equals `asdict` of the function's result.

## 6. Frontend wiring

FE-1 — `frontend/src/api/types.ts` declares every wire type of §3 with the names used there, and keeps the existing types that other endpoints still return. T8 commit `ce62cc3` is the base (its `Freshness` with `last_ingest_ms: number | null`, `IngestStatus.store_exists`, `SessionsRow`, WIRE_EXTRA fields); its `lib/sessionTitle.ts` is not reused, because the server now supplies `name` (SYS-30: "the UI never builds a name from raw text").

FE-2 — `frontend/src/api/client.ts` exposes one method per endpoint of §3.2 that the UI uses, each taking an `AbortSignal` as its last argument and returning the typed body. Scope parameters are serialised by one function `scopeParams(scope)` that writes repeated keys (API-2, API-3) and omits defaults. `source()` gains `part` (API-26).

FE-3 — No mock data in the product (EXP-83; D22 narrowed by Kai 2026-10-12). `resolveClient()` returns the HTTP client. The mock client is loaded only when `import.meta.env.DEV` is true and `VITE_BOTSWORTH_MOCK=1`; the product build contains no mock module.
  Verify: `grep -r createMockClient frontend/dist` finds nothing after `npm run build`.

FE-4 — Data fetching uses one hook, `useApi(key, fetcher)`, which replaces `lib/useAsync.ts`:
- `key` is the request URL (method + path + sorted query string); equal keys share one in-flight request and one cache entry;
- a key change or unmount aborts the in-flight request (`AbortController`); an aborted request never sets state or shows an error;
- while a new key loads, the hook returns the previous key's data with `stale: true`, so regions keep their content (EXP-78); only a first load with no previous data returns `data: undefined` (skeletons, EXP-77, SYS-54);
- results are cached in memory (LRU, 200 entries) per key; a cached result is shown at once and revalidated with `If-None-Match` (API-6);
- no optimistic updates: the API is read-only.

FE-5 — Search typing (EXP-19 "results update while typing"): the URL updates on every keystroke; requests use the search text debounced by 150 ms; each new request aborts the previous one (FE-4).

FE-6 — URL ↔ queries: the URL (EXP-74, EXP-76 and S1b's blade parameters) is the only source of scope state. A parser maps the URL to `{scope, measure, sort, dir, group}`, dropping unknown values (EXP-75); the explorer derives every request key from that value. The measure (EXP-31) is not a request parameter: summary and days return both measures.

FE-7 — Store changes: every response carries `freshness.last_ingest_ms`. When a data response that the user caused (navigation, scope change, Retry) shows a value different from the one the cache was filled under, the hook clears every other cache entry; the session list refetches from offset 0 (QRY-16). The app polls `/api/ingest/status` every 60 s and when the window regains focus. The poll updates only the freshness text (EXP-8) and the SYS-57a banner. It never clears the cache and never refetches visible data, even when the poll returns a new `last_ingest_ms` (D37; D38 H3).

FE-8 — Error and state mapping (SYS-54–57, EXP-77–83):

| Condition | Detected by | UI state |
|---|---|---|
| first load | `data === undefined`, no error | skeletons (SYS-54) |
| scope change | `stale: true` | previous content, 120 ms fade on arrival (EXP-78) |
| no store | status `store_state: 'missing'`, or any 503 with `code: 'no_store'` | SYS-57 banner; regions show "No data yet." (EXP-82) |
| schema mismatch | `store_state: 'schema_mismatch'` or 503 `schema_mismatch` | the SYS-57 banner reading "Store needs a rebuild." with the `botsworth ingest` chip (D37) |
| ingest running | status `ingest.running` | neutral banner "Ingest running — figures may be partial" (D37). The banner clears on the first status response with `ingest.running = false`, with no data refetch (D38 H3) |
| request failed (4xx/5xx) | `ApiError` | the region's error card with the server `error` text and Retry (SYS-56, EXP-81) |
| server unreachable | `fetch` rejects with `TypeError` | every region: "Couldn't reach the Botsworth server" + Retry (EXP-83) |
| aborted | `AbortError` | nothing |

FE-9 [D50] — Period resolution: the client resolves the URL's period (preset or custom) into `from`/`to` Days using today's date in the browser zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`) and sends that zone as `tz` on every explorer request (QRY-4, API-3). It no longer waits for `/api/ingest/status` to learn a zone. The chart window (EXP-32) is computed the same way and sent as `window_from`/`window_to`.

FE-10 — Palette dates: the palette parses date forms client-side (EXP-69 item 2, the same parser as EXP-15) and requests `/api/explorer/days` with `window_from = window_to = <day>` (or the range) under the current filters for the row facts (EXP-70). Sessions, projects and filter values come from `/api/palette` with a 100 ms debounce.

FE-11 — Dev vs product: `make dev` runs Vite with `/api` proxied to `botsworth serve` on :8420. The product is `botsworth serve` alone, serving the built assets (CLI-10). Both use the same client and the same endpoints; dev differs only in hot reload and the opt-in mock (FE-3).

## 7. Testing contract

TST-1 [D50] — Fixture store: the backend tests build a store from a canonical-stream fixture that contains at least: a session spanning two local days ($3 day 1, $5 day 2; DM-42 test), an automated session (one `sdk` turn), a session with an unpriced call, a session with zero llm_calls, two projects with colliding last components, a main turn with an agent across two engagements and a `+cycle` reply (DM-45 test), harness turns, an idle stretch crossing a turn start (DM-52 test), a failed Bash call, a skill load, and a derived tool duration with a silence ≥ 5 min. [D50] There is no stored zone; the fixture's tests name the request zone (`Asia/Kolkata` unless a test says otherwise).

TST-2 — Per endpoint, the backend tests prove:
- **Shape**: the JSON key set of every object equals the wire type's field list (the existing `shapes.py` key-set pattern, extended to the §3 types), and nullability matches (a non-null field is never null on the fixture).
- **Errors**: a bad parameter → 400 `bad_request`; unknown id → 404 `not_found`; no store → 503 `no_store`; a v6 store → 503 `schema_mismatch`; `/api/ingest/status` → 200 in all three store states.
- **Caching**: a repeated request with the ETag → 304; `/api/source` and `/api/ingest/status` carry `no-store`; while the lock file names a live pid, data routes carry `no-store`.
- **CLI parity**: for each command in §5.1 marked new or changed, `botsworth <cmd> --json` stdout parses to the same object as the HTTP body for the same arguments (CLI-3), and the exit codes of CLI-4 occur for their causes.

TST-3 — Cost invariants (each a test on the fixture and an acceptance check on the real store):
1. Σ `SessionRow.cost_usd` over all pages = `PeriodSummary.totals.cost_usd` (QRY-23).
2. Σ `projects[].cost_usd` = `totals.cost_usd` with no project filter.
3. For every session, Σ of its `day_split` = its `cost_usd`; for the two-day fixture session, a one-day period lists it at 8.00 while that day's bar holds 5.00 (DM-42).
4. Σ `TurnRow.cost_usd` + `cost.unattributed` = `SessionDetail.session.cost.cost_usd`; `cost.main + cost.agents` = the same total.
5. Σ call rows + Σ agent rows = the turn's cost (QRY-31); an agent detail's `top_calls` + `other_calls` = its row cost; Σ session histogram `cost_usd` = the session cost; Σ a stretch-free turn's `time_split` = its span wall (D37).
6. `time_split` sums to `wall_ms` for the session (DM-51).
7. Duplicates never add: the fixture's copied calls change no figure.
8. A null-cost call sets `cost_lower_bound` and `n_unpriced` on its session, its project, its day and the totals.
9. Real store (acceptance, after the v7 ingest): `day_series` for 2026-10-07 with provider `claude_code` gives 1,847,233,004 tokens, $549.89 rounded, 18 sessions (DM-41; D28 case 1), with `show_automated = true` (D38 M7).

TST-8 [D50] — Zone tests (query layer, one fixture store ingested once; no test re-ingests to change a zone):
- (a) Zone from the request: the same `day_series` over one store, asked with `tz` = `Asia/Kolkata`, `UTC` and `America/New_York`, gives different day splits; sums over all days are equal.
- (b) Half-hour and 45-minute zones: with `tz = Asia/Kathmandu` (+5:45), a call at 18:14Z and a call at 18:15Z on the same UTC date fall on different local days; the day bar sums exactly the leaves whose `start_ms` is inside that local day.
- (c) DST: in `America/New_York`, the local day 2026-11-01 spans 25 h and 2026-03-08 spans 23 h; a call in each extra or missing hour is counted once, and local-hour edges (`bucket=hour`) give two buckets for the repeated hour.
- (d) Unknown zone: `tz=Not/AZone` gives `QueryError` in the query layer and HTTP 400 `bad_request` JSON; the CLI `--tz Not/AZone` exits 2.
- (e) Colour slots: the slots of QRY-14 for two zones differ only when the 30-day window edge moves a project across a rank boundary; with a fixture where no project crosses, the slots are equal.
- (f) CLI zone: `--since 7d` resolves "today" in `--tz`; with `--tz` absent it uses the machine zone (the test sets `TZ`); the HTTP query the CLI sends carries `tz`.
- (g) Ingest side: see DM-41 test (g) (data-model.md §4.1).
- (h) ETag: two requests that differ only in `tz` have different URLs and the same tag; `freshness` has no `tz` key (TST-2 shape test).

TST-4 — Scope rules: membership by llm_call day (EXP-1 scenario); automated hidden by default and counted in `n_automated_hidden`; "except own" facet counts (QRY-8); every-term search across fields, with `%` in the text matched literally; sort ties stable across page boundaries (pages are disjoint and their union is the whole set).

TST-5 — Latency smoke: `make bench-api` (not in `make check`) runs each endpoint of API-50 against the v7 store passed with `--db` (default `~/.old-profiler/v7/profiler.sqlite`, D39; D42) read-only, 20 times each, and fails when a p95 exceeds its target by more than 50 %. It skips with a message when the store is missing or its schema differs. It never runs ingest.

TST-6 — Frontend tests:
- `client.ts`: URL building (repeated keys, omitted defaults, encoded op ids), `part` on `source`, `ApiError` carrying `status` and `code`.
- `useApi`: abort on key change (the stale response does not render), previous data kept during reload, cache cleared when `last_ingest_ms` changes, 304 revalidation reuses the cached body.
- Error mapping: each FE-8 row renders its state.
- Fixtures: the backend test suite writes one golden JSON body per endpoint to `frontend/src/api/__fixtures__/`; a type-check-only test assigns each to its wire type (`const x: SessionPage = fixture`), so a renamed or missing field fails `tsc`.
- Product build has no mock module (FE-3).

## 8. Data needs mapped to fields

### 8.1 Explorer and system (S1a "needs S2/S3")

| Need (source) | Field | Endpoint |
|---|---|---|
| `display_title`, `title_source` (SYS-30, EXP §15) | `name`, `title_source` (QRY-13) | API-12, API-14, API-20 |
| project short name, colour slot (SYS-11, EXP §15) | `project_name`, `color_slot` (QRY-14) | every `ProjectRef` |
| `is_automated` (EXP-4) | `is_automated`; scope `auto` | API-12; API-3 |
| `n_agents` (EXP-52) | `SessionRow.n_agents` | API-12 |
| `models` used (EXP-22) | model filter (QRY-7) and `facets.models` | API-13 |
| `active_days`, `day_split` (EXP-1, EXP-3, EXP-40) | `SessionRow.day_split` (active days = its `day` values) | API-12 |
| `activity_bins` (SYS-40, EXP-53) | `SessionRow.activity`, `stats.max_activity_tok` | API-12 |
| `search_text` incl. ask snippets (EXP-19) | server-side `q` (QRY-7) | API-3 |
| N unpriced calls per item (SYS-32) | `n_unpriced` (QRY-11) | every `Money` |
| day × project totals under filters (EXP-33, EXP-37) | `days[].by_project` | API-11 |
| filter values with counts (EXP-21–23) | `Facets` | API-13 |
| all-time search count (EXP-79) | `empty.all_time_matches` | API-10 |
| nearest days with sessions (EXP-80) | `empty.prev_day`, `empty.next_day` | API-10 |
| per-day facts for date rows (EXP-70) | `days[]` (`n_sessions`, `cost_usd`, `tok_total`) | API-11 |
| "of 212", "N automated hidden" (EXP-12) | `n_sessions_period`, `n_automated_hidden` | API-10 |
| "All time · Sep 22 – Oct 12" (EXP-10) | `first_day`, `last_day` | API-10 |
| "+Nd" flag (EXP-52) | `ends_after_days` | API-12 |
| weight 600 at p90 (EXP-49) | `stats.cost_p90` | API-12 |
| group totals and strip (EXP-56, SYS-37) | `SessionGroup` | API-12 |
| `last_ingest_ms`, `store_exists` (EXP-8, SYS-57) | `freshness.last_ingest_ms`, `store_state` | API-30 |

### 8.2 Session, turn and detail (S1b: session.md §9, turn.md §8, detail.md §8)

| Need (S1b id) | Field | Endpoint |
|---|---|---|
| Session meta line, title, `+Nd` (SES-24, SES-25) | `name`, `title_source`, `project_name`, `start_ms`, `end_ms`, `ends_after_days`, `n_turns` | API-20 |
| Cost answer, main/agents split (SES-14, SES-26) | `cost.cost_usd`, `cost.main`, `cost.agents`, `cost_lower_bound` | API-20 |
| Time answer, seven states, legend (SES-15, SES-16, SES-26) | `time_split` | API-20 |
| Legend jumps (SES-27; D37) | `longest[state]` | API-20 |
| Details stat line, unattributed, model table (SES-28) | `tok_total`, `n_llm_calls`, `active_ms`, `n_agents`, `cost.unattributed`, `models` | API-20 |
| Over time: bins, state lane, turn ticks, skill pins, folds (SES-29) | `Histogram` (session) | API-31 |
| Turn row: kind and origin, number, label, flags, Cost, Wall, Waiting, Tokens (SES-34; SES-21 span, D37) | `TurnRow` | API-21 |
| Harness folds with cost (SES-35) | `kind = 'harness'`, `cost_usd` | API-21 |
| Gap rows and their labels and reasons; usage limit (SES-17, SES-18, SES-36) | `GapRow.state`, `GapRow.substate`, `next_turn` (origin and label; D37; D54) | API-21 |
| Waterfall agent tails (SES-42; D37) | `TurnRow.agent_runs` | API-21 |
| Search: matching rows per turn (SES-32, SES-34 item 4) | `find?kind=search` → `hits`, `per_turn` | API-25 |
| Finder pools `b w e s a` (SES-51–56) | `find?kind=…` | API-25 |
| Row id resolution (SES-9) | `resolve` | API-29 |
| Turn answers, details, "Full message" (TRN-4, TRN-5) | `TurnRows.turn` (`cost`, `time_split`, `models_full`, `text_bytes`) | API-22 |
| Rows: call, tool, agent, gap (TRN-30–34) | `CallRow`, `ToolRow`, `AgentRow`, `GapRow` | API-22 |
| Agents-only stretches with the engagements running (SES-19, TRN-34; D37) | `Stretch.state = 'agents'`, `running` | API-21, API-22, API-31 |
| An agent's rows, paged (TRN-35) | engagement rows | API-32 |
| Turn histogram by model, incl. caused agents (TRN-21, TRN-22) | `Histogram.bins[].by_model` | API-31 |
| Zoom: clock-rule totals, per-agent in-range figures, range split (TRN-4, TRN-24, TRN-33) | `TurnRows.range` | API-22 |
| Call detail (DET-10–14) | `Detail` `call`: `turn_cost_usd`, `tok`, `ttft_ms`, `stop_reason`, `speed_*`, `sections` | API-24 |
| Tool detail: duration flags, skill load, command, output (DET-20–24) | `Detail` `tool`: `time_prov`, `max_silence_ms`, `user_wait`, `skill_load`, `command`, `output_src`, `result_bytes` | API-24 |
| Agent detail (DET-30–34) | `Detail` `agent`: `runs[].ask_src`, `top_calls`, `other_calls`, `n_rows` | API-24 |
| Gap detail (DET-40–43; D37) | `GapRow` (`next_turn`, `running`) plus turn and session `wall_ms`; no endpoint | API-21, API-22 |
| Message detail (DET-50) | `Detail` `message`, `text_src`; URL-encoded as `RowRef` type `'turn'` (D38 L6) | API-24 |
| Lazy text, too large, Codex reasoning, redacted thinking (DET-60, DET-63–65) | `/api/source` with `part`, `limit`, and `block` | API-26 |

No field that S1b shows lacks a source.

## 9. Open questions

Closed by D37 (Kai, 2026-10-12):

| # | Ruling | Applied in |
|---|---|---|
| Q1 | [D50] One zone per request: `Scope.tz` (the browser zone for the UI, `--tz` or the machine zone for the CLI). The period popover names it. | QRY-4, FE-9 |
| Q2 | Four `title_source` values. SYS-30's "command label" level is dropped. | QRY-13 |
| Q3 | A full rebuild writes a new store file and swaps it in on success. During any ingest the UI shows "Ingest running — figures may be partial". | API-30, FE-8, API-6 |
| Q4 | A v6 store shows the SYS-57 banner "Store needs a rebuild." | FE-8 |
| Q5, Q9, Q11 | Accepted as recommended. | QRY-28, API-11, FE-10 |
| Q6 | Stretches carry a `state` (`none` or `agents`). The model and tool legend jumps are ruled under Q12. | QRY-29, QRY-41 |
| Q7 | Gap detail has no endpoint. Agents stretches list the engagements running. | QRY-35, QRY-41 |
| Q8 | No automatic refetch when an ingest finishes. | FE-7 |
| Q10 | The session-viz CLAUDE.md conventions apply. The build plan adds `old-profiler/CLAUDE.md`. | all |
| Q12 | The model and tool legend jumps follow SES-27: they go to the longest row. D37's "turn with the largest share" no longer applies to these two states (Kai 2026-10-12). D38 H2 extends this to every state: there is no "largest share" rule, and every legend jump goes to the longest row or stretch of that state, at 2 min or more for wait states. | QRY-29 |
| Q13 | Use S2's names: `span_wall_ms`, `span_model_ms`, `span_tool_ms`, `span_agents_ms`, `span_none_ms` (DM-53), and `stretches.state` (DM-52) (Kai 2026-10-12). | QRY-30, QRY-41 |

Still open:

| # | Question | Recommendation | Affects |
|---|---|---|---|
| Q14 | Turn Waiting. DM-53 defines it as `span_none_ms`. SES-21 defines it as the time when nothing ran on the main thread, which is `span_agents_ms + span_none_ms`. | Follow SES-21, the UI source; the figure differs only in turns that have agents. Kai to confirm, and S2 to amend its DM-53 sentence. | QRY-30 | **Closed (Kai 2026-10-12): SES-21 wins; DM-53 fixed.**

## Sources

Decisions D1, D3, D5, D20–D22, D26, D28, D29, D31–D35 (the decision log); `deferred.md`; `evidence/spec-inventory.md` §2–§4; `docs/design/design.md` §4.7, §5, §6, §9; `docs/specs/data-model.md` (S2, all DM- items cited); `docs/specs/ui/system.md` and `explorer.md` (S1a); `prototype notes (not in this repo)/` `CONSOLIDATION-NOTES.md`, `REVIEW-v4-steps-r3.md`, `REVIEW-v4-detail.md`, `spike-steps-r3/extract.py`; code: `src/botsworth/query/__init__.py`, `query/shapes.py`, `http/server.py`, `cli/main.py`, `store/ingest.py`, `frontend/src/api/client.ts`, `types.ts`, `lib/useAsync.ts`; T8 commits `ce62cc3` (types.ts wire contract, `sessionTitle`) and `aadf58c` (fake query shapes); read-only timing queries on `~/.old-profiler/profiler.sqlite` (§4), 2026-10-12.
