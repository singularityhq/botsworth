"""CONTRACT: the analysis frames of the REPL (interfaces.md REPL-5, D58): the store as pandas DataFrames.

GUARANTEES:
- `load(db, tz, since, until, sessions)` opens the store read-only (`mode=ro`), checks the schema version and
  returns a `Frames`. `Frames.<name>` for each name in `NAMES` (`calls`, `tool_calls`, `threads`, `turns`,
  `sessions`, `events`, `prices`) builds that frame on first use. Every connection is closed before the
  frame is returned; `Frames` holds none. Nothing is written.
- One row per thing, counted once: `calls` and `tool_calls` hold owned rows only (`duplicate_of IS NULL`), so
  cost and
  usage count once per requestId group; `events` holds no copies. Σ `calls.cost_usd` of a session equals the
  stored `sessions.cost_usd`.
- Rollups are stored values (`threads.incl_cost_usd`, the session cost parts and time split); no second
  implementation of them exists here. Own cost columns are sums of owned `llm_call` rows.
- Scope: `since` / `until` are local days (`YYYY-MM-DD`, inclusive, in `tz`); `sessions` is a list of session
  ids.
  A session is in scope when it started in the day range and is listed (when given). A frame then holds every
  row of those sessions, so per-session sums still match the stored rollups and `gap_ms` / `thread_pos` are
  never cut.
- `local_day` and `local_hour` are the local day and hour of the row's start (events: `ts_ms`) in `tz`, DST
  correct.
- `threads.depth` is nesting taken from `parent_thread_id` (main thread 0, a thread started from the main
  thread
  1, ...); `spawn_depth` is not a frame column because it reads 0 for Claude engagements started by an agent.
- `prices` holds one row per model name the bundled `Pricer` resolves: catalog ids, alias names and `[1m]`
  names, per provider. `calls.provider` + `calls.model` therefore find exactly one row by equality.
EXPECTS: pandas is installed (the module imports it); `tz` is an IANA zone name; a store of the current
  schema.
FAILURE BEHAVIOR: a missing store raises `NoStore`; a store of another schema version raises `SchemaMismatch`
  with the store's message; an unknown zone, a malformed day or a session id that is not in the store raises
  `QueryError`. A store with no sessions gives empty frames.
DOES NOT: write the store, cache a frame across `load` calls, hold a connection, or read text of the sessions
  (every row carries `src`, a pointer for `pointer.resolve`, D39).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from botsworth.pricing import CATALOG_OF, Pricer
from botsworth.query import (
    SCHEMA_VERSION,
    NoStore,
    QueryError,
    _is_current,
    _meta,
    _mismatch,
    _open_ro,
)
from botsworth.query.zone import day_bounds, zone_of

STATES = (
    "model",
    "tool",
    "agents",
    "you",
    "agentmsg",
    "limit",
    "none",
)  # DM-50 time_split states, fixed columns
NAMES = ("calls", "tool_calls", "threads", "turns", "sessions", "events", "prices")


def _src(alias: str) -> str:
    """src:// pointer of a row (DM-8): store prefix + '#' + src_loc + ?h=<hex> when src_h is set."""
    return (
        f"(SELECT prefix FROM src_files WHERE rowid = {alias}.src_file) || '#' || {alias}.src_loc || "
        f"CASE WHEN {alias}.src_h IS NULL THEN '' ELSE '?h=' || lower(hex({alias}.src_h)) END"
    )


def _local(df: pd.DataFrame, ms_col: str, tz: str) -> pd.DataFrame:
    """Add local_day ('YYYY-MM-DD') and local_hour (0-23) of an epoch-ms column, in `tz` (DST correct).

    Zone offsets are multiples of 15 minutes, so day and hour are constant inside a 15-minute bucket of
    epoch time; the conversion runs once per distinct bucket, not once per row.
    """
    bucket = (df[ms_col] // 900_000).astype("Int64")
    uniq = pd.Series(bucket.dropna().unique()).astype("int64")
    t = pd.to_datetime(uniq * 900_000, unit="ms", utc=True).dt.tz_convert(tz)
    df["local_day"] = bucket.map(pd.Series(t.dt.strftime("%Y-%m-%d").values, index=uniq.values))
    df["local_hour"] = bucket.map(pd.Series(t.dt.hour.values, index=uniq.values)).astype("Int64")
    return df


_COUNTS = (
    "failed", "automated", "n_calls", "n_tools", "depth", "pre_tokens", "post_tokens", "duration_ms",
    "is_fork", "is_error", "exit_code", "interrupted", "result_bytes",
    "n_human_turns", "n_agents", "display_ix",
)  # fmt: skip


def _types(df: pd.DataFrame) -> pd.DataFrame:
    """Fix column types whatever the data: integers with NULLs are Int64, money is float64."""
    for c in df.columns:
        if c.endswith(("_ms", "_tok", "_ix", "_pos", "_rowid")) or c in _COUNTS:
            df[c] = pd.to_numeric(df[c]).astype("Int64")
        elif c.endswith("_usd"):
            df[c] = pd.to_numeric(df[c]).astype("float64")
    if "is_main" in df.columns:
        df["is_main"] = df["is_main"].astype("boolean")
    return df


# thread of an op = the op that opens it (DM-92); the session root is `so`; main thread <=> th = so
_THREAD = "LEFT JOIN ops th ON th.rowid = {a}.thread_rowid"
_MAIN = "(th.rowid = so.rowid)"


def _depth(threads: pd.DataFrame) -> pd.Series:
    """Nesting of each thread from `parent_thread_id`: main 0, started from main 1, from that 2, ..."""
    parent = dict(zip(threads["thread_id"], threads["parent_thread_id"], strict=True))
    cache: dict[str, int] = {}

    def depth(t: str) -> int:
        chain = []
        cur: str | None = t
        while cur is not None and cur not in cache:
            chain.append(cur)
            nxt = parent.get(cur)
            cur = None if pd.isna(nxt) else nxt
        base = -1 if cur is None else cache[cur]
        for i, c in enumerate(reversed(chain), 1):
            cache[c] = base + i
        return cache[t]

    return threads["thread_id"].map(depth).astype("Int64")


class Frames:
    """Lazy set of frames. Attribute access builds a frame once."""

    def __init__(self, db: Path, tz: str, meta: dict, roots: list[int] | None):
        self._db, self._tz, self.meta, self._cache = db, tz, meta, {}
        self._roots = roots  # rowids of the sessions in scope; None = all

    def _query(self, sql: str) -> pd.DataFrame:
        con = _open_ro(self._db)
        try:
            con.execute("PRAGMA query_only = 1")
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame.from_records(cur.fetchall(), columns=cols)
        finally:
            con.close()

    def _in(self, col: str) -> str:
        """SQL condition limiting `col` (a session rowid column) to the sessions in scope."""
        return "" if self._roots is None else f" AND {col} IN ({','.join(map(str, self._roots)) or 'NULL'})"

    def _get(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            self._cache[name] = getattr(self, f"_build_{name}")()
        return self._cache[name]

    calls = property(lambda s: s._get("calls"))
    tool_calls = property(lambda s: s._get("tool_calls"))
    turns = property(lambda s: s._get("turns"))
    threads = property(lambda s: s._get("threads"))
    sessions = property(lambda s: s._get("sessions"))
    events = property(lambda s: s._get("events"))
    prices = property(lambda s: s._get("prices"))

    # ---- builders (each reads only the store tables it needs) ----

    def _build_calls(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT l.op_id AS call_id, s.session_id, s.provider, s.project, na.s AS account, s.is_automated AS
            automated,
            th.op_id AS thread_id, {_MAIN} AS is_main, l.agent_id, th.agent_type,
            pt.op_id AS parent_turn_id, mt.op_id AS turn_id, l.model,
            l.start_ms, l.end_ms, l.dur_ms, l.in_tok, l.out_tok, l.reasoning_tok, l.cache_read_tok,
              l.cache_write_tok,
            l.cache_write_5m_tok, l.cache_write_1h_tok,
            COALESCE(l.in_tok,0) + COALESCE(l.cache_read_tok,0) + COALESCE(l.cache_write_tok,0) AS
              context_tok,
            l.cost_usd, l.cost_in_usd, l.cost_out_usd, l.cost_cache_read_usd, l.cost_cache_write_usd,
              l.cost_basis,
            l.stop_reason, l.error_kind, l.thread_pos, l.gap_ms, cb.op_id AS caused_by, cb.kind AS
              caused_by_kind,
            l.effect, (SELECT count(*) FROM ops tc WHERE tc.parent_rowid = l.rowid AND tc.kind = 'tool_call'
                         AND tc.duplicate_of IS NULL) AS n_tools, {_src("l")} AS src
          FROM ops l JOIN sessions s ON s.rowid = l.root_rowid JOIN ops so ON so.rowid = s.op_rowid
          LEFT JOIN names na ON na.id = l.account_id
          LEFT JOIN ops pt ON pt.rowid = l.parent_rowid
          {_THREAD.format(a="l")}
          LEFT JOIN ops cb ON cb.rowid = l.caused_by_rowid
          LEFT JOIN ops mt ON mt.rowid = l.attr_turn_rowid
          WHERE l.kind = 'llm_call' AND l.duplicate_of IS NULL{self._in("l.root_rowid")}""")
        return _local(_types(df), "start_ms", self._tz)

    def _build_tool_calls(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT tc.op_id AS tool_id, pc.op_id AS call_id, s.session_id, th.op_id AS thread_id, {_MAIN} AS
            is_main,
            th.agent_type, mt.op_id AS turn_id, pc.model, tc.tool_name, tc.command_key, tc.command_norm,
              tc.command,
            tc.file_path, tc.effect, tc.start_ms, tc.dur_ms, tc.is_error, tc.exit_code, tc.interrupted,
            tc.result_bytes, tc.user_wait,
            CASE WHEN tc.is_error = 1 OR COALESCE(tc.exit_code,0) <> 0 OR tc.interrupted = 1 OR EXISTS (
              SELECT 1 FROM steps st WHERE st.op_rowid = tc.rowid
                AND (st.outcome IN ('error','interrupted') OR (st.outcome LIKE 'exit:%' AND st.outcome <>
                  'exit:0')))
              THEN 1 ELSE 0 END AS failed,
            pc.cost_usd AS _call_cost, {_src("tc")} AS src
          FROM ops tc JOIN sessions s ON s.rowid = tc.root_rowid JOIN ops so ON so.rowid = s.op_rowid
          LEFT JOIN ops pc ON pc.rowid = tc.parent_rowid
          {_THREAD.format(a="tc")}
          LEFT JOIN ops mt ON mt.rowid = tc.attr_turn_rowid
          WHERE tc.kind = 'tool_call' AND tc.duplicate_of IS NULL{self._in("tc.root_rowid")}""")
        # the issuing call's cost divided evenly among its owned tool calls
        df["call_cost_share_usd"] = df.pop("_call_cost") / df["call_id"].map(df["call_id"].value_counts())
        return _local(_types(df), "start_ms", self._tz)

    def _build_threads(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT t.op_id AS thread_id, s.session_id, (t.rowid = so.rowid) AS is_main,
            CASE WHEN t.rowid <> so.rowid THEN pth.op_id END AS parent_thread_id,
            t.agent_id, t.agent_type, t.agent_name, t.engagement_ix, t.is_fork,
            t.start_ms, t.end_ms, t.wall_ms,
            COALESCE(lc.n, 0) AS n_calls, COALESCE(lc.c, 0.0) AS cost_usd, t.incl_cost_usd,
            COALESCE(tcn.n, 0) AS n_tools, {_src("t")} AS src
          FROM ops t JOIN sessions s ON s.rowid = t.root_rowid JOIN ops so ON so.rowid = s.op_rowid
          LEFT JOIN ops pth ON pth.rowid = t.thread_rowid
          LEFT JOIN (SELECT thread_rowid AS t, count(*) AS n, sum(cost_usd) AS c FROM ops
                     WHERE kind = 'llm_call' AND duplicate_of IS NULL GROUP BY 1) lc ON lc.t = t.rowid
          LEFT JOIN (SELECT thread_rowid AS t, count(*) AS n FROM ops
                     WHERE kind = 'tool_call' AND duplicate_of IS NULL GROUP BY 1) tcn ON tcn.t = t.rowid
          WHERE (t.kind = 'engagement' OR t.rowid = so.rowid){self._in("t.root_rowid")}""")
        df["depth"] = _depth(df)
        return _local(_types(df), "start_ms", self._tz)

    def _build_sessions(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT s.session_id, s.provider, s.project, s.account, COALESCE(s.display_title, s.title) AS title,
            s.is_automated AS automated, so.op_id AS main_thread_id, s.first_ms, s.last_ms, s.wall_ms,
              s.active_ms,
            s.incl_cost_usd AS cost_usd, so.incl_cost_in_usd AS cost_in_usd, so.incl_cost_out_usd AS
              cost_out_usd,
            so.incl_cost_cache_read_usd AS cost_cache_read_usd, so.incl_cost_cache_write_usd AS
              cost_cache_write_usd,
            s.n_llm_calls AS n_calls, s.n_tool_calls AS n_tools, s.n_human_asks AS n_human_turns,
            s.n_engagements AS n_agents, s.op_rowid AS _op, {_src("so")} AS src
          FROM sessions s JOIN ops so ON so.rowid = s.op_rowid WHERE 1=1{self._in("s.rowid")}""")
        ts = self._query(
            "SELECT op_rowid AS _op, state, sum(ms) AS ms FROM time_split WHERE scope='session' GROUP BY 1,2"
        )
        parts = ts.pivot(index="_op", columns="state", values="ms").reindex(columns=STATES)
        parts = parts.add_suffix("_ms")
        df = df.merge(parts, left_on="_op", right_index=True, how="left").drop(columns="_op")
        # a session with no split row: 0, not NULL
        df[list(parts.columns)] = df[list(parts.columns)].fillna(0)
        return _local(_types(df), "first_ms", self._tz)

    def _build_events(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT ev.event_id, ev.kind, s.session_id, ev.provider, ev.project, ev.ts_ms,
            CASE WHEN eo.kind = 'llm_call' THEN eo.op_id END AS call_id, th.op_id AS thread_id, {_MAIN} AS
              is_main,
            json_extract(ev.fields,'$.trigger') AS trigger, json_extract(ev.fields,'$.pre_tokens') AS
              pre_tokens,
            json_extract(ev.fields,'$.post_tokens') AS post_tokens, json_extract(ev.fields,'$.duration_ms') AS
              duration_ms,
            CASE WHEN ev.kind='api_error' THEN json_extract(ev.fields,'$.kind') END AS error_kind,
            json_extract(ev.fields,'$.status') AS status, json_extract(ev.fields,'$.skill') AS skill,
            json_extract(ev.fields,'$.via') AS via, {_src("ev")} AS src
          FROM events ev JOIN sessions s ON s.rowid = ev.root_rowid JOIN ops so ON so.rowid = s.op_rowid
          LEFT JOIN ops eo ON eo.op_id = ev.op_id
          {_THREAD.format(a="eo")}
          WHERE ev.copied = 0{self._in("ev.root_rowid")}""")
        return _local(_types(df), "ts_ms", self._tz)

    def _build_turns(self) -> pd.DataFrame:
        df = self._query(f"""
          SELECT t.op_id AS turn_id, s.session_id, th.op_id AS thread_id, {_MAIN} AS is_main,
            t.turn_kind, t.turn_origin, t.display_ix, t.start_ms, t.end_ms,
            COALESCE(lc.n, 0) AS n_calls, COALESCE(lc.c, 0.0) AS cost_usd, t.attr_cost_usd, {_src("t")} AS src
          FROM ops t JOIN sessions s ON s.rowid = t.root_rowid JOIN ops so ON so.rowid = s.op_rowid
          {_THREAD.format(a="t")}
          LEFT JOIN (SELECT parent_rowid AS t, count(*) AS n, sum(cost_usd) AS c FROM ops
                     WHERE kind = 'llm_call' AND duplicate_of IS NULL GROUP BY 1) lc ON lc.t = t.rowid
          WHERE t.kind = 'turn'{self._in("t.root_rowid")}""")
        return _local(_types(df), "start_ms", self._tz)

    def _build_prices(self) -> pd.DataFrame:
        """Every model name the `Pricer` resolves, per provider, with its rates (USD per Mtok)."""
        pricer = Pricer()
        rows = []
        for provider, catalog in CATALOG_OF.items():
            names = set(pricer.catalog[catalog]["models"]) | set(pricer.aliases.get(catalog, {}))
            names |= {n + "[1m]" for n in names}
            for name in sorted(names):
                r = pricer.resolve(name, provider)
                if r is not None:
                    rows.append(
                        (
                            provider,
                            name,
                            r.model_id,
                            r.input,
                            r.output,
                            r.cache_read,
                            r.cache_write_5m,
                            r.cache_write_1h,
                        )
                    )
        self.meta["price_snapshot"] = pricer.snapshot_path.name
        return pd.DataFrame(
            rows,
            columns=[
                "provider", "model", "price_id", "in_per_mtok", "out_per_mtok",
                "cache_read_per_mtok", "cache_write_5m_per_mtok", "cache_write_1h_per_mtok",
            ],
        )  # fmt: skip


def load(
    db: str | Path,
    tz: str,
    since: str | None = None,
    until: str | None = None,
    sessions: list[str] | None = None,
) -> Frames:
    """Open the store read-only and return its frames, scoped as the module contract says."""
    path = Path(db).resolve()
    if not path.is_file():
        raise NoStore(f"no store at {path}")
    zone_of(tz)  # QueryError for an unknown zone
    con = _open_ro(path)
    try:
        meta = _meta(con)
        if not _is_current(meta):
            raise _mismatch(meta)
        roots = None
        if since or until or sessions:
            cond, args = ["1=1"], []
            if since:
                cond.append("first_ms >= ?")
                args.append(day_bounds(since, tz)[0])
            if until:
                cond.append("first_ms < ?")
                args.append(day_bounds(until, tz)[1])
            if sessions:
                cond.append(f"session_id IN ({','.join('?' * len(sessions))})")
                args += list(sessions)
            roots = [
                r[0] for r in con.execute(f"SELECT rowid FROM sessions WHERE {' AND '.join(cond)}", args)
            ]
            if sessions:
                have = {
                    r[0]
                    for r in con.execute(
                        "SELECT session_id FROM sessions WHERE session_id IN "
                        f"({','.join('?' * len(sessions))})",
                        list(sessions),
                    )
                }
                missing = sorted(set(sessions) - have)
                if missing:
                    raise QueryError(f"no such session: {', '.join(missing)}")
    finally:
        con.close()
    info = {
        "db": str(path),
        "schema_version": SCHEMA_VERSION,
        "tz": tz,
        "since": since,
        "until": until,
        "n_sessions_in_scope": None if roots is None else len(roots),
    }
    return Frames(path, tz, info, roots)
