"""CONTRACT: §5 query execution and the secondary reads over the SQLite store.

GUARANTEES:
- `run(conn, q)` executes a parsed `Q` (shapes.parse_query): scope (op |
  sessions | all), select (children | descendants | self | sessions | events |
  steps), where (eq/ne/in/contains/gte/gt/lte/lt), measure, metrics,
  group_by, bucket.time (hour/day/week edges in the zone `q.tz`; a week starts on local Monday, D50), agg
  (sum/count/max/p50/p90/p95/distribution), outliers, compare previous_bucket,
  order_by (any allowed metric, `n`, `start_ms`) and offset cursors bound to
  the query.
- Events with `copied = 1` (events of duplicate ops, store.load) are excluded
  from `select events` and `timeline`; `show` of the copy still lists them.
- Ops with `duplicate_of` are excluded from every op select except
  `select self` on an op scope (invariant 3: copies never count).
- `scope_totals` are the scope roots' inclusive values: the op itself for an
  op scope; the sum over the matching session ops for sessions/all (additive
  metrics only, others null). `share` = row value ÷ scope total for the order
  metric when that metric is additive and agg is sum (or rows are ops), else
  null; `n` ordering shares over the total count. A complete children list
  therefore has Σ share ≤ 1.
- Row prov: per metric; null value -> `missing`; aggregated rows carry the
  weakest member prov (a non-null sum with prov `missing` is a lower bound).
- `show`, `timeline`, `steps`, `source` return the §5.2 shapes (types.ts).
  `timeline` drops ops with end < start (counted on the op as n_inverted by
  the store) and duplicates.
EXPECTS: a connection with `analytics.register` applied and row_factory Row.
FAILURE BEHAVIOR: unknown ids -> `QueryError`; a cursor from another query ->
  `QueryError`; missing source file -> `pointer.SourceMissing` (HTTP 404).
DOES NOT: write to the store; read source files except in `source`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict
from functools import lru_cache
from typing import Any

from botsworth import pointer
from botsworth.query import QueryError, zone
from botsworth.query.outline import resolve_op, resolve_src
from botsworth.query.shapes import (
    ADDITIVE,
    AVAILABILITY,
    GROUPS,
    RANK_PROV,
    MetricSql,
    Q,
    event_wire,
    field_sql,
    freshness,
    op_metric,
    op_wire,
    step_wire,
)

_STEP_MS = 900_000  # every local edge in every zone is a multiple of this (DM-41)
_RANK_SQL = "CASE {p} WHEN 'present' THEN 3 WHEN 'derived' THEN 2 WHEN 'estimated' THEN 1 ELSE 0 END"
# v6-shaped reads go through the v7 views (schema.py CONTRACT): `session_id`, `src` ... are view columns
_FROM = {
    "ops": "ops_v o",
    "events": "events_v e",
    "steps": "steps_v s LEFT JOIN ops_v so ON so.rowid = s.op_rowid",
}
_TIME = {"ops": "o.start_ms", "events": "e.ts_ms", "steps": "s.start_ms"}
_LAST = {"ops": "COALESCE(o.end_ms, o.start_ms)", "events": "e.ts_ms", "steps": "s.start_ms"}
_SESSION = {"ops": "o.session_id", "events": "e.session_id", "steps": "so.session_id"}
_TIEBREAK = {"ops": "o.op_id", "events": "e.event_id", "steps": "s.turn_id, s.ordinal"}


@lru_cache(maxsize=1 << 16)
def _bucket_start(bucket: str, tz: str, quarter: int) -> int:
    ms = quarter * _STEP_MS
    if bucket == "hour":
        return zone.hour_edges(ms, ms, tz)[0]
    day = zone.local_day(ms, tz)
    return zone.day_bounds(day, tz)[0] if bucket == "day" else zone.week_edges(day, day, tz)[0]


def local_bucket(bucket: str, tz: str, ms: int | None) -> int | None:
    """Epoch start of the local hour, day or Monday-week of `ms` in `tz` (QRY-4, D50)."""
    return None if ms is None else _bucket_start(bucket, tz, ms // _STEP_MS)


def _bucket_sql(expr: str, bucket: str, tz: str) -> str:
    """SQL for the local bucket start of the epoch-ms expression `expr`; `bucket` and `tz` are validated."""
    return f"local_bucket('{bucket}', '{tz}', {expr})"


def _metric(q: Q, name: str) -> MetricSql:
    if q.kind == "ops":
        return op_metric(name, q.measure)
    if q.kind == "steps":
        return MetricSql(f"s.{name}", "COALESCE(so.time_prov, 'missing')")
    return MetricSql("e.ts_ms", "COALESCE(e.basis, 'missing')")


def _op_row(conn: sqlite3.Connection, op_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ops_v WHERE op_id = ?", (op_id,)).fetchone()
    if row is None:
        raise QueryError(f"unknown op {op_id!r}")
    return row


# ── scope ───────────────────────────────────────────────────────────────────


def _sessions_subquery(scope: dict[str, Any]) -> tuple[str, list[Any]]:
    pieces, args = ["kind = 'session'"], []
    for key, value in scope.get("sessions", {}).items():
        if key == "from":
            pieces.append("start_ms >= ?")
        elif key == "to":
            pieces.append("start_ms < ?")
        else:
            pieces.append(f"{key} = ?")  # key whitelisted by shapes._scope
        args.append(value)
    return "SELECT rowid FROM ops_v WHERE " + " AND ".join(pieces), args


def _scope(conn: sqlite3.Connection, q: Q) -> tuple[list[str], list[Any], list[sqlite3.Row]]:
    """(conditions, args, scope root rows) for the row source of `q`."""
    if "op" in q.scope:
        root = resolve_op(conn, q.scope["op"])
        rid = root["rowid"]
        subtree = (  # DM-15: the subtree of the scope op, itself included
            "SELECT d.op_id FROM ops d WHERE d.root_rowid = ? AND d.pre BETWEEN ? AND ?"
        )
        span = [root["root_rowid"], root["pre"], root["post"]]
        if q.kind == "ops":
            cond, args = {
                "self": ("o.rowid = ?", [rid]),
                "children": ("o.parent_rowid = ?", [rid]),
                "descendants": ("o.root_rowid = ? AND o.pre > ? AND o.pre <= ?", span),
                "sessions": ("o.rowid = ?", [root["root_rowid"]]),
            }[q.select]
            conds = [cond] if q.select == "self" else [cond, "o.duplicate_of IS NULL"]
            return conds, args, [root]
        if root["kind"] == "session":
            return [f"{q.kind[0]}.root_rowid = ?"], [rid], [root]
        if q.kind == "events":
            return [f"e.op_id IN ({subtree})"], span, [root]
        return [f"(s.turn_id IN ({subtree}) OR s.op_id IN ({subtree}))"], span + span, [root]
    sub, args = _sessions_subquery(q.scope)
    if q.kind == "ops" and "all" in q.scope and q.select == "descendants":
        # every op belongs to some session: skip the root_rowid IN (...) probe (full-corpus scans)
        roots = conn.execute(f"SELECT * FROM ops_v WHERE rowid IN ({sub})", args).fetchall()
        return ["o.duplicate_of IS NULL"], [], roots
    roots = conn.execute(f"SELECT * FROM ops_v WHERE rowid IN ({sub})", args).fetchall()
    if q.kind == "ops":
        col = {
            "self": "o.rowid",
            "sessions": "o.rowid",
            "children": "o.parent_rowid",
            "descendants": "o.root_rowid",
        }
        return [f"{col[q.select]} IN ({sub})", "o.duplicate_of IS NULL"], list(args), roots
    return [f"{q.kind[0]}.root_rowid IN ({sub})"], list(args), roots


def _where(q: Q) -> tuple[list[str], list[Any]]:
    conds, args = [], []
    for p in q.where:
        expr = field_sql(q.kind, p.field, q.measure)
        v = int(p.value) if isinstance(p.value, bool) else p.value
        if p.op in ("eq", "ne") and v is None:
            conds.append(f"({expr}) IS {'NOT ' if p.op == 'ne' else ''}NULL")
        elif p.op == "in":
            conds.append(f"({expr}) IN ({', '.join('?' for _ in v)})")
            args.extend(int(x) if isinstance(x, bool) else x for x in v)
        elif p.op == "contains":
            esc = v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(f"({expr}) LIKE ? ESCAPE '\\'")
            args.append(f"%{esc}%")
        else:
            sym = {"eq": "=", "ne": "!=", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}[p.op]
            conds.append(f"({expr}) {sym} ?")
            args.append(v)
    return conds, args


def _totals(
    conn: sqlite3.Connection, q: Q, roots: list[sqlite3.Row], names: list[str]
) -> tuple[dict[str, Any], dict[str, str], bool]:
    lower = any(bool(r["cost_lower_bound"]) for r in roots)
    if q.kind != "ops" or not roots:
        return {}, {}, lower
    totals: dict[str, Any] = {}
    provs: dict[str, str] = {}
    ids = [r["rowid"] for r in roots]
    marks = ", ".join("?" for _ in ids)
    single = "op" in q.scope
    for name in names:
        if name in ("n", "bucket"):
            continue
        m = op_metric(name, "incl")
        if not single and name not in ADDITIVE:
            totals[name], provs[name] = None, "missing"
            continue
        agg = "" if single else "SUM"
        rank = _RANK_SQL.format(p=m.prov)
        sql = (
            f"SELECT {agg}({m.value}), MIN(CASE WHEN ({m.value}) IS NULL THEN 0 ELSE {rank} END) "
            f"FROM ops o WHERE o.rowid IN ({marks})"
        )
        value, rank_v = conn.execute(sql, ids).fetchone()
        totals[name], provs[name] = value, RANK_PROV[rank_v if value is not None else 0]
    return totals, provs, lower


def _share(value: Any, total: Any) -> float | None:
    if value is None or total in (None, 0):
        return None
    return value / total


# ── cursor ──────────────────────────────────────────────────────────────────


def _fingerprint(q: Q) -> str:
    d = asdict(q)
    d.pop("cursor")
    d.pop("limit")
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _offset(q: Q) -> int:
    if q.cursor is None:
        return 0
    try:
        d = json.loads(base64.urlsafe_b64decode(q.cursor.encode()))
        fp, off = d["f"], d["o"]
    except (ValueError, KeyError, TypeError) as exc:
        raise QueryError("malformed cursor") from exc
    if fp != _fingerprint(q):
        raise QueryError("cursor belongs to a different query")
    return int(off)


def _cursor(q: Q, offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"f": _fingerprint(q), "o": offset}).encode()).decode()


# ── query ───────────────────────────────────────────────────────────────────


def run(conn: sqlite3.Connection, q: Q) -> dict[str, Any]:
    conn.create_function("local_bucket", 3, local_bucket, deterministic=True)
    scope_conds, scope_args, roots = _scope(conn, q)
    where_conds, where_args = _where(q)
    conds, args = scope_conds + where_conds, scope_args + where_args
    if q.kind == "events":
        conds.append("e.copied = 0")  # store.load: events of duplicate ops never count (invariant 3)
    total_names = list(q.metrics)
    if q.order_metric not in total_names:
        total_names.append(q.order_metric)
    totals, tprov, lower = _totals(conn, q, roots, total_names)
    if q.aggregated:
        rows, next_cursor = _aggregated(conn, q, conds, args, totals)
    else:
        rows, next_cursor = _plain(conn, q, conds, args, totals)
    return {
        "freshness": freshness(conn),
        "scope_totals": {m: totals.get(m) for m in q.metrics} if q.kind == "ops" else {},
        "scope_prov": {m: tprov.get(m, "missing") for m in q.metrics} if q.kind == "ops" else {},
        "cost_lower_bound": lower,
        "rows": rows,
        "next_cursor": next_cursor,
        "availability": AVAILABILITY,
    }


def _plain(conn, q: Q, conds, args, totals) -> tuple[list[dict[str, Any]], str | None]:
    kind = q.kind
    cols, metric_sql = [], [_metric(q, m) for m in q.metrics]
    for i, m in enumerate(metric_sql):
        cols += [f"{m.value} AS m{i}", f"{m.prov} AS p{i}"]
    order = _metric(q, q.order_metric)
    if q.outliers is not None:
        quant = {"p50": 0.5, "p90": 0.9, "p95": 0.95}[q.outliers["over"]]
        sql = f"SELECT pctl({order.value}, {quant}) FROM {_FROM[kind]} WHERE {' AND '.join(conds)}"
        threshold = conn.execute(sql, args).fetchone()[0]
        conds = [*conds, f"({order.value}) > ?" if threshold is not None else "0"]
        args = [*args, threshold] if threshold is not None else args
    star = {"ops": "o.*", "events": "e.*", "steps": "s.*"}[kind]
    source = _FROM[kind]
    if q.select == "sessions":
        star += (
            ", ss.title AS ss_title, ss.git_branch AS ss_git_branch, ss.harness_version AS ss_harness_version"
        )
        star += ", ss.status AS ss_status"
        source += " LEFT JOIN sessions ss ON ss.op_rowid = o.rowid"
    offset = _offset(q)
    sql = (
        f"SELECT {', '.join([star, *cols, f'{order.value} AS ord'])} FROM {source} "
        f"WHERE {' AND '.join(conds)} "
        f"ORDER BY (ord IS NULL), ord {q.order_dir}, {_TIEBREAK[kind]} LIMIT ? OFFSET ?"
    )
    found = conn.execute(sql, [*args, q.limit + 1, offset]).fetchall()
    share_ok = q.order_metric in ADDITIVE and kind == "ops"
    rows = []
    for r in found[: q.limit]:
        metrics = {m: r[f"m{i}"] for i, m in enumerate(q.metrics)}
        prov = {m: (r[f"p{i}"] if metrics[m] is not None else "missing") for i, m in enumerate(q.metrics)}
        row: dict[str, Any] = {"metrics": metrics, "prov": prov}
        if kind == "ops":
            row["op"] = op_wire(r, session_fields=q.select == "sessions")
            row["share"] = _share(r["ord"], totals.get(q.order_metric)) if share_ok else None
            if q.outliers is not None and q.outliers["with_top_child"]:
                row["top_child"] = _top_child(conn, r["rowid"], q.order_metric)
        elif kind == "events":
            row["event"] = event_wire(r)
        else:
            row["step"] = step_wire(r)
        rows.append(row)
    nxt = _cursor(q, offset + q.limit) if len(found) > q.limit else None
    return rows, nxt


def _top_child(conn, rowid: int, metric: str) -> dict[str, Any] | None:
    m = op_metric(metric if metric in ADDITIVE else "cost_usd", "incl")
    r = conn.execute(
        f"SELECT o.* FROM ops_v o WHERE o.parent_rowid = ? AND o.duplicate_of IS NULL "
        f"ORDER BY ({m.value}) IS NULL, ({m.value}) DESC, o.op_id LIMIT 1",
        (rowid,),
    ).fetchone()
    return op_wire(r) if r is not None else None


_AGG_SQL = {"sum": "SUM({v})", "count": "COUNT({v})", "max": "MAX({v})"}
_QUANT = {"p50": 0.5, "p90": 0.9, "p95": 0.95}


def _agg_expr(agg: str, value: str) -> str:
    if agg in _AGG_SQL:
        return _AGG_SQL[agg].format(v=value)
    return f"pctl({value}, {_QUANT['p50' if agg == 'distribution' else agg]})"


def _ops_from(q: Q) -> str:
    """FROM clause for op scans: the v7 view. v6 pinned `ops_commands`, `ops_command_key` and
    `ops_kind_start` with INDEXED BY; SQLite cannot name an index through a view, so the planner
    chooses (not measured on the full corpus; M2 rewrites these scans on the v7 columns)."""
    return _FROM[q.kind]


# rollup_time (store.load) holds owned leaves summed per (root, bucket, provider, account,
# model, project, agent_type); llm-only sums read from it equal the op scan, provenance
# included (test-enforced): cost from n_unpriced (omp reported, Claude/Codex computed, §4.7),
# tokens from n_tok_missing, llm time from n_time_* (schema v6).
_ROLLUP_METRICS = {
    "cost_usd": "cost_usd",
    "tokens.total": "tok_total",
    "tokens.in": "in_tok",
    "tokens.out": "out_tok",
    "tokens.cache_read": "cache_read_tok",
    "tokens.cache_write": "cache_write_tok",
    "tokens.reasoning": "reasoning_tok",
    "llm_ms": "llm_ms",
}
_ROLLUP_GROUPS = {
    "model": "r.model",
    "provider": "r.provider",
    "account": "r.account",
    "project": "r.project",
}
_TOK_PROV = "MIN(CASE WHEN r.n_tok_missing > 0 THEN 0 ELSE 3 END)"
_ROLLUP_PROV = {
    "cost_usd": "MIN(CASE WHEN r.n_unpriced > 0 THEN 0 WHEN r.provider = 'omp' THEN 3 ELSE 2 END)",
    "llm_ms": "MIN(CASE WHEN r.n_time_missing > 0 THEN 0 WHEN r.n_time_estimated > 0 THEN 1"
    " WHEN r.n_time_derived > 0 THEN 2 ELSE 3 END)",
    **{m: _TOK_PROV for m in _ROLLUP_METRICS if m.startswith("tokens.")},
}


def _rollup_sql(q: Q, names: list[str]) -> tuple[str, list[Any]] | None:
    """The same grouped sum over llm_calls, read from rollup_time; None when not equivalent."""
    w = q.where[0] if len(q.where) == 1 else None
    llm_only = (
        w is not None and w.field == "kind" and w.value in ("llm_call", ["llm_call"]) and w.op in ("eq", "in")
    )
    if not (
        q.kind == "ops"
        and q.select == "descendants"
        and q.measure == "own"
        and "op" not in q.scope
        and q.agg == "sum"
        and q.outliers is None
        and llm_only
        and set(q.group_by) <= set(_ROLLUP_GROUPS)
        and set(names) <= set(_ROLLUP_METRICS)
    ):
        return None
    cols = [f"{_ROLLUP_GROUPS[g]} AS g{i}" for i, g in enumerate(q.group_by)]
    cols.append(f"{_bucket_sql('r.bucket_start_ms', q.bucket, q.tz)} AS b" if q.bucket else "NULL AS b")
    for i, name in enumerate(names):
        cols.append(f"SUM(r.{_ROLLUP_METRICS[name]}) AS m{i}")
        cols.append(f"{_ROLLUP_PROV[name]} AS p{i}")
    cols += ["SUM(r.n_llm_calls) AS n", "COUNT(DISTINCT r.root_rowid) AS ns", "NULL AS t0", "NULL AS t1"]
    sql = f"SELECT {', '.join(cols)} FROM rollup_time r WHERE r.n_llm_calls > 0"
    args: list[Any] = []  # every owned leaf is in exactly one 15-minute bucket
    if "sessions" in q.scope:
        sub, sargs = _sessions_subquery(q.scope)
        sql += f" AND r.root_rowid IN ({sub})"
        args += sargs
    group = [f"g{i}" for i in range(len(q.group_by))] + (["b"] if q.bucket else [])
    if group:
        sql += " GROUP BY " + ", ".join(group)
    return sql, args


def _aggregated(conn, q: Q, conds, args, totals) -> tuple[list[dict[str, Any]], str | None]:
    kind = q.kind
    agg = q.agg or "sum"  # FALLBACK-OK: design §5 grouped/bucketed rows sum by default
    gexprs = [GROUPS[kind][g] for g in q.group_by]
    cols = [f"{e} AS g{i}" for i, e in enumerate(gexprs)]
    bexpr = _bucket_sql(_TIME[kind], q.bucket, q.tz) if q.bucket else "NULL"
    cols.append(f"{bexpr} AS b")
    names = list(q.metrics)
    if q.order_metric not in (*names, "n", "bucket"):
        names.append(q.order_metric)
    for i, name in enumerate(names):
        m = _metric(q, name)
        cols.append(f"{_agg_expr(agg, m.value)} AS m{i}")
        rank = _RANK_SQL.format(p=m.prov)
        cols.append(f"MIN(CASE WHEN ({m.value}) IS NULL THEN 0 ELSE {rank} END) AS p{i}")
        if agg == "distribution":
            cols += [
                f"pctl({m.value}, 0.9) AS d{i}_p90",
                f"MAX({m.value}) AS d{i}_max",
                f"COUNT({m.value}) AS d{i}_n",
            ]
    cols += [
        "COUNT(*) AS n",
        f"COUNT(DISTINCT {_SESSION[kind]}) AS ns",
        f"MIN({_TIME[kind]}) AS t0",
        f"MAX({_LAST[kind]}) AS t1",
    ]
    group = [f"g{i}" for i in range(len(gexprs))] + (["b"] if q.bucket else [])
    fast = _rollup_sql(q, names)
    if fast is not None:
        sql, args = fast
    else:
        source = _ops_from(q)
        sql = f"SELECT {', '.join(cols)} FROM {source} WHERE {' AND '.join(conds)}"
        if group:
            sql += " GROUP BY " + ", ".join(group)
    found = conn.execute(sql, args).fetchall()
    if not group and found and found[0]["n"] == 0:
        found = []

    rows: list[dict[str, Any]] = []
    for r in found:
        vals = {name: r[f"m{i}"] for i, name in enumerate(names)}
        row: dict[str, Any] = {
            "key": {g: r[f"g{i}"] for i, g in enumerate(q.group_by)},
            "metrics": {m: vals[m] for m in q.metrics},
            "prov": {m: RANK_PROV[r[f"p{names.index(m)}"] if vals[m] is not None else 0] for m in q.metrics},
            "counts": {
                k: v
                for k, v in (
                    ("n", r["n"]),
                    ("sessions", r["ns"]),
                    ("first_ms", r["t0"]),
                    ("last_ms", r["t1"]),
                )
                if v is not None
            },
        }
        if q.bucket:
            row["bucket"] = r["b"]
        if agg == "distribution":
            row["dist"] = {
                m: {"p50": vals[m], "p90": r[f"d{i}_p90"], "max": r[f"d{i}_max"], "n": r[f"d{i}_n"]}
                for i, m in enumerate(names)
                if m in q.metrics
            }
        row["_ord"] = (
            r["n"]
            if q.order_metric == "n"
            else r["b"]
            if q.order_metric == "bucket"
            else vals[q.order_metric]
        )
        rows.append(row)

    if q.compare:
        index = {(json.dumps(r["key"], sort_keys=True), r["bucket"]): r for r in rows}
        for r in rows:
            prev = index.get(
                (json.dumps(r["key"], sort_keys=True), local_bucket(q.bucket, q.tz, r["bucket"] - 1))
            )
            r["delta"] = {
                m: (r["metrics"][m] - prev["metrics"][m])
                if prev is not None and r["metrics"][m] is not None and prev["metrics"][m] is not None
                else None
                for m in q.metrics
            }

    n_total = sum(r["counts"]["n"] for r in rows)
    for r in rows:
        if q.order_metric == "n":
            r["share"] = _share(r["counts"]["n"], n_total)
        elif q.order_metric in ADDITIVE and agg == "sum" and kind == "ops":
            r["share"] = _share(r["_ord"], totals.get(q.order_metric))
        else:
            r["share"] = None

    def sort_key(r: dict[str, Any]) -> tuple:
        v = r["_ord"]
        tie = (json.dumps(r["key"], sort_keys=True, default=str), r.get("bucket") or 0)
        return (v is None, (-v if q.order_dir == "desc" else v) if v is not None else 0, tie)

    rows.sort(key=sort_key)
    for r in rows:
        del r["_ord"]
    offset = _offset(q)
    page = rows[offset : offset + q.limit]
    nxt = _cursor(q, offset + q.limit) if len(rows) > offset + q.limit else None
    return page, nxt


# ── secondary reads (§5.2) ──────────────────────────────────────────────────


def show(conn, ident: str) -> dict[str, Any]:
    row = resolve_op(conn, ident)
    ancestors = conn.execute(  # DM-15: strictly enclosing intervals, outermost first
        "SELECT o.* FROM ops_v o WHERE o.root_rowid = ? AND o.pre < ? AND o.post >= ? ORDER BY o.depth",
        (row["root_rowid"], row["pre"], row["post"]),
    ).fetchall()
    counts = conn.execute(
        "SELECT kind, COUNT(*) FROM ops WHERE parent_rowid = ? GROUP BY kind", (row["rowid"],)
    )
    events = conn.execute("SELECT * FROM events_v WHERE op_id = ? ORDER BY ts_ms, event_id", (row["op_id"],))
    return {
        "op": op_wire(row),
        "ancestors": [op_wire(a) for a in ancestors],
        "children_counts": {k: n for k, n in counts},
        "events": [event_wire(e) for e in events],
        "freshness": freshness(conn),
    }


def timeline(conn, ident: str, depth: int) -> dict[str, Any]:
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 32:
        raise QueryError("depth must be 0..32")
    root = resolve_op(conn, ident)
    nest_sql = (  # engagements enclosing (or equal to) the op: DM-15 intervals of the same root
        "(SELECT COUNT(*) FROM ops e WHERE e.root_rowid = {r} AND e.pre <= {pre} AND e.post >= {post}"
        " AND e.kind = 'engagement')"
    )
    base = conn.execute(
        "SELECT " + nest_sql.format(r="?", pre="?", post="?"), (root["root_rowid"], root["pre"], root["post"])
    ).fetchone()[0]
    ops = conn.execute(
        "SELECT o.* FROM ops_v o "
        "WHERE o.root_rowid = ? AND o.pre > ? AND o.pre <= ? AND o.duplicate_of IS NULL "
        "AND NOT (o.end_ms < o.start_ms) "
        f"AND {nest_sql.format(r='o.root_rowid', pre='o.pre', post='o.post')} - ? <= ? "
        "ORDER BY o.start_ms, COALESCE(o.seq, o.pre), o.op_id",
        (root["root_rowid"], root["pre"], root["post"], base, depth),
    ).fetchall()
    if root["kind"] == "session":
        ev = conn.execute(
            "SELECT * FROM events_v WHERE root_rowid = ? AND copied = 0 ORDER BY ts_ms, event_id",
            (root["rowid"],),
        )
    else:
        ev = conn.execute(
            "SELECT e.* FROM events_v e WHERE e.op_id IN (SELECT d.op_id FROM ops d "
            "WHERE d.root_rowid = ? AND d.pre BETWEEN ? AND ?) AND e.copied = 0 "
            "ORDER BY e.ts_ms, e.event_id",
            (root["root_rowid"], root["pre"], root["post"]),
        )
    return {
        "root": op_wire(root),
        "ops": [op_wire(o) for o in ops],
        "events": [event_wire(e) for e in ev],
        "freshness": freshness(conn),
    }


def steps(conn, ident: str, limit: int, cursor: str | None) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
        raise QueryError("limit must be 1..5000")
    turn = resolve_op(conn, ident)
    if turn["kind"] != "turn":
        raise QueryError(f"{ident!r} is a {turn['kind']}, not a turn")
    try:
        after = int(cursor) if cursor is not None else -1
    except ValueError as exc:
        raise QueryError("malformed cursor") from exc
    found = conn.execute(
        "SELECT * FROM steps_v WHERE turn_id = ? AND ordinal > ? ORDER BY ordinal LIMIT ?",
        (turn["op_id"], after, limit + 1),
    ).fetchall()
    page = found[:limit]
    return {
        "turn": op_wire(turn),
        "steps": [step_wire(s) for s in page],
        "next_cursor": str(page[-1]["ordinal"]) if len(found) > limit else None,
        "freshness": freshness(conn),
    }


# part -> candidate json paths, tried in order below the pointer's own path
# (T0 hand-off note 7). `output` is also tried on the last line of a multi-line
# locator, where tool results land.
PARTS: dict[str, tuple[str, ...]] = {
    "input": ("input", "arguments", "payload.arguments", "payload.input", "message.content.0.input"),
    "thinking": ("thinking", "message.content.0.thinking", "payload.summary"),
    "text": ("text", "message.content.0.text", "message.content", "payload.message", "payload.content"),
    "output": ("toolUseResult", "message.content.0.content", "payload.output", "output", "content", "result"),
}


def source(conn, ident: str, part: str | None, offset: int, max_bytes: int, roots) -> dict[str, Any]:
    if part is not None and part not in PARTS:
        raise QueryError(f"part must be one of {sorted(PARTS)}")
    if not (
        isinstance(offset, int) and offset >= 0 and isinstance(max_bytes, int) and 1 <= max_bytes <= 1 << 24
    ):
        raise QueryError("offset must be >= 0 and max_bytes 1..16 MiB")
    src = ident if ident.startswith(pointer.SCHEME) else resolve_src(conn, ident)
    try:
        if part is None:
            out = pointer.resolve(src, roots, None, offset, max_bytes)
        else:
            out = _resolve_part(src, part, roots, offset, max_bytes)
    except pointer.PointerError as exc:
        raise QueryError(str(exc)) from exc
    out["freshness"] = freshness(conn)
    return out


def _resolve_part(src: str, part: str, roots, offset: int, max_bytes: int) -> dict[str, Any]:
    p = pointer.parse(src)
    candidates = [src]
    if part == "output" and isinstance(p.locator, pointer.Lines) and p.locator.last > p.locator.first:
        last = pointer.Lines(p.locator.last, p.locator.last)
        candidates.append(pointer.format(p.provider, p.relpath, last))
    for uri in candidates:
        for path in PARTS[part]:
            try:
                return pointer.resolve(uri, roots, path, offset, max_bytes)
            except pointer.PointerError:
                continue  # FALLBACK-OK: T0 note 7 — part paths are candidates; none matching raises below
    raise QueryError(f"part {part!r} not found under {src}")


# ── listings for HTTP §6.3 (sessions, projects) ─────────────────────────────

_SESSION_ORDER = {"start_ms": "o.start_ms", "cost_usd": "o.incl_cost_usd", "tokens.total": "o.incl_tok_total"}


def sessions(conn, filters: dict[str, Any], order: str, limit: int, offset: int = 0) -> dict[str, Any]:
    """API-27: `total` counts the matching sessions before `offset`/`limit`; rows carry QRY-13 names."""
    from botsworth.query.views import common  # lazy: the views package imports this package

    if order.lstrip("-") not in _SESSION_ORDER:
        raise QueryError(f"order must be one of {sorted(_SESSION_ORDER)} (prefix - for desc)")
    if not isinstance(limit, int) or not 1 <= limit <= 5000:
        raise QueryError("limit must be 1..5000")
    if not isinstance(offset, int) or offset < 0:
        raise QueryError("offset must be >= 0")
    sub, args = _sessions_subquery({"sessions": filters})
    direction = "DESC" if order.startswith("-") else "ASC"
    total = conn.execute(f"SELECT COUNT(*) FROM ops_v WHERE rowid IN ({sub})", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT o.*, s.title AS ss_title, s.git_branch AS ss_git_branch, "
        "s.harness_version AS ss_harness_version, s.status AS ss_status, "
        "s.display_title AS ss_display_title, s.title_source AS ss_title_source, "
        "s.is_automated AS ss_is_automated, p.name AS ss_project_name FROM ops_v o "
        "JOIN sessions s ON s.op_rowid = o.rowid LEFT JOIN projects p ON p.project = s.project "
        f"WHERE o.rowid IN ({sub}) "
        f"ORDER BY {_SESSION_ORDER[order.lstrip('-')]} {direction}, o.op_id LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    out = []
    for r in rows:
        wire_row = op_wire(r, session_fields=True)
        name, title_source = common.session_name(
            {
                "display_title": r["ss_display_title"],
                "title_source": r["ss_title_source"],
                "project_name": r["ss_project_name"],
                "session_id": r["session_id"],
            }
        )
        out.append(
            wire_row
            | {
                "name": name,
                "title_source": title_source,
                "project_name": r["ss_project_name"],
                "is_automated": bool(r["ss_is_automated"]),
            }
        )
    return {"freshness": freshness(conn), "total": total, "sessions": out}


def projects(conn, tz: str) -> dict[str, Any]:
    """API-28: one row per project. `n_sessions` and `n_automated` come from `projects` (DM-60, DM-61: only
    sessions with an owned llm_call count, so a project can list with 0). API-7: the body has freshness."""
    from botsworth.query.views import common  # lazy: the views package imports this package

    slots = common.color_slots(conn, tz)
    rows = conn.execute(
        "SELECT s.project AS project, COUNT(*) AS n, GROUP_CONCAT(DISTINCT s.provider) AS providers, "
        "MIN(o.start_ms) AS f, MAX(COALESCE(o.end_ms, o.start_ms)) AS l, SUM(o.incl_cost_usd) AS c, "
        "SUM(o.incl_tok_total) AS t, p.name AS project_name, p.n_sessions AS n_counted, "
        "p.n_automated AS n_automated FROM ops_v o JOIN sessions s ON s.op_rowid = o.rowid "
        "LEFT JOIN projects p ON p.project = s.project WHERE o.kind = 'session' "
        "GROUP BY s.project ORDER BY l DESC"
    ).fetchall()
    for r in rows:
        assert r["project_name"] is not None, f"project {r['project']!r} has no projects row (DM-60)"
    return {
        "freshness": freshness(conn),
        "projects": [
            {
                "project": r["project"],
                "project_name": r["project_name"],
                "color_slot": slots.get(r["project"]),
                "n_sessions": r["n_counted"],
                "n_automated": r["n_automated"],
                "providers": sorted(r["providers"].split(",")),
                "first_ms": r["f"],
                "last_ms": r["l"],
                "incl_cost_usd": r["c"],
                "tok_total": r["t"],
            }
            for r in rows
        ],
    }
