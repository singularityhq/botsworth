# Spec S2 — Data model and capture for UI v4 and the agent CLI

Status: open questions resolved by Kai (S2, 2026-10-12). Delta on `docs/design/design.md` §4 (model), §7 (data) and §8 (ingestion), and on store schema v6 (`src/botsworth/store/schema.py`). Anything this spec does not mention keeps its current definition.

Reader: the implementer (adapters, store fold, pipeline). Interfaces (query functions, HTTP, CLI shapes) are spec S3 (`docs/specs/interfaces.md`); this spec names the stored fields S3 reads.

Every requirement has an id `DM-n`, a source (decision id or file), and a tag:
- **[v4]** required before the v4 UI can ship.
- **[later]** specified now, built after v4.

All counts below come from read-only SQL on `~/.old-profiler/profiler.sqlite` (schema 6, `meta.last_ingest_ms` = 1791803474380) or from `rg` over the source trees, run 2026-10-12. No ingest was run.

## 0. Terms

Existing terms (session, engagement, turn, llm_call, tool_call, step, event, own, incl, owned, `time_prov`) are as in design.md §4. New terms:

| Term | Meaning |
|---|---|
| **main turn** | A `turn` op with `agent_id IS NULL` (it belongs to the main transcript, not to an agent). |
| **turn kind** | One of `human`, `message`, `harness` (D34). See DM-20. |
| **human ask** | A main turn with turn kind `human`. |
| **automated session** | A session with no human ask (D32). |
| **request zone** [D50] | The IANA time zone (for example `Asia/Kolkata`) that a query names in `Scope.tz` (HTTP `tz`, CLI `--tz`). The store holds no zone; every local edge is computed at query time in this zone (DM-41). |
| **local day** [D50] | The calendar date of a timestamp in the request zone, from local midnight to the next local midnight (23 or 25 h on a DST change day). |
| **stretch** | A maximal time interval inside a session's wall in which one activity state holds. See DM-50. |
| **attributed turn** | The main turn that a leaf op's cost belongs to under the D34 cost rule. See DM-45. |

## 1. Current data: facts this spec relies on

| Fact | Count | Query or source |
|---|---|---|
| Ops by kind | session 1,622 · engagement 6,693 · turn 18,379 · llm_call 257,610 · tool_call 282,457 | `ops GROUP BY kind` |
| Sessions with no `title` | 795 of 1,622 (claude_code 737/1,103, codex 46/423, omp 12/96) | `sessions` |
| omp turns labelled `user prompt` | 818 of 975 | `ops.label` |
| Turns with empty label | claude_code 1,110, codex 152 | `ops.label` |
| Main-turn labels that are harness records (Claude) | `[Request interrupted…` 336, `/compact` 319, `/clear` 103, `/model` 11, `/copy` 14 … | label prefix scan |
| Main-turn labels that are messages (Claude) | `<mc-message…` 89, plugin and peer records (see §3.2) | label prefix scan |
| Claude records with `promptSource` | 1,055 of 1,108 main transcripts; values `typed` 3,867, `system` 3,399, `sdk` 597, `queued` 493 | `rg` over `~/.claude/projects` |
| Claude `origin.kind` | human 5,341, task-notification 2,255, peer 1,616, plugin 1,068, coordinator 200, auto-continuation 3 | `rg` |
| Claude `entrypoint` | `cli` 1,032,459 records, `sdk-cli` 30,386; all 356 `/private/tmp/helperbot` and all 58 `/private/tmp/helperbot2` sessions are `sdk-cli` | `rg` |
| omp user `message.attribution` | `user` 535, `agent` 763, absent 44 | `rg` over `~/.omp/agent/sessions` |
| Codex `session_meta` | `source`: cli 211, vscode 332, exec 105, subagent 181; `originator`: Codex Desktop 441, codex_cli_rs 322, codex_exec 120, codex-tui 114 | `rg` over `~/.codex/sessions` |
| `sessions.entrypoint`, `sessions.originator` | NULL on all 1,622 rows | `sessions` |
| `steps.skill` | NULL on all 536,820 rows | `steps` |
| `skill_invocation` events | claude tool 1,931 / read 440 / slash 515; codex read 795 / slash 67; omp read 894 / slash 58 | `events` |
| Claude slash events that are built-in commands | compact 165, clear 103, model 11, copy 14, effort 6 (of 515) | `events.fields.skill` |
| Codex `via=read` events by command program | `sed` 589, `cat` 83, `rg` 50, `nl` 22, `wc` 10, `git` 3, others | events joined to steps and `ops.command` |
| Claude llm_call ttft | 0 of 189,107 owned calls | `ops.ttft_ms` |
| llm_call `time_prov` | claude derived 186,105 / missing 3,002; codex derived 22,336; omp present 30,686 / derived 391 / missing 777 | `ops` |
| tool_call `time_prov` | derived 220,833 · present 45,341 · missing 40; over 1 h: 26 (20 derived) | `ops` |
| Tool calls over 1 h by tool | AskUserQuestion 14, omp `ask` 6, Bash 3, Read 1, Codex exec 2 | `ops` |
| Engagements with `wall_ms`/`active_ms` | 6,693 of 6,693 non-null | `ops` |
| Engagements with `caused_by_basis` `+cycle` | 123 (28 sessions, $83.96) | `ops` |
| Engagements with no cause (`none`) | 166 ($271.78) | `ops` |
| Owned llm_calls with no step whose `op_id` is the call | claude 36,568 ($1,884.17), codex 3,787 ($108.77), omp 5,764 ($36.20) | `steps.op_id` |
| Turns with null `incl_tok_total` | 3,774: 2,591 have no llm_call, 1,150 have only duplicate (copied) calls, 33 have calls with missing tokens | `ops` |
| Turns with null `start_ms`/`end_ms` | 198, all claude, all with 0 llm_calls and 0 tool_calls | `ops` |
| Owned llm_calls that start after `sessions.last_ms` | 39 in 2 sessions (d4e5f6a8: 35, by up to 383 s; c3d4e5f7: 4) | `ops` join `sessions` |
| Sessions with 0 owned llm_calls | claude 46, codex 52 | `sessions.n_llm_calls` |
| Cost totals | Σ owned llm_call cost = Σ `rollup_time` cost = Σ `sessions.incl_cost_usd` = $12,598.89 | three sums |
| Local-day check (request zone `Asia/Kolkata`) | 2026-10-07 claude_code 1,847,233,004 tokens, $549.89, 18 sessions; Oct 6 $301.49; Oct 8 $518.66 | `date(start_ms/1000,'unixepoch','localtime')` over owned llm_calls |
| Same, by UTC bucket (current `rollup_time`) | Oct 7 = 1.62B / $482 / 16 (ui-redesign-inputs.md) | — |

## 2. Schema v7 and migration (schema 8 since D50)

### 2.1 Migration policy

- **DM-1 [v4] [D50]** `SCHEMA_VERSION = 8` (7 until D50, which changes the `rollup_time` layout and drops `meta.tz`; the store is a cache, so v7 stores are rebuilt, DM-4). Source: CLAUDE.md "SQLite cache is disposable"; schema.py CONTRACT.
  Confirmed behaviour of the current code: `schema.ensure` drops every table in `TABLES` when `meta.schema_version` differs, including `source_files` (schema.py:197–205; v9 also drops the old `stream_files`, D52). `pipeline._run` then finds no `source_files` row for any file, so every bundle counts as changed and is rebuilt from provider files (pipeline.py:156, `known.get(p) is None`). The first v8 ingest is therefore a full rebuild (about 225 s on the current corpus, deferred.md "From T11").
  Test: open an empty file with `ensure`; `meta.schema_version` = 8, all tables empty; `pipeline.run` on a fixture root rebuilds every bundle (`rebuilt` = `discovered`).
- **DM-4 [v4]** Full rebuild by swap (D37). When the store's `meta.schema_version` differs from `SCHEMA_VERSION` (or the store does not exist), the ingest does not drop tables in place. It builds a new store file `<db>.rebuild` (removed first if left over) under the ingest lock, runs the whole pipeline into it, checkpoints and closes it (no `-wal`/`-shm` left), then replaces `<db>` with it by `os.replace` and removes the old `<db>-wal`/`<db>-shm`. On any failure the new file is deleted and `<db>` is untouched. Readers therefore see either the complete old store or the complete new one, never dropped tables. While the old store is v6, readers follow DM-3. Tests: a pipeline that raises midway leaves `<db>` byte-identical and no `<db>.rebuild`; a successful run leaves `meta.schema_version` = 8 at `<db>`; a reader connection opened during the build reads the old store without error.
- **DM-5 [v4]** `ANALYZE` runs on the store after every ingest that changed any bundle, and after the DM-4 swap (D37: S3 timings). Test: after ingest, `sqlite_stat1` has rows for `ops` and `sessions`.
- **DM-2 [v4]** `pipeline.PARSER_VERSION` becomes `"2"` in the same change (D26: bump when any adapter's canonical output changes). Note: today a parser-version bump alone does not trigger a rebuild (pipeline.py compares only file stats). For v7 the schema bump forces the rebuild. See DM-90 for the later fix.
- **DM-3 [v4]** Status and query routes on a v6 store keep today's behaviour (`ingest.status` returns `{sources: []}`, ingest.py:357); the user must run `botsworth ingest`. The UI shows "Store needs a rebuild." (D37, SYS-57).

### 2.2 New and changed columns

`ops` (all nullable; NULL on kinds where the column does not apply):

| Column | Kinds | Meaning | Req |
|---|---|---|---|
| `turn_kind` TEXT | turn | `human` \| `message` \| `harness` | DM-20 |
| `turn_origin` TEXT | turn | detail of the kind: `typed`, `queued`, `slash`, `plugin:<name>`, `peer`, `coordinator`, `teammate`, `agent`, `sdk`, `exec`, `interrupt`, `builtin:<cmd>`, `empty`, `compact` | DM-20 |
| `display_ix` INTEGER | turn | 1-based number among the session's non-harness main turns, in time order; NULL for harness turns and agent turns | DM-24 |
| `attr_turn_rowid` INTEGER | llm_call, tool_call | rowid of the attributed turn | DM-45 |
| `attr_cost_usd` REAL, `attr_tok_total` INTEGER, `attr_n_llm_calls` INTEGER, `attr_cost_lower_bound` INTEGER | main turn | sums over owned llm_calls whose `attr_turn_rowid` is this turn | DM-45 |
| `span_wall_ms` INTEGER | main turn | span-based turn wall (D37). [D54] The span and window splits are rows of `time_split`; the columns `model_ms`..`none_ms` and `span_model_ms`..`span_none_ms` are dropped. | DM-51, DM-53 |
| `max_silence_ms` INTEGER | tool_call | longest interval inside `[start_ms, end_ms]` with no timestamped record in any file of the bundle | DM-40 |
| `user_wait` TEXT [D54] | tool_call | `ask` \| `permission` when the call waited on the user, else NULL (replaces `waits_on_user`) | DM-40 |

`steps`:

| Column | Meaning | Req |
|---|---|---|
| `skill_load` TEXT | the skill name when this step loads a skill; else NULL. `steps.skill` keeps its D15 meaning (attribution, unset in V1). | DM-30 |

`sessions`:

| Column | Meaning | Req |
|---|---|---|
| `title_source` TEXT | `rename` \| `generated` \| `first_ask` \| `fallback` | DM-10 |
| `display_title` TEXT | the D34 title; NULL when `title_source = 'fallback'` (S3 formats `<project name> · <id8>`) | DM-10 |
| `n_human_asks` INTEGER | count of human asks | DM-25 |
| `is_automated` INTEGER | 1 when `n_human_asks = 0` | DM-25 |
| `activity_bin_ms` INTEGER, `activity_tok` TEXT (JSON int array) | per-session activity bins | DM-44 |
| `model_ms`, `tool_ms`, `agents_ms`, `none_ms` INTEGER | **removed** [D54]: the session split is rows of `time_split` | DM-51 |
| `entrypoint`, `originator` | now filled (today NULL on all rows) | DM-26 |

`rollup_time` (changed meaning and new columns; D50: one row per root, 15-minute bucket and dimension set, every time value is epoch ms):

| Column | Meaning | Req |
|---|---|---|
| `bucket_start_ms` [D50] | epoch ms of the start of a 15-minute bucket aligned to the epoch: `start_ms - start_ms % 900000`. One bucket size only; no zone is used. | DM-41 |
| `bucket_kind`, `bucket_day` [D50] | **removed.** There are no `hour`, `day` or `week` rows and no TEXT date column; local days, weeks and hours are sums over buckets inside epoch ranges (DM-41). | DM-41 |
| `timed_out_tok` INTEGER, `timed_ms` INTEGER | Σ `out_tok` and Σ `dur_ms` over owned llm_calls with `dur_ms > 0` and `out_tok` not null | DM-47 |
| `n_ttft` INTEGER, `ttft_ms_sum` INTEGER | count and Σ of `ttft_ms` over owned llm_calls with `ttft_ms` not null | DM-47 |

New tables:

```sql
-- DM-52 (D37; D54): stretches >= 60 s that no main-thread call covers, for gap rows, `w`, and legend jumps
CREATE TABLE stretches(
  root_rowid INTEGER NOT NULL, turn_rowid INTEGER,        -- main turn the stretch lies in; NULL before the first main turn
  state TEXT NOT NULL CHECK (state IN ('agents', 'you', 'agentmsg', 'limit', 'none')),
  substate TEXT,                                           -- DM-50 substate, NULL for states without one
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL);
CREATE INDEX stretches_root ON stretches(root_rowid, start_ms);
CREATE INDEX stretches_len ON stretches(state, (end_ms - start_ms) DESC);

-- DM-51, DM-53 [D54]: time per (state, substate) of a session, a main turn's window and a main turn's span
CREATE TABLE time_split(
  root_rowid INTEGER NOT NULL,
  op_rowid INTEGER NOT NULL,                               -- the session op for scope 'session', else the main turn
  scope TEXT NOT NULL CHECK (scope IN ('session', 'window', 'span')),
  state TEXT NOT NULL CHECK (state IN ('model', 'tool', 'agents', 'you', 'agentmsg', 'limit', 'none')),
  substate TEXT,
  ms INTEGER NOT NULL);                                    -- rows with 0 ms are not stored
CREATE INDEX time_split_op ON time_split(op_rowid, scope);
CREATE INDEX time_split_root ON time_split(root_rowid);

-- DM-60: friendly project names
CREATE TABLE projects(
  project TEXT PRIMARY KEY, name TEXT NOT NULL,
  n_sessions INTEGER, n_automated INTEGER, first_ms INTEGER, last_ms INTEGER);
```

New indexes (D38 M1: this section and §2.3 are the only DDL source; interfaces API-51 lists names and measurements only):

```sql
CREATE INDEX rollup_bucket ON rollup_time(bucket_start_ms, root_rowid);                 -- D50: DM-42 in-scope sessions, DM-54 membership (interfaces API-51)
CREATE INDEX rollup_root ON rollup_time(root_rowid);                                    -- D50: per-session reads
CREATE INDEX ops_attr_turn ON ops(attr_turn_rowid) WHERE attr_turn_rowid IS NOT NULL;   -- DM-45
CREATE INDEX steps_skill_load ON steps(root_rowid) WHERE skill_load IS NOT NULL;       -- DM-30
CREATE INDEX sessions_automated_time ON sessions(is_automated, first_ms);               -- DM-25
CREATE UNIQUE INDEX sessions_op ON sessions(op_rowid);                                 -- D37; D38 M1: op_rowid is 1:1
CREATE INDEX ops_parent_kind_start ON ops(parent_rowid, kind, start_ms);               -- D37: turn lists, next turn (DM-55)
CREATE INDEX ops_caused_by ON ops(caused_by_rowid) WHERE caused_by_rowid IS NOT NULL;  -- D37: agent runs per turn (DM-55); column per DM-14
```

`meta` has no `tz` key [D50] (DM-41; the keys are `schema_version`, `parser_version`, `price_fingerprint`, `last_ingest_ms`, `n_ownership_disagreements`). `TABLES` gains `stretches`, `projects` and [D54] `time_split`.

### 2.3 Content references and stored previews (D39)

Source: D39, D42. Measured 2026-10-12 with `dbstat` on a copy of the v6 store (1,574,932,480 bytes; 566,761 ops, 536,820 steps; 2,779 bytes per op). Largest v6 objects: `ops` 696.9 MB, `steps` 306.0 MB, `op_closure` + `closure_desc` 103.1 MB, `ops_op_id` 64.7 MB, `steps_op` 62.1 MB, `steps_turn` 54.3 MB, `owner_key` 46.1 MB, `ops_commands` 39.5 MB, `ops_occ` 36.0 MB, `ops_bundle` 35.5 MB, `ops_command_norm` 27.6 MB. Text in `ops`: `src` 82.9 MB, `op_id` 50.3, `command` 50.6, `parent` 45.1, `occ_key` 26.7, `bundle` 26.1, `cwd` 25.0, `project` 23.0, `label` 23.5, `command_norm` 21.3, `session_id` 20.4, `path_sig` 12.9, `account` 9.1. In `steps`: `src` 88.2, `op_id` 48.1, `turn_id` 39.7, `snippet` 30.1, `bundle` 24.6, `summary` 19.7. A probe that applies DM-8 and DM-13–16 to a copy of the v6 data gives 706.9 MB (44.9 %); the probe scripts are in `the build run log (not in this repo)evidence/m1-size-probe/`.

- **DM-6 [v4]** One content-reference grammar, unchanged from `pointer.py`: `src://<provider>/<relpath>#<locator>[/<json.path>][?h=<hash>]`; `locator` = `L<first>-L<last>` (1-based lines) or `B<start>+<len>`; `relpath` percent-encoded with `/` safe; `json.path` dotted (`message.content.0`); `h` = the first 16 hex chars of SHA-256 over the addressed bytes (`pointer.content_hash`). It is the only way to reach long content (D39). The canonical stream keeps full URIs; the store keeps the DM-8 parts; every API field that carries a reference (`src`, `text_src`, `input_src`, `output_src`, `ask_src`, `command_src`, `sections[].src`) is a URI in this grammar. Test: for every op, step and event of a fixture bundle, the URI rebuilt from the stored parts equals the stream's `src` byte for byte, and `pointer.parse` accepts it.
- **DM-7 [v4]** Provider roots and the parts each reference resolves. Roots (`pointer.DEFAULT_ROOTS`): `claude_code` = `~/.claude/projects`, `codex` = `~/.codex` (relpaths start `sessions/YYYY/MM/DD/`), `omp` = `~/.omp/agent/sessions`. Rules common to all providers: a step's reference addresses its content block (json path to the block); a tool_call's locator spans from the line of the call to the line of its result, its json path selects the call block on the first line, and its output is the result record on the last line; a turn's reference addresses the one boundary record that holds the ask (not a range over the turn); an agent's prompt is the `input` part of the engagement's `caused_by` tool_call; the full command is the `input` part of the tool_call. Paths below are relative to the record on the named line.

  | Part | Claude Code | Codex | omp |
  |---|---|---|---|
  | `input` (tool call, agent prompt, full command) | first line, `message.content.<i>.input` (`.command` for Bash, `.prompt` for Agent/Task) | first line, `payload.arguments` (`function_call`, a JSON string) or `payload.input` (`custom_tool_call`) | first line, `message.content.<i>.arguments` |
  | `output` | last line, `toolUseResult`, else `message.content.0.content` | last line, `payload.output` (`function_call_output`) | last line (`role = toolResult`), `message.content` |
  | `thinking` | step line, `message.content.<i>.thinking` | step line, `payload.summary` (plaintext summary only; `encrypted_content` is never returned) | step line, `message.content.<i>.thinking` |
  | `text` (say, answer) | step line, `message.content.<i>.text` | step line, `payload.message` (`agent_message`) | step line, `message.content.<i>.text` |
  | `text` (turn ask) | boundary `user` record, `message.content` | the `event_msg` `user_message` line, `payload.message` | boundary `message` line, `message.content`; `skill-prompt` line, `details` |

  Tests (one per provider, adapter fixture plus `pointer.resolve` with fixture roots): a tool call → `input` holds the call's arguments, `output` holds its result text; a thinking step → `thinking`; an answer step → `text`; a main turn → `text` holds the ask; a spawn call → `input` holds the agent prompt; a Bash/exec call → `input` holds the full command, longer than its 160-char preview.
- **DM-8 [v4]** Compact storage. `ops`, `steps` and `events` store a reference as three columns: `src_file INTEGER NOT NULL` (rowid in `src_files`), `src_loc TEXT NOT NULL` (the URI text after `#` and before `?`: locator plus `/json.path` when present), `src_h BLOB` (the 8 bytes of `h`, NULL when the URI has none). The text column `src` is dropped from all three tables.
  ```sql
  CREATE TABLE src_files(rowid INTEGER PRIMARY KEY, provider TEXT NOT NULL, relpath TEXT NOT NULL,
    prefix TEXT NOT NULL,                        -- 'src://<provider>/<percent-encoded relpath>'
    UNIQUE(provider, relpath));
  ```
  The URI is `prefix || '#' || src_loc || CASE WHEN src_h IS NULL THEN '' ELSE '?h=' || lower(hex(src_h)) END`. One Python function, `botsworth.store.refs.uri(prefix, src_loc, src_h)`, builds it; the SQL expression `refs.SRC_SQL` is the same rule for views. `src_files` rows are interned on load (`INSERT … ON CONFLICT DO NOTHING`), are never deleted by a bundle reload, and are rebuilt only by a full rebuild (DM-4). This replaces D39's "(source_files rowid, locator, hash)": `source_files.path` is absolute, so recovering the relpath from it would put provider roots into the store layer, and `source_files` rows are deleted and reinserted per bundle; `src_files` needs neither, and it also makes `--stream` ingest resolve without `source_files` rows (D42). Tests: round trip (DM-6 test); a reference with no `h` stores `src_h` NULL and rebuilds without `?h=`; reloading a bundle keeps its `src_files` rowids; a relpath with `#`, `?` and a space round-trips.
- **DM-9 [v4]** Stored previews. No stored text column holds more than 160 characters except `ops.op_id`, `ops.duplicate_of`, `ops.command_norm` (the D17 grouping key; 21.3 MB measured; kept whole because grouping on a clipped key would merge distinct commands), `events.fields` and `segments`. Caps: `label` 120, `steps.summary` 120 (the stream contract, unchanged); `steps.snippet` 160; `ops.command` 160 (the store clips at load; the stream keeps the adapter's value up to 2,048); `steps.target` and `ops.file_path` 160 (store clips at load; the stream keeps 200). Clipping is `common.turns.clip` semantics (cut at the cap, then strip trailing whitespace). The first part of the ask is the main turn's `label` (DM-22, DM-23); the full ask is the turn reference's `text` part (DM-7). `command_src` (D42) is the URI of the tool_call reference read with part `input`; Q2c builds it from `ops.src_file`, `src_loc`, `src_h` with `refs.uri`. Tests: a 2,048-char Bash command → `ops.command` length 160 and the `input` part of its reference returns all 2,048 chars; a 300-char file path → stored 160.
- **DM-13 [v4]** Never stored in the store (D4, D39): prompts and asks beyond the label, thinking, model text, tool input and tool output, agent prompts, file contents, full commands, `args_json` (the column is dropped; it was empty on all 566,761 rows), and per-record timestamps (`source_file.ts_list`, DM-40, lives in the stream only). Test: after loading a fixture whose prompt, thinking, tool input, tool output and Bash command each carry a unique 40-char marker after their first 200 characters, no text column of any table contains a marker (`SELECT` over every TEXT column).
- **DM-14 [v4]** Redundant and repeated strings in `ops` (measured above):
  - dropped, derived at read time: `parent` (from `parent_rowid`), `session_id`, `bundle` and `project` (from `sessions` via `root_rowid`; `ops.project` equals the session's project on all 566,761 rows), `src` (DM-8);
  - replaced by a rowid: `caused_by` → `caused_by_rowid` (same bundle; the stream validator already rejects a dangling `caused_by`);
  - interned in `names(id INTEGER PRIMARY KEY, s TEXT NOT NULL UNIQUE)`: `cwd` → `cwd_id`, `account` → `account_id`, `path_sig` → `path_sig_id`;
  - `occ_key` → `occ_hash BLOB` = the first 8 bytes of SHA-256 over the UTF-8 `occ_key` (`refs.occ_hash`); `owner_key` and `ops_occ` index `occ_hash`. Collision odds at 10⁶ keys are about 3·10⁻⁸. A provider request id or tool_use id is resolved through `op_id`, which embeds it (`…/llm_call/req_…`, `…/tool_call/toolu_…`), not through `occ_key` (amends QRY-38a's wording).
  `steps`: `turn_id` → `turn_rowid`, `op_id` → `op_rowid`, `bundle` dropped; `step_id` stays `<turn op_id>#<ordinal>` on the wire. `events`: `bundle` dropped, `op_id` kept (5.4 MB table).
  `agent_id` (10.6 MB), `model`, `tool_name` and `agent_type` stay text: they are short or used in `IS NULL` tests everywhere.
  The view `ops_v` returns every v6 column name (`parent`, `caused_by`, `session_id`, `bundle`, `project`, `cwd`, `account`, `path_sig`, `src`) from these columns, for readers that have not moved to the v7 columns. Indexes removed: `ops_bundle`, `ops_command_norm` (the only reader groups by `command_norm` under one `command_key`, which `ops_commands` covers). `ops_commands` uses `root_rowid` in place of `session_id`.
  Tests: `ops_v` returns the stream's `parent`, `caused_by`, `session_id`, `cwd`, `account`, `path_sig` and `src` for every op of a fixture bundle; two bundles with the same `occ_key` still give one owner and one `duplicate_of`.
- **DM-15 [v4]** Op tree as nested intervals; `op_closure` is removed (103.1 MB with its index). Each op stores `pre` and `post`: `pre` = its 0-based pre-order position in its session tree (children in `seq` order, as `load._tree` walks), `post` = the largest `pre` in its subtree. Descendants of X (X included) = `root_rowid = X.root_rowid AND pre BETWEEN X.pre AND X.post`; ancestors of Y = walk `parent_rowid` (at most 32 hops, D18) or `pre <= Y.pre AND post >= Y.post` in the same root. A refold keeps `pre`/`post` (the tree does not change).
  ```sql
  CREATE INDEX ops_root_pre ON ops(root_rowid, pre);
  ```
  Test: for a fixture tree of depth ≥ 3, the descendant set by `pre`/`post` equals the set from walking `parent_rowid`, for every op.
- **DM-16 [v4]** Size target (D39, PLAN E3). After the full rebuild of the corpus, the v7 store file is at most 787,466,240 bytes (50 % of v6's 1,574,932,480), and at most 1,390 bytes per op (50 % of v6's 2,779), whichever corpus growth makes the fairer test; both figures are reported. The full rebuild runs `VACUUM` on `<db>.rebuild` before the DM-4 swap (the probe figure is post-`VACUUM`; a store built bundle by bundle has partly filled index pages). Measured with `page_count * page_size` and `dbstat` per object. Test (B4 acceptance, not `make check`): both figures and the per-object table are written to the B4 gate.

## 3. Adapter capture

Each item: source field, rule, test case. Adapters stay pure (design §10): they emit canonical records; the store computes cross-op derivations (§4).

### 3.1 Session title (D34, deferred.md "Session display title")

- **DM-10 [v4]** The session title is chosen in this order, first non-empty wins:
  1. **rename**: Claude `custom-title` records `customTitle` in the main transcript, last record wins. Codex `~/.codex/session_index.jsonl` `thread_name` for the session id, last line for that id wins; else the last `event_msg` `thread_name_updated` in the rollout (codex/tree.py:745 already reads this).
  2. **generated**: Claude `ai-title` `aiTitle`, last record wins; else the first `summary` record's `summary` (today's fallback, claude/tree.py:216–219). omp `title` record, last wins, else the header `title` (unchanged).
  3. **first_ask**: the label of the first human ask (DM-20) that qualifies (DM-11). For Codex, when no turn qualifies, `threads.title` from `state_5.sqlite` if it qualifies (D34: "Codex threads.title counts as a first ask").
  4. **fallback**: none of the above; `display_title` = NULL.
  Adapters emit levels 1–2 as `title` plus a new op field `title_source` (`rename|generated`). The store computes level 3 (it needs turn kinds) and writes `sessions.display_title` and `title_source`. Codex `threads.title` is read by the adapter (only it reads `state_5.sqlite`) and emitted as `title` with `title_source = thread_title`; the store uses it as `first_ask` only when no human ask qualifies (D43).
  Tests: a Claude fixture with `ai-title` "A" then `custom-title` "B" then `ai-title` "C" → `B`, `rename`. Two `custom-title` records "x" then "y" → `y`. Codex session listed in `session_index.jsonl` with `thread_name` "Review app" and `threads.title` = first message → `Review app`, `rename`. A Claude session with no title records whose first human ask is "fix the rounding in cost.py" → that text, `first_ask`. A session whose only asks are `/clear` and "ok" → `fallback`.
- **DM-11 [v4]** A label qualifies as a first ask when, after whitespace collapse: it has at least 4 words; it does not start with `/`, `<`, `[`, `~/`, or `#`; and it is not a UUID (regex `^[0-9a-f]{8}-[0-9a-f]{4}-`). Source: D34; prototype BRIEF.md:27. Tests: "go with recommendation" (3 words) fails; "/mad:bootstrap HANDOFF.md now please" fails; "<pasted_content …> explain this" fails; "plan out the team roster" passes.
- **DM-12 [v4]** `session_index.jsonl` is outside every Codex bundle, so a rename there does not change any bundle file. Rule: the Codex adapter exposes `session_overrides(root) -> {session_id: thread_name}`; the pipeline calls it every run and the store applies it to `sessions.title`/`title_source`/`display_title` without rebuilding bundles. Test: append a line to a fixture `session_index.jsonl`, run ingest with no other change → that session's `display_title` changes; `rebuilt` = 0.

### 3.2 Turn kind and the human-ask flag (D34, D32, deferred.md "Per-turn human-ask flag")

Turn boundaries stay as today (design §4.5, claude/tree.py `is_boundary`), except DM-21's slash rule. Every boundary record is classified; harness turns stay turn ops (so no cost is lost) but are folded and unnumbered (DM-24).

- **DM-20 [v4]** `turn_kind` and `turn_origin` for **main turns**, first matching rule wins:

  **Claude Code** (fields on the boundary record):
  | Rule | kind | origin |
  |---|---|---|
  | text starts with `[Request interrupted by user` | harness | `interrupt` |
  | text is empty or `(no text)` | harness | `empty` |
  | `<command-name>/X` with no expanded-prompt record (DM-21) | harness | `builtin:X` |
  | `<bash-input>`, `<bash-stdout>`, `<local-command-stdout>` | harness | `builtin:bash` |
  | `origin.kind = plugin` | message | `plugin:<origin.name>` |
  | `origin.kind` in `peer`, `coordinator` | message | that kind |
  | text contains `<mc-message` or `<teammate-message`, or starts with `Another Claude session sent` | message | `peer` |
  | `promptSource = sdk` | message | `sdk` |
  | `promptSource = system` (no rule above matched) | message | `system` |
  | `<command-name>/X` with an expanded-prompt record | human | `slash` |
  | `origin.kind = human` or `promptSource` in `typed`, `queued` | human | `typed` / `queued` |
  | no `promptSource` and no `origin` (older harness), record `entrypoint = cli` | human | `typed` |
  | no `promptSource` and no `origin`, `entrypoint = sdk-cli` | message | `sdk` |

  **Codex** (rollout `session_meta.payload.source` and `originator`, `user_message.message`):
  | Rule | kind | origin |
  |---|---|---|
  | message empty after strip (58 records) | harness | `empty` |
  | `source = exec` or `originator = codex_exec` | message | `exec` |
  | else | human | `typed` |

  **omp** (`message.role = user`, not `steering`; `custom_message` `skill-prompt`):
  | Rule | kind | origin |
  |---|---|---|
  | `custom_message` with `customType = skill-prompt` | human | `slash` |
  | `message.attribution = agent` | message | `agent` |
  | `attribution = user`, or absent | human | `typed` |

  Agent-transcript turns (all providers): kind `message`, origin `agent` (an arrival is never a human ask). Compactions are events, not turns; a Claude `/compact` boundary is `harness`, `builtin:compact`.
  Tests (one fixture record each): Claude `{"type":"user","origin":{"kind":"plugin","name":"e2e-grid"},"promptSource":"system"}` → message/`plugin:e2e-grid`. Claude `promptSource:"sdk", entrypoint:"sdk-cli"` (helperbot shape) → message/`sdk`. Claude `[Request interrupted by user for tool use]` → harness/`interrupt`. Claude typed "fix it", `promptSource:"typed"` → human/`typed`. Codex user_message in a `source:"exec"` rollout → message/`exec`. omp user message `attribution:"agent"` → message/`agent`; `attribution:"user"` → human.
- **DM-21 [v4]** Claude slash commands. A user record whose text holds `<command-name>/X</command-name>` is a **skill or custom command** when the next `user` record in the same transcript has the same `promptId` and `isMeta = true` with text content (the expanded prompt, for example "Base directory for this skill:" or the command body). Otherwise it is a **built-in** command (the next user record is `<local-command-stdout>…` or there is none). Source: source records checked 2026-10-12 (`/clear`, `/compact`, `/model` → `<local-command-stdout>` or system record; `/mad:bootstrap`, `/ctl-command` → `isMeta` expanded prompt); session-viz commit 701ca51 (`<command-name>` rule). Built-ins emit **no** `skill_invocation` event (today they do: compact 165, clear 103, model 11 …). Turn label for both = `/X` plus the `<command-args>` text, space-joined, clipped to 120 (today the label is `/X` only, claude/tree.py:264). Tests: `/clear` record followed by a `system` record → harness, no skill event; `/mad:bootstrap ARGS` followed by an `isMeta` record → human/`slash`, one skill event `skill = mad:bootstrap`, label `/mad:bootstrap ARGS`.
- **DM-22 [v4]** omp turn labels (deferred.md "From T8"; omp/adapter.py:286). The label is the text of the boundary record, not the constant `user prompt`: for `message` records, the `text` parts of `message.content` (a string, or a list of `{type:"text"}` blocks) joined by a space, clipped to 120; for `skill-prompt`, `/` + `details.name` + space + `details.args`, clipped. The label `initial` (derived first turn) stays. Test: the fixture record with content `[{"type":"text","text":"approved"}]` → label `approved`; after rebuild, `count(*) WHERE provider='omp' AND label='user prompt'` = 0.
- **DM-23 [v4]** Message turn labels show the message body: for envelopes, `common.turns.envelope_body` (teammate, agent, fork); for Claude plugin records, the text after the `The <name> plugin sent a message` prefix line. Test: an `<mc-message from="x">ship it</mc-message>` boundary → label `ship it`.
- **DM-24 [v4]** `display_ix` numbers the main turns with kind `human` or `message` 1, 2, 3 … by `start_ms` (ties by `seq`, or by `pre` when `seq` is NULL: Codex ops carry no `seq`; FX2-BE). Harness turns get NULL. Source: D34 ("Harness records fold away and are not numbered turns"). The D21 display ids (`T12`) use `display_ix` from v7 on; S3 owns that interface change. Test: turns [human, harness `/clear`, human] → `display_ix` 1, NULL, 2.
- **DM-25 [v4]** `sessions.n_human_asks` = count of main turns with kind `human`; `is_automated = (n_human_asks = 0)`. A session with no main transcript (Claude subagents-only, S1 in session-viz) is automated. Source: D32. Tests: an helperbot-shaped session (one `sdk` turn) → automated; a session whose only asks are "ok" and "yes" (short, human) → **not** automated (the ≥4-word filter in the prototype would have hidden it, deferred.md); expected after rebuild: every session in `/private/tmp/helperbot` (356) is automated.
- **DM-26 [v4]** Session `entrypoint`/`originator` are filled: Claude `entrypoint` of the first main-transcript record that has one; Codex `session_meta.payload.originator` and `source` (as `entrypoint`); omp stays NULL (no field). Test: a Codex fixture with `source:"vscode"` → `entrypoint = vscode`.

### 3.3 Skill loads (D34, D15, deferred.md "Skill and speed signals")

- **DM-30 [v4]** A step is a skill load when it carries a `skill_invocation` event (the event's `fields.step_id` names it). The store sets `steps.skill_load = fields.skill` on that step. For slash events (Claude 515, omp 58, which have no `step_id` today), the adapter sets `step_id` to the first step of the turn the event opens; if that turn has no step, `skill_load` is not set and the event's `op_id` (the turn) is the anchor. The UI "s" key (D33) and the session "skill loads" figure read `skill_load` plus slash events. Test: a Claude `Skill` tool call → its step has `skill_load = <skill>`; a Codex `sed -n '1,200p' ~/.claude/skills/x/SKILL.md` step → `skill_load = x`, verb stays `run`.
- **DM-31 [v4]** Codex shell reads (D34 left this open; codex/steps.py:203 matches any command that mentions `/skills/<name>/SKILL.md`). New rule: split the command at `&&`, `||`, `;`, `|`; a segment counts as a load when it mentions the path **and** its program (first word after `cd …`/`bash -lc` unwrapping) is one of `cat`, `sed` (without `-i`), `head`, `tail`, `nl`, `less`, `bat`, or the step has `parsed_cmd.type = read`. `rg`, `grep`, `wc`, `git`, `ls`, writes and redirects into the file do not count. Expected effect on today's 795 Codex read events: about 690 stay (sed 589, cat 83, nl 22), about 65 drop (rg 50, wc 10, git 3). Tests: `sed -n '1,260p' /x/skills/a/SKILL.md` → load `a`; `rg -n foo .claude/skills/team/SKILL.md` → no load; `pwd && ls dir && sed -n '1,220p' /x/skills/b/SKILL.md` → load `b`; `sed -i 's/a/b/' /x/skills/c/SKILL.md` → no load.
- **DM-32 [later]** Claude `Read` of a SKILL.md stays a load (`via=read`, 440 events). An Edit or Write of a SKILL.md is not a load (unchanged).

### 3.4 Time capture

- **DM-40 [v4]** Derived tool durations (deferred.md "Long tool durations are mostly derived"; REVIEW-v4-detail.md §Data 1). Facts: Claude has `toolUseResult.durationMs` for 1,002 calls only; 194,421 Claude tool durations are `derived` = result-record time − tool_use time. The long ones include a permission or usage-limit wait or a suspended machine: the 10h44m `Read` (e5f6a7b9, L5176–5177) has a `queue-operation` record 28 min before its result; the 2h29m "Run contrast checks" (f6a7b8c0 agent file, L1239–1241) has no record between 10:38:13 and 13:07:55. Fix:
  - [D54] `user_wait` on a tool_call, set by the adapter from the record:
    - `ask`: Claude `AskUserQuestion`, Codex `request_user_input`, omp `ask` (every call, answered or declined);
    - `permission`: a Claude tool result whose `permissionDecision.source` starts with `user` (Claude Code 2.1.293 and later), or whose result text starts with "The user doesn't want to proceed" (any version), when the call is not an `ask`;
    - NULL otherwise, including a `permissionDecision.source` value that is neither `user*` nor `config` (adapter tests pin the known values).
    Known limits: approvals before Claude Code 2.1.293 leave no record and stay tool time; Codex and omp record no approvals; a permission wait includes the run after approval, because no record separates them. The store keeps only the category, never the result text (D39).
  - The store computes `max_silence_ms` for every tool_call with `time_prov = derived`: the longest sub-interval of `[start_ms, end_ms]` that contains no timestamped record of any file of the bundle. (Adapters emit the sorted record timestamps per file as a new `source_file` field `ts_list`, or the store uses the start/end of all ops and steps in the bundle; the implementer chooses, the result must match on the test.)
  - `dur_ms` and `time_prov` are not changed.
  Display rule (for S1/S3): a derived duration is shown with `≈`; "longest commands" and "slowest tools" lists exclude calls with `user_wait` set and list a call with `max_silence_ms ≥ 300,000` separately as "no records for <max_silence>". Threshold 5 min (Q2, resolved).
  Tests: a fixture tool_use at t, a `queue-operation` record at t+10 min, result at t+40 min, nothing else → `max_silence_ms` = 30 min. Tool_use and result 4 s apart → `max_silence_ms` ≤ 4 s. [D54] An AskUserQuestion → `ask`; a result with `{decision: accept, source: user_temporary}` → `permission`; `{decision: reject, source: config}` → NULL; a result whose text starts with the rejection text and has no `permissionDecision` → `permission`.
- **DM-36 [v4]** Agent run time (deferred.md "Turn detail data gaps"; REVIEW-v4-session-r2.md §5). No new capture: every engagement already has `start_ms`, `end_ms`, `wall_ms`, `active_ms` (6,693 of 6,693). Rule for readers: an agent row's run time is its **engagement** `wall_ms` (the engagement caused by that Agent or SendMessage call, found by `caused_by`), not the agent's first-to-last span; `runs` and `run_ms` are per agent row, that is per causing call (D38 M12): `runs` = the number of engagements with `caused_by` = that call, `run_ms` = the union of their intervals (Σ `wall_ms` when they do not overlap, Q7). A later SendMessage is a separate row. Test (D38 M12): one spawn call that caused two engagements, #0 (10 min) and #1 (5 min) one hour apart → one row, `run_ms` 15 min, `runs` 2.
- **DM-37 [v4]** Claude ttft and llm `dur_ms` (deferred.md; design §4.6). No source field exists: 0 of 189,107 Claude calls have ttft; `dur_ms` is the gap from the previous non-assistant record (includes queue and hook time). Nothing to capture. Readers label Claude and Codex speed with basis `derived` (DM-47); ttft appears only where `ttft_ms` is not null (omp).

## 4. Derived fields and rollups (store fold, `store/load.py`)

### 4.1 Epoch buckets and the request zone (B1, D50)

- **DM-41 [v4] [D50]** No zone in the store (D50: "all date and time in the storage must be unixepoch"). Every stored time is epoch ms (`source_files.mtime_ns` is epoch ns, a file-identity check, never displayed; `price_table.snapshot_date` is a TEXT version label of a price sheet, not a time; the `tz` key inside a limit-hit `events.fields` JSON is source text copied from the record). There is no `meta.tz`, `BOTSWORTH_TZ` or `TZ` read at ingest, and no zone re-fold.
  `rollup_time` has one bucket size: 15 minutes aligned to the epoch (`bucket_start_ms = start_ms - start_ms % 900000`). Every IANA offset in use is a multiple of 15 minutes (IST +5:30, Nepal +5:45, Chatham +12:45), so every local midnight, local Monday and local hour in any zone falls on a bucket edge. A local day is the sum of buckets with `bucket_start_ms` in `[local midnight, next local midnight)`; DST days are 23 or 25 h long; local weeks start on local Monday; local hours use local-hour edges. These edges are computed at query time with `zoneinfo.ZoneInfo`, in the request zone (`Scope.tz`, interfaces QRY-3/QRY-4); the same store gives different day splits for different zones with no re-ingest.
  Tests: (g) one `rollup_time` row per (root, 15-minute bucket, dimension set): two calls of one root in the same 15 minutes and the same dimensions give one row; calls 15 minutes apart give two; `bucket_start_ms % 900000 = 0` on every row; the store has no `meta.tz`, no TEXT date column in `rollup_time`, and the ingest function takes no zone argument. A call at 2026-10-06T19:00Z has `bucket_start_ms` = 2026-10-06T19:00Z. Day figures per zone are tested through the query layer (interfaces TST-8). Acceptance on the real store, request zone `Asia/Kolkata`: day 2026-10-07, provider claude_code → tokens 1,847,233,004, cost $549.89, 18 distinct roots.
- **DM-42 [v4] [D50]** Session × day rollup (deferred.md "Day totals must split sessions across days"; D32). No new table: `rollup_time` rows carry `root_rowid`, so after DM-41 [D50] the session × local-day figures are sums over the buckets inside each local day's epoch range `[local midnight, next local midnight)` in the request zone, grouped by `root_rowid` and day. Rules for readers:
  - a day's total = Σ of that day's rows (cost by llm_call start time);
  - a session is **in range** per DM-54 (a bucket with `n_llm_calls > 0` inside the range's epoch range);
  - list rows, group headers, the donut and the header total use the **full** session cost (`sessions.incl_cost_usd`) of in-range sessions (D32; deferred.md "REVERSED by the maintainer"), so a day's bar need not equal the sum of its rows.
  Test: a fixture session with $3 of calls on local Oct 6 and $5 on Oct 7 → $3 in the Oct 6 epoch range and $5 in the Oct 7 epoch range; a range of Oct 7 only lists the session at $8.00 and the Oct 7 bar shows $5.00.
- **DM-43 [v4] [D50]** Time-split and activity figures are recomputed whenever a bundle's `rollup_time` rows are. There is no zone re-fold: a change of the request zone changes no stored row.

### 4.2 Per-session activity bins

- **DM-44 [v4]** `sessions.activity_bin_ms` and `activity_tok` (prototype BRIEF.md:27: "tokens per bin across the session's life, ≤ 96 bins, plus bin_ms"; D32 activity strip). Span = `[sessions.first_ms, sessions.last_ms]`. `activity_bin_ms` = max(60,000, ⌈span / 96⌉ rounded up to a whole minute). Number of bins = ⌈span / bin_ms⌉ (≤ 96; 1 for a span of 0). Bin i = Σ `tok_total` of owned llm_calls in the session's subtree (agents included) with `start_ms` in `[first_ms + i·bin_ms, first_ms + (i+1)·bin_ms)`; a null `tok_total` adds 0. Resume gaps are inside the span and show as empty bins. Sessions with no owned llm_call: `activity_tok = []`. Test: span 2 h → ⌈7,200,000 / 96⌉ = 75,000 ms, rounded up to 120,000 ms → 60 bins; Σ bins = `sessions.incl_tok_total` when no call has null tokens.

### 4.3 Per-turn cost including agents

- **DM-45 [v4]** Attributed turn (D34: "Turn cost = its llm_calls plus agents linked by caused_by from Agent and SendMessage calls"). For each owned leaf (llm_call, tool_call): walk up its parents to the nearest main turn or engagement. A main turn is the answer. At an engagement, continue from the engagement's `caused_by` tool_call (not its `parent`). An engagement with `caused_by` NULL ends the walk with no turn (`attr_turn_rowid` NULL; the leaf is "unattributed agent work", shown at session level). The walk ends by revisit detection: a revisited engagement raises `BundleInvalid`, and a cause that is not a tool_call is a defect. There is no time-order check: in v6, 95 of 6,527 linked engagements have a cause that starts after the engagement (62 SendMessage, 33 Agent; D46) (design §4.5 loud-failure rule).
  Turn columns: `attr_cost_usd` = Σ `cost_usd` (null costs excluded, `attr_cost_lower_bound` = 1 when any is null), `attr_tok_total`, `attr_n_llm_calls`.
  A main-thread leaf with no main-turn ancestor (a session-level record) is also unattributed (D38 L9).
  Invariant: Σ over the session's main turns of `attr_cost_usd` + Σ cost of unattributed leaves (under an engagement with NULL `caused_by`, or with no main-turn ancestor; D38 L9) = `sessions.incl_cost_usd`.
  Evidence from the real store: the walk ends at a main turn for 6,510 engagements and with no turn for 183; no cycle; max 9 hops. It differs from the tree parent (today's `turn.incl_cost_usd`) for 14 engagements (the `+cycle` replies whose SendMessage is in a later turn). Turn `claude_code/c3d4e5f7…/turn/main#2`: own main-thread calls $10.67 (153 calls) + 46 engagements $16.32 = $26.99 = today's `incl_cost_usd`.
  Tests: main turn T1 spawns agent A (engagement A#0, $2); in turn T2 a SendMessage to A causes A#1 ($1), which the cycle rule parents under T1's spawn call → T1 `attr_cost_usd` includes $2, T2 includes $1. An engagement with basis `none` → its calls have `attr_turn_rowid` NULL and the invariant holds.
- **DM-46 [v4]** Turn and step rows come from llm_calls, not from steps (deferred.md "Turn detail data gaps"). This is a reader rule, stated here because it follows from the model: the cost unit is the llm_call; its steps are the steps whose `op_id` is the call plus the steps whose `op_id` is one of its tool_calls (`tool_call.parent` = the call). 46,119 owned calls ($2,029.14) have no step with their own `op_id` because their only content is tool_use; in the sample turn, all 19 such calls have steps through their tool_calls. Test: a call whose content is a single tool_use → one row with that call's cost and the tool step under it.

### 4.4 Per-model aggregates with speed

- **DM-47 [v4]** Speed = Σ `out_tok` / Σ `dur_ms` over owned llm_calls with `dur_ms > 0` and `out_tok` not null, in tokens per second. Its basis = the weakest `time_prov` among those calls (`present` < `derived` < `estimated`). `rollup_time` carries `timed_out_tok`, `timed_ms`, `n_ttft`, `ttft_ms_sum` so period queries do not scan ops. Per-session and per-turn model lines (calls, cost, model time = Σ `dur_ms`, speed, basis) are computed at query time from ops by `root_rowid` (S3). Sources: REVIEW-v4-session-r2.md §5; REVIEW-v4-turn-r2.md; deferred.md (Claude speed is relative). Test: two calls, (out 100, dur 2,000 ms, derived) and (out 50, dur null) → speed 50 tok/s, basis `derived`, `timed_out_tok` 100.

### 4.5 Time split and no-activity stretches

- **DM-50 [v4] [D54]** States and substates. Inside a session's wall (the union of its segments, invariant 4), each instant has exactly one (state, substate), by the first rule that matches:
  1. `model` — a main-transcript llm_call (`agent_id` NULL) interval covers it;
  2. `tool` — a main-transcript tool_call with `user_wait` NULL covers it;
  3. `agents` — some engagement's own llm_call, or own tool_call with `user_wait` NULL, covers it;
  4. `you` / `ask` or `you` / `permission` — a tool_call with that `user_wait` covers it; main-transcript calls before agent calls, `ask` before `permission`;
  5. nothing above covers it (an idle run; idle runs are cut at every main-turn start): the main turn that starts where the idle run ends decides. Before the first numbered turn (no numbered main turn starts at or before the run's start) → `none` / `before_first`; a `human` turn whose label matches SES-17 → `limit`; another `human` turn → `you` / `reply`; a `message` turn with origin `peer`, `coordinator`, `teammate` or `agent` → `agentmsg`; else (another kind, or no turn starts there) → `none` / `none`. When several main turns share a start, the one with the lowest `seq` (`pre` when `seq` is NULL) decides.
  The states are the seven of SES-15. `you` has the substates `ask`, `permission`, `reply`; `none` has `before_first` and `none`; the other states have none (NULL). One function in `store/timesplit.py` computes the runs; load and the query layer (zoom ranges, the histogram state lane) call it with the same inputs, so the query figures over a whole session equal the stored ones.
  Source: D34 as amended by D54 (the `you` state is given only on recorded facts: a user-wait call, or the kind of the next turn); REVIEW-v4-steps-r3.md §2 (state priority). Async tool calls (`is_async = 1`) count as `tool`. Anything recorded as running beats a wait on the user: a question asked while an agent works shows as `agents` for that time (D54).
  Tests: the DM-51 test; a main AskUserQuestion 10:00–10:20 with nothing else → 20 min `you`/`ask`; the same while an agent runs 10:05–10:10 → 15 min `you`/`ask` and 5 min `agents`; an agent Bash with `user_wait = permission` 10:00–10:04, main idle, no other agent → 4 min `you`/`permission`; idle 30 s before a human turn → 30 s `you`/`reply`; a main tool_call in parallel with a main AskUserQuestion → `tool` for the overlap.
- **DM-51 [v4] [D54]** `time_split` rows with scope `session` (the session op) and `window` (each main turn). A main turn's window is `[turn.start_ms, next main turn start_ms)`; the last turn's window ends at the session's wall end. When several main turns share a start, the one with the lowest `seq` holds the window and the others get none. Per D37 the window figures exist only so that session sums add up; the UI's turn Wall, Waiting and split use DM-53. Invariants: on the session, Σ `ms` = `wall_ms`; per (state, substate), Σ over the windows plus the part before the first turn = the session's figure. Test: session wall 60 min; main llm 10 min, main tool 5 min, agent-only 20 min, nothing 25 min with no turn starting at its end → `model` 10, `tool` 5, `agents` 20, `none`/`none` 25, sum 60.
- **DM-52 [v4] [D54]** `stretches` rows (D37): every maximal run of at least 60 s of one (state, substate) that no main-transcript call covers — states `agents`, `you` (substate `reply`, or `ask`/`permission` from an agent's call), `agentmsg`, `limit`, `none` — split at main-turn starts (a run that crosses a turn start becomes two rows; each part is kept if it is at least 60 s). A main-transcript call that waits on the user is a tool row already and gets no stretch. `turn_rowid` = the main turn whose window holds the row (it can be a harness turn). `turn_rowid` is a storage detail: S3 (QRY-41) recomputes the attached turn per SES-37 and does not read it (D38 M11). Resume gaps lie outside the wall and produce no rows. Minimum length 60 s (Q3, resolved). Test: nothing runs 13:45–14:32 and a human ask starts at 14:10 → two rows, 13:45–14:10 `you`/`reply` (previous turn) and 14:10–14:32 `none`/`none` (new turn; no turn starts at 14:32). Only agents run 15:00–15:40 → one `agents` row. Agents run 30 s → no row. A main AskUserQuestion of 10 min → no row.
- **DM-53 [v4] [D54]** Span-based turn figures (D37; session.md Q2). For each main turn, span = the turn op's stored `[start_ms, end_ms]` (D38 H1). It is not widened over engagements the turn caused: 965 of 5,944 such engagements end after their turn's `end_ms`, and that agent time after the end is a Waterfall tail, not turn wall. `span_wall_ms` = end − start. The `time_split` rows with scope `span` = time in each DM-50 (state, substate) inside the span; they sum to `span_wall_ms`. Time in the span outside the wall (a resume gap, D48) counts as `none`/`none`. The turn's Waiting figure = `span_wall_ms` − span `model` − span `tool`, the time in the span when no main-thread model call or tool was working (session.md SES-21; Kai 2026-10-12, interfaces Q14; D54: time waiting on the user is in Waiting, not in tool). The time from `turn.end_ms` to the next main turn's start is not in the turn; it is a gap row (a `stretches` row when ≥ 60 s). Test: turn 10:00–10:30 with 10 min model, 5 min tool, 15 min nothing, next turn at 11:00 → span 30 min, `model` 10, `tool` 5, `none` 15; the 10:30–11:00 interval is a stretch row, not part of the turn. Test (D54): a turn 10:00–10:30 with a 12 min AskUserQuestion and 18 min model → span `you`/`ask` 12, Waiting 12. Test (D38 H1): a turn whose agent ends 10 min after the turn's last main-thread record → `span_wall_ms` excludes those 10 min, the turn op's `end_ms` is unchanged, and the agent run (DM-55) ends 10 min after `end_ms`.
- **DM-54 [v4] [D50]** Period membership (D37). A session belongs to a period when it has at least one `rollup_time` row with `n_llm_calls > 0` and `bucket_start_ms` inside the period's epoch range (`[local midnight of the first day, local midnight after the last day)` in the request zone). A day with only tool activity does not make the session a member. Test: a session with calls on Oct 6 and only a tool result on Oct 7 is not in the period Oct 7.
- **DM-55 [v4]** Joins S3 needs (D37); no new columns, because both are derivable from v7 fields:
  - next turn's origin and label: the next main turn of the same session by `start_ms` (`ops` with `parent_rowid` = session rowid, `kind = 'turn'`, `agent_id` NULL; index `ops_parent_kind_start`), reading its `turn_origin`, `turn_kind` and `label`;
  - end time of each agent run a turn caused: the engagements whose `caused_by` is a tool_call with `attr_turn_rowid` = that turn (index `ops_caused_by`), each with its `end_ms`.
  Test: for turn T with a SendMessage that caused engagement E ending at 10:45, the join returns E with end 10:45; for the last turn the next-turn join returns nothing.

### 4.6 Projects

- **DM-60 [v4]** `projects.name` (D32: "Every project is nameable"; inline name in the list). For all projects in the store: take the last path component; while two projects share a name, prefix each colliding name with its next parent component (`bizdemo/src`). A path equal to the home directory is named `~`. Recomputed at the end of every ingest from `sessions` (150 projects today). `n_sessions`, `n_automated`, `first_ms`, `last_ms` from `sessions`; `n_sessions` and `n_automated` count only sessions with `n_llm_calls > 0`, the same membership rule as DM-54 (D38 L1). Tests: `/Users/a/p/bizdemo/src` and `/Users/a/q/demolib/src` → `bizdemo/src`, `demolib/src`; `/private/tmp/helperbot` alone → `helperbot`.
- **DM-61 [v4]** Automated helper projects are identified through sessions, not paths: a project whose sessions are all automated (`n_automated = n_sessions`, for example `/private/tmp/helperbot` 356 and `/private/tmp/helperbot2` 58 once DM-25 runs) is hidden from rankings by the default filter (S1/S3). No path list. The rule requires `n_sessions > 0`: a project with no session that has model calls is never a helper and never all-automated (D41 N2). Test: a project whose only session has 0 llm_calls has `n_sessions = 0` and is not hidden as a helper. See Q4.

### 4.7 Session bounds

- **DM-62 [v4]** `sessions.first_ms`/`last_ms` and the session op's `start_ms`/`end_ms` cover every owned leaf and every container in the subtree (the load contract already widens containers; the session root is not widened today). Fixes the 39 llm_calls after `last_ms` in d4e5f6a8 and c3d4e5f7. Test: an agent transcript whose last call ends 5 min after the main transcript's last record → `last_ms` = that call's end.

## 5. Data defects: fix or display

| Defect (source) | Current count | Disposition | Req |
|---|---|---|---|
| Sessions with no title (ui-redesign-inputs; deferred "Session display title") | 795 of 1,622 | Fix: DM-10–12 | v4 |
| omp turns labelled `user prompt` (deferred T8) | 818 of 975 | Fix: DM-22 | v4 |
| Claude slash turns labelled with the bare command; built-ins counted as skills | 515 slash events, ≥ 299 built-in | Fix: DM-21 | v4 |
| Turns with null `incl_tok_total` (deferred T8 says 373; that was a sample) | 3,774 | Not a defect: 2,591 turns have no llm_call (mostly harness records: interrupts, `/clear`, `/compact`, empty), 1,150 have only copied calls, 33 have calls with missing tokens. Display: harness turns fold (DM-24); a non-harness turn with no owned call shows "no model calls"; null tokens show `—`, never 0 (invariant 6). | v4 |
| Turns with null start/end (deferred T8 says 49) | 198 (claude, no calls, no tools) | Display: fold with harness turns; they get `display_ix` only if kind is human/message, and sort by `seq` | v4 |
| llm_calls after `sessions.last_ms` (deferred T8) | 39 in 2 sessions | Fix: DM-62 | v4 |
| 10 h Read, 2h29m Bash (deferred T8, panel-detail) | 26 tool calls > 1 h, 20 derived | Fix and display: DM-40 | v4 |
| "Session 50 h at $0" (deferred T8) | now one 11.4 h claude session with 0 llm_calls (a7b8c9d1); 98 sessions have 0 owned calls | Display: cost `—` with "no model calls"; such sessions are automated or not by DM-25 like any other | v4 |
| UTC day buckets (B1) | Oct 7: $482 UTC vs $549.89 local | Fix: DM-41 | v4 |
| Whole session cost on start day (deferred "Day totals") | Oct 6 $619 vs $301 | Fix: DM-42 | v4 |
| `steps.skill` NULL everywhere | 536,820 | Not used for loads; DM-30 adds `skill_load`. `steps.skill` stays the D15 attribution column. | v4 |
| Codex SKILL.md shell reads (deferred) | 795 events | Fix: DM-31 | v4 |
| Agent cost per turn (deferred "Turn detail data gaps") | 14 engagements change turn | Fix: DM-45 | v4 |
| llm_calls without own step rows | 46,119 calls, $2,029.14 | Reader rule DM-46 | v4 |
| Agent run time not stored (deferred) | engagement times exist for 6,693 | Reader rule DM-36 | v4 |
| Claude has no ttft; derived `dur_ms` (deferred) | 0 of 189,107 ttft | Display basis: DM-37, DM-47 | v4 |
| `n_errors` as list signal (deferred "Session-level failure signal") | 144 of 212 sessions in the sample week | List shows no error mark (D32). Failure signal: DM-80 | later |
| `args_json` empty; prompts and tool output not stored (deferred; REVIEW-v4-detail §Data 3, 5) | 282,457 tool calls | By design (D4, invariant 7): detail views read text through `src` (S3 owns the endpoint). No capture change. | v4 (S3) |
| Agent ask (spawn prompt) not stored | — | Same as above: the engagement's `caused_by` tool_call `src` points at the prompt | v4 (S3) |
| Top project by count is a bot (`/private/tmp/helperbot`, 356) | 356 + 58 | DM-25, DM-61 | v4 |
| Hotspots default query 5.8–8.2 s (deferred T8) | — | No data change. S3: start from narrow scopes or turns (turn-only 0.05 s). | later |
| Baseline ("4.2× median") definitions disagree (ui-redesign-inputs) | — | Dropped for v4: D29 rules out analysis; no baseline is stored. | — |
| Gap attribution "waiting for you" (ui-redesign-inputs) | — | Replaced by D34: no-activity stretches (DM-50–52); the next turn's kind is a fact, not a cause. D54 adds the recorded user-wait calls (DM-40) | v4 |
| `taxonomy.clip` trailing space (deferred) | — | Fix when touching `common/turns.clip`: strip after cutting | later |
| omp async job durations; Codex/omp `git_branch`/version gaps (deferred) | — | No source field; display `—` | later |
| Rejected bundle visible only in one run's report (deferred T11) | 0 rejected now | `source_files.status` gains `rejected` when the UI needs a list | later |
| Prefix-resume (deferred T11) | second run ~6 s | Not needed until incremental runs exceed ~30 s | later |

## 6. Later items

- **DM-80 [later]** Session failure signal (deferred "Session-level failure signal"). Proposed field `sessions.end_state` from the last non-harness main turn: `interrupted` (a harness `interrupt` turn follows it, or Codex `turn_aborted`), `limit` (a `limit_hit` event after its last llm_call), `api_error` (its last llm_call has `error_kind`, or an `api_error` event follows the last call), else `ok`. Facts only; whether and how the list shows it is for the maintainer (D32 shows no error marks). Test: a fixture whose last turn ends with `[Request interrupted by user]` → `interrupted`.
- **DM-90 [later]** The pipeline rebuilds every bundle of a provider when `meta.parser_version` differs from `PARSER_VERSION` (today a bump has no effect, DM-2).
- **DM-91 [later]** Persist rejected bundles (`source_files.status = 'rejected'`).

## 7. Summary: required for v4

DM-1–3 (v7 rebuild), DM-6–9 and DM-13–16 (content references, previews, compact storage, size target), DM-10–12 (titles), DM-20–26 (turn kinds, human asks, automated, omp labels, slash rule, numbering), DM-30–31 (skill loads), DM-36–37 and DM-40 (agent run time rule, speed basis, tool duration flags), DM-41–47 (epoch buckets and the request zone, session × day, bins, attributed turn cost, llm_call rows, model speed), DM-4–5 (swap rebuild, ANALYZE), DM-50–55 (time split, stretches, span figures, period membership, joins), DM-60–62 (project names, automated projects, session bounds).

Later: DM-32, DM-80, DM-90, DM-91, and the "later" rows of §5.

## 8. Resolved (Kai 2026-10-12)

All recommendations accepted as written.

| Q | Ruling |
|---|---|
| Q1 | `display_ix` shifts display ids; accepted. Ids are positional shorthand; every call also accepts the stable `op_id` (S3). |
| Q2 | "No records" threshold for derived tool durations (DM-40) = 5 min (300,000 ms). |
| Q3 | Minimum stored stretch (DM-52) = 60 s; D37 extends storage to `agents` stretches. |
| Q4 | No path list for helper projects; the session rule (DM-25, DM-61) marks them automated. |
| Q5 | A Codex `exec` prompt is not a human ask. |
| Q6 | Store `next_turn_kind`. The UI labels a gap "Waiting on you" exactly when `next_turn_kind = 'human'`. That label rests on the stored fact; D34 forbids it without that fact. [D54] Superseded in form: the load decides the label from the same fact (DM-50 rule 5) and stores it as the stretch's state and substate; `next_turn_kind` is dropped. |
| Q7 | Agent run time across engagements = union of engagement intervals (equals the sum when they do not overlap). |

## Sources

decisions.md D4, D15, D21, D26, D29, D32, D33, D34; deferred.md (all sections); evidence/ui-redesign-inputs.md; evidence/spec-inventory.md §4; docs/design/design.md §4, §7, §8; prototype notes (not in this repo)/BRIEF.md, REVIEW-v4-session-r2.md, REVIEW-v4-turn-r2.md, REVIEW-v4-steps-r3.md, REVIEW-v4-detail.md, REVIEW-v4-overview.md, REVIEW-v4-list-r2.md, CONSOLIDATION-NOTES.md; code: store/schema.py, store/load.py, store/ingest.py, pipeline.py, adapters/claude/tree.py, adapters/codex/tree.py, adapters/codex/steps.py, adapters/omp/adapter.py.
