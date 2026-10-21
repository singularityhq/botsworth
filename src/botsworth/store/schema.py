"""CONTRACT: the SQLite schema of the store (design §4.2 store tables, §4.7, §4.11; data-model.md §2.2, §2.3).

GUARANTEES:
- `DDL` creates every store table, index and view (schema v11): ops, steps, events, sessions,
  source_files, src_files, names, accounts, price_table, model_aliases, rollup_time,
  stretches, time_split, projects, meta; views `ops_v`, `steps_v`, `events_v`. Column names equal the
  canonical record field names (`botsworth.model`) except where DM-8/DM-14 compact them: a content
  reference is (`src_file`, `src_loc`, `src_h`); `cwd`, `account`, `path_sig` are `names` ids;
  `parent`/`caused_by` are rowids; `session_id`, `bundle`, `project` are read through `sessions`;
  `occ_key` is `occ_hash`.
  JSON-valued fields (`events.fields`, `steps.sub_items`, `ops.segments`, `sessions.activity_tok`) are
  TEXT holding JSON. `source_files` is the store's only change manifest (D52): one row per provider
  file, `reject_reason` set on every file of a bundle the store refused to load.
  `events.copied` = 1 when the event's op is a duplicate (non-owner) copy.
- The views return every v6 column name that v7 dropped (`ops_v`: `parent`, `caused_by`, `session_id`,
  `bundle`, `project`, `cwd`, `account`, `path_sig`, `src`; `steps_v`: `turn_id`, `op_id`, `bundle`,
  `src`; `events_v`: `bundle`, `src`) next to the table's own columns, so a reader that has not moved
  to the v7 columns keeps working. `ops_v` has no `occ_key` and no `args_json` (neither is stored).
- Op tree: `pre`/`post` nested intervals (DM-15) replace `op_closure`.
- v11 (D55): `ops.bg_end_ms`, `command_class`, `tests_passed`, `tests_failed` (tool_call only; DM-41,
  DM-28, DM-29); `stretches.state` also allows `tool` (`background` only, DM-52); `notice` events
  (DM-41) are ordinary `events` rows.
- `ensure(conn)` creates the schema on an empty database and, when `meta.schema_version` differs from
  `SCHEMA_VERSION`, drops every table and view (also the v6 `op_closure`) and recreates them (the store
  is a disposable cache over the provider logs, D52). It sets WAL mode. The swap
  rebuild (DM-4) is `store.ingest`'s, not this module's.
- `rollup_time` provenance counts cover owned llm_calls only (v6): `n_tok_missing`
  (tok_prov missing or tok_total null), `n_time_derived|estimated|missing`
  (time_prov; a null dur_ms counts as missing), so grouped llm token/time sums keep
  their weakest provenance without an op scan.
- `ops_commands` is a partial covering index for command_key queries (v6): every
  column the query layer's command distribution reads is in it (index-only scan).
- At most one owner per `(provider, kind, occ_hash)` among leaf ops with
  `duplicate_of IS NULL` (unique partial index `owner_key`, invariant 5).
EXPECTS: a sqlite3 connection the caller owns.
FAILURE BEHAVIOR: sqlite3 errors propagate.
DOES NOT: load, roll up, or query data; fill any B3 derived column (they exist and are NULL).
KNOWN LIMITATIONS: `ops.path_sig_id` is a group_by key named in design §5 but not
  defined there.
"""

from __future__ import annotations

import sqlite3

from botsworth.store import refs

SCHEMA_VERSION = 11

# design §4.2 op record (canonical names), then store-only and derived columns (data-model.md §2.2).
# Dropped from v6 (DM-8, DM-13, DM-14): parent, caused_by, session_id, bundle, project, cwd, account,
# path_sig, occ_key, src, args_json.
_OP_COLUMNS = """
  rowid INTEGER PRIMARY KEY,
  op_id TEXT NOT NULL, kind TEXT NOT NULL, seq INTEGER, depth INTEGER,
  caused_by_rowid INTEGER, caused_by_basis TEXT,
  provider TEXT NOT NULL, agent_id TEXT, engagement_ix INTEGER,
  cwd_id INTEGER, account_id INTEGER, agent_type TEXT, agent_name TEXT,
  spawn_depth INTEGER, is_fork INTEGER, occ_hash BLOB, duplicate_of TEXT, id_prov TEXT,
  start_ms INTEGER, end_ms INTEGER, dur_ms INTEGER, time_prov TEXT,
  ttft_ms INTEGER, ttft_prov TEXT, is_async INTEGER,
  in_tok INTEGER, cache_read_tok INTEGER, cache_write_tok INTEGER,
  cache_write_5m_tok INTEGER, cache_write_1h_tok INTEGER,
  out_tok INTEGER, reasoning_tok INTEGER, context_tokens INTEGER, tok_prov TEXT,
  cost_usd REAL, cost_in_usd REAL, cost_out_usd REAL, cost_cache_read_usd REAL, cost_cache_write_usd REAL,
  cost_basis TEXT, price_alias TEXT, price_fingerprint TEXT,
  cut_off INTEGER, stop_reason TEXT, error_kind TEXT, model TEXT, model_context_window INTEGER,
  tool_name TEXT, command TEXT, command_norm TEXT, command_key TEXT,
  file_path TEXT, exit_code INTEGER, exit_code_prov TEXT,
  is_error INTEGER, interrupted INTEGER, result_bytes INTEGER, role TEXT,
  label TEXT, segments TEXT,
  src_file INTEGER NOT NULL, src_loc TEXT NOT NULL, src_h BLOB,
  title TEXT, git_branch TEXT, harness_version TEXT, first_line_ms INTEGER,
  parent_rowid INTEGER, root_rowid INTEGER, path_sig_id INTEGER, pre INTEGER, post INTEGER,
  incl_in_tok INTEGER, incl_cache_read_tok INTEGER, incl_cache_write_tok INTEGER,
  incl_cache_write_5m_tok INTEGER, incl_cache_write_1h_tok INTEGER,
  incl_out_tok INTEGER, incl_reasoning_tok INTEGER, tok_total INTEGER, incl_tok_total INTEGER,
  incl_cost_usd REAL, incl_cost_in_usd REAL, incl_cost_out_usd REAL,
  incl_cost_cache_read_usd REAL, incl_cost_cache_write_usd REAL,
  incl_llm_ms INTEGER, incl_tool_ms INTEGER,
  wall_ms INTEGER, active_ms INTEGER, busy_sum_ms INTEGER, blocked_ms INTEGER, idle_ms INTEGER,
  max_parallel INTEGER,
  n_llm_calls INTEGER, n_tool_calls INTEGER, n_engagements INTEGER, n_errors INTEGER,
  n_steps INTEGER, n_duplicates INTEGER, n_inverted INTEGER,
  incl_tok_prov TEXT, incl_time_prov TEXT, cost_lower_bound INTEGER,
  turn_kind TEXT, turn_origin TEXT, display_ix INTEGER,
  attr_turn_rowid INTEGER, attr_cost_usd REAL, attr_tok_total INTEGER, attr_n_llm_calls INTEGER,
  attr_cost_lower_bound INTEGER,
  span_wall_ms INTEGER, max_silence_ms INTEGER, user_wait TEXT,
  bg_end_ms INTEGER, command_class TEXT, tests_passed INTEGER, tests_failed INTEGER
"""

_STEP_COLUMNS = """
  rowid INTEGER PRIMARY KEY, root_rowid INTEGER, turn_rowid INTEGER NOT NULL, ordinal INTEGER NOT NULL,
  op_rowid INTEGER NOT NULL, verb TEXT NOT NULL,
  tool_name TEXT, target TEXT, outcome TEXT, summary TEXT, snippet TEXT,
  start_ms INTEGER, dur_ms INTEGER, skill TEXT, skill_load TEXT, sub_items TEXT,
  src_file INTEGER NOT NULL, src_loc TEXT NOT NULL, src_h BLOB
"""

_EVENT_COLUMNS = """
  rowid INTEGER PRIMARY KEY, root_rowid INTEGER,
  event_id TEXT NOT NULL, kind TEXT NOT NULL, ts_ms INTEGER, session_id TEXT NOT NULL,
  op_id TEXT, provider TEXT, account TEXT, project TEXT, basis TEXT, fields TEXT NOT NULL,
  src_file INTEGER NOT NULL, src_loc TEXT NOT NULL, src_h BLOB, copied INTEGER NOT NULL DEFAULT 0
"""


def _names(columns: str) -> list[str]:
    return [line.split()[0] for chunk in columns.split(",") if (line := chunk.strip())]


OP_TABLE_COLUMNS = _names(_OP_COLUMNS)
STEP_TABLE_COLUMNS = _names(_STEP_COLUMNS)
EVENT_TABLE_COLUMNS = _names(_EVENT_COLUMNS)


def _view(name: str, table: str, columns: list[str], extra: dict[str, str], joins: str) -> str:
    """A view of `table` (alias `o`): its own columns, then the v6 names in `extra` (name -> expression)."""
    parts = [f"o.{c}" for c in columns] + [f"{expr} AS {alias}" for alias, expr in extra.items()]
    return f"CREATE VIEW {name} AS SELECT {', '.join(parts)} FROM {table} o {joins};"


_OPS_V = _view(
    "ops_v",
    "ops",
    OP_TABLE_COLUMNS,
    {
        "parent": "po.op_id",
        "caused_by": "cb.op_id",
        "session_id": "ss.session_id",
        "bundle": "ss.bundle",
        "project": "ss.project",
        "cwd": "nc.s",
        "account": "na.s",
        "path_sig": "np.s",
        "src": refs.src_sql("o", "sf"),
    },
    "JOIN sessions ss ON ss.rowid = o.root_rowid"
    " JOIN src_files sf ON sf.rowid = o.src_file"
    " LEFT JOIN ops po ON po.rowid = o.parent_rowid"
    " LEFT JOIN ops cb ON cb.rowid = o.caused_by_rowid"
    " LEFT JOIN names nc ON nc.id = o.cwd_id"
    " LEFT JOIN names na ON na.id = o.account_id"
    " LEFT JOIN names np ON np.id = o.path_sig_id",
)
_STEPS_V = _view(
    "steps_v",
    "steps",
    STEP_TABLE_COLUMNS,
    {
        "turn_id": "t.op_id",
        "op_id": "so.op_id",
        "bundle": "ss.bundle",
        "src": refs.src_sql("o", "sf"),
    },
    "JOIN ops t ON t.rowid = o.turn_rowid"
    " JOIN ops so ON so.rowid = o.op_rowid"
    " JOIN sessions ss ON ss.rowid = o.root_rowid"
    " JOIN src_files sf ON sf.rowid = o.src_file",
)
_EVENTS_V = _view(
    "events_v",
    "events",
    EVENT_TABLE_COLUMNS,
    {"bundle": "ss.bundle", "src": refs.src_sql("o", "sf")},
    "JOIN sessions ss ON ss.rowid = o.root_rowid JOIN src_files sf ON sf.rowid = o.src_file",
)

DDL = f"""
CREATE TABLE src_files(rowid INTEGER PRIMARY KEY, provider TEXT NOT NULL, relpath TEXT NOT NULL,
  prefix TEXT NOT NULL, UNIQUE(provider, relpath));
CREATE TABLE names(id INTEGER PRIMARY KEY, s TEXT NOT NULL UNIQUE);

CREATE TABLE ops({_OP_COLUMNS});
CREATE UNIQUE INDEX ops_op_id ON ops(op_id);
CREATE INDEX ops_parent_cost ON ops(parent_rowid, incl_cost_usd DESC);
CREATE INDEX ops_parent_tok ON ops(parent_rowid, incl_tok_total DESC);
CREATE INDEX ops_root_kind ON ops(root_rowid, kind, cost_usd);
CREATE INDEX ops_root_pre ON ops(root_rowid, pre);
CREATE INDEX ops_kind_start ON ops(kind, start_ms);
CREATE INDEX ops_kind_cost ON ops(kind, incl_cost_usd DESC);
CREATE INDEX ops_occ ON ops(occ_hash);
CREATE INDEX ops_command_key ON ops(command_key) WHERE command_key IS NOT NULL;
CREATE INDEX ops_commands ON ops(command_key, command_norm, dur_ms, time_prov, root_rowid, start_ms, end_ms)
  WHERE kind = 'tool_call' AND duplicate_of IS NULL AND command_key IS NOT NULL;
CREATE UNIQUE INDEX owner_key ON ops(provider, kind, occ_hash)
  WHERE kind IN ('llm_call', 'tool_call') AND duplicate_of IS NULL AND occ_hash IS NOT NULL;
CREATE INDEX ops_attr_turn ON ops(attr_turn_rowid) WHERE attr_turn_rowid IS NOT NULL;
CREATE INDEX ops_parent_kind_start ON ops(parent_rowid, kind, start_ms);
CREATE INDEX ops_caused_by ON ops(caused_by_rowid) WHERE caused_by_rowid IS NOT NULL;

CREATE TABLE steps({_STEP_COLUMNS});
CREATE UNIQUE INDEX steps_turn ON steps(turn_rowid, ordinal);
CREATE INDEX steps_op ON steps(op_rowid);
CREATE INDEX steps_root ON steps(root_rowid);
CREATE INDEX steps_skill_load ON steps(root_rowid) WHERE skill_load IS NOT NULL;

CREATE TABLE events({_EVENT_COLUMNS});
CREATE UNIQUE INDEX events_id ON events(event_id);
CREATE INDEX events_kind_time ON events(kind, ts_ms);
CREATE INDEX events_session ON events(session_id, ts_ms);
CREATE INDEX events_op ON events(op_id);
CREATE INDEX events_root ON events(root_rowid);

CREATE TABLE sessions(
  rowid INTEGER PRIMARY KEY,
  op_rowid INTEGER NOT NULL, bundle TEXT NOT NULL,
  provider TEXT NOT NULL, session_id TEXT NOT NULL, project TEXT, account TEXT, cwd_first TEXT,
  git_branch TEXT, title TEXT, harness_version TEXT, entrypoint TEXT, originator TEXT,
  first_ms INTEGER, first_line_ms INTEGER, last_ms INTEGER, status TEXT,
  check_cost_usd REAL, check_api_ms INTEGER, check_api_no_retry_ms INTEGER, check_tool_ms INTEGER,
  n_malformed INTEGER, n_duplicates INTEGER,
  incl_in_tok INTEGER, incl_cache_read_tok INTEGER, incl_cache_write_tok INTEGER,
  incl_out_tok INTEGER, incl_reasoning_tok INTEGER, incl_tok_total INTEGER,
  incl_cost_usd REAL, incl_llm_ms INTEGER, incl_tool_ms INTEGER,
  wall_ms INTEGER, active_ms INTEGER, busy_sum_ms INTEGER, blocked_ms INTEGER, idle_ms INTEGER,
  max_parallel INTEGER, n_llm_calls INTEGER, n_tool_calls INTEGER, n_engagements INTEGER,
  n_errors INTEGER, n_steps INTEGER, incl_tok_prov TEXT, incl_time_prov TEXT, cost_lower_bound INTEGER,
  title_source TEXT, display_title TEXT, n_human_asks INTEGER, is_automated INTEGER,
  activity_bin_ms INTEGER, activity_tok TEXT);
CREATE UNIQUE INDEX sessions_key ON sessions(provider, session_id);
CREATE UNIQUE INDEX sessions_op ON sessions(op_rowid);
CREATE INDEX sessions_project_time ON sessions(project, first_ms);
CREATE INDEX sessions_account_time ON sessions(account, first_ms);
CREATE INDEX sessions_automated_time ON sessions(is_automated, first_ms);

CREATE TABLE source_files(
  path TEXT PRIMARY KEY, provider TEXT NOT NULL, bundle_id TEXT NOT NULL,
  size INTEGER, mtime_ns INTEGER, sha_first_4k TEXT,
  lines_ingested INTEGER, bytes_ingested INTEGER, parser_version TEXT, ingested_at_ms INTEGER,
  status TEXT CHECK (status IN ('live', 'settled', 'source_missing')),
  reject_reason TEXT);
CREATE INDEX source_files_bundle ON source_files(provider, bundle_id);

CREATE TABLE accounts(
  account TEXT PRIMARY KEY, provider_family TEXT, plan TEXT, credential_id TEXT,
  window_5h_anchor_ms INTEGER, window_week_anchor_ms INTEGER, anchor_basis TEXT);

CREATE TABLE price_table(
  model_id TEXT NOT NULL, provider TEXT NOT NULL, snapshot_date TEXT NOT NULL,
  in_usd_m REAL, out_usd_m REAL, cache_read_usd_m REAL, cache_write_5m_usd_m REAL, cache_write_1h_usd_m REAL,
  PRIMARY KEY(provider, model_id));
CREATE TABLE model_aliases(alias TEXT NOT NULL, model_id TEXT NOT NULL, provider TEXT NOT NULL,
  PRIMARY KEY(provider, alias));

CREATE TABLE rollup_time(
  root_rowid INTEGER NOT NULL, bucket_start_ms INTEGER NOT NULL,
  provider TEXT, account TEXT, model TEXT, project TEXT, agent_type TEXT,
  n_llm_calls INTEGER, n_tool_calls INTEGER,
  in_tok INTEGER, out_tok INTEGER, cache_read_tok INTEGER, cache_write_tok INTEGER, reasoning_tok INTEGER,
  tok_total INTEGER, cost_usd REAL, n_unpriced INTEGER, llm_ms INTEGER, tool_ms INTEGER,
  n_errors INTEGER, max_dur_ms INTEGER,
  n_tok_missing INTEGER, n_time_derived INTEGER, n_time_estimated INTEGER, n_time_missing INTEGER,
  timed_out_tok INTEGER, timed_ms INTEGER, n_ttft INTEGER, ttft_ms_sum INTEGER);
CREATE INDEX rollup_bucket ON rollup_time(bucket_start_ms, root_rowid);
CREATE INDEX rollup_root ON rollup_time(root_rowid);

CREATE TABLE stretches(
  root_rowid INTEGER NOT NULL, turn_rowid INTEGER,
  state TEXT NOT NULL CHECK (state IN ('agents', 'tool', 'you', 'agentmsg', 'limit', 'none')),
  substate TEXT,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL);
CREATE INDEX stretches_root ON stretches(root_rowid, start_ms);
CREATE INDEX stretches_len ON stretches(state, (end_ms - start_ms) DESC);

CREATE TABLE time_split(
  root_rowid INTEGER NOT NULL,
  op_rowid INTEGER NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('session', 'window', 'span')),
  state TEXT NOT NULL CHECK (state IN ('model', 'tool', 'agents', 'you', 'agentmsg', 'limit', 'none')),
  substate TEXT,
  ms INTEGER NOT NULL);
CREATE INDEX time_split_op ON time_split(op_rowid, scope);
CREATE INDEX time_split_root ON time_split(root_rowid, scope);

CREATE TABLE projects(
  project TEXT PRIMARY KEY, name TEXT NOT NULL,
  n_sessions INTEGER, n_automated INTEGER, first_ms INTEGER, last_ms INTEGER);

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

{_OPS_V}
{_STEPS_V}
{_EVENTS_V}
"""

TABLES = (
    "ops",
    "steps",
    "events",
    "sessions",
    "source_files",
    "src_files",
    "names",
    "accounts",
    "price_table",
    "model_aliases",
    "rollup_time",
    "stretches",
    "time_split",
    "projects",
    "meta",
)
VIEWS = ("ops_v", "steps_v", "events_v")
# v6 tables that v7 no longer creates; an in-place upgrade still drops them
LEGACY_TABLES = ("op_closure", "stream_files")
# meta keys (design §4.2, build-plan §3)
META_KEYS = (
    "schema_version",
    "parser_version",
    "price_fingerprint",
    "last_ingest_ms",
    "n_ownership_disagreements",
)


def _version(conn: sqlite3.Connection) -> int | None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return None
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else None


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    if _version(conn) == SCHEMA_VERSION:
        return
    script = "".join(f"DROP VIEW IF EXISTS {v};" for v in VIEWS)
    script += "".join(f"DROP TABLE IF EXISTS {t};" for t in (*TABLES, *LEGACY_TABLES)) + DDL
    script += f"INSERT INTO meta(key, value) VALUES ('schema_version', '{SCHEMA_VERSION}');"
    conn.executescript("BEGIN;" + script + "COMMIT;")
