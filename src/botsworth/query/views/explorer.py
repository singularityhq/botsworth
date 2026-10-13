"""CONTRACT: explorer views (owner Q1): period, day chart, session list, facets, palette.

Spec ids: QRY-3–QRY-16, QRY-23–QRY-28; API-10–API-14.

GUARANTEES:
- Zone (D50): every local day, week and hour is an epoch range in `Scope.tz` (`query/zone.py`) over the
  15-minute `rollup_time` buckets. Nothing reads `meta.tz`, `bucket_day` or `bucket_kind`.
- Membership (QRY-5): a session is in scope when it has a `rollup_time` bucket with `n_llm_calls > 0` inside
  the period's epoch range (any such bucket for all time) and passes the filters (QRY-7) and the automated
  rule. All figures of a session row, group, project or total are the session's full `incl_*` figures
  (QRY-9); only day bars, hour/day strips and `day_split` sum buckets (QRY-10).
- Money (QRY-11): sums skip null cost. An in-scope session has an owned llm_call, so its cost is never
  null: a session whose calls are all unpriced reads 0.0 with `cost_lower_bound`. A total over zero
  sessions is null. `n_unpriced` is the sum of `rollup_time.n_unpriced` over the session's day rows.
- `period_summary.totals.cost_usd` = sum of `projects[].cost_usd` (no project filter) = sum over the
  `list_sessions` rows of all pages. `first_day`/`last_day` span every bucket with an owned llm_call in
  the store (filters ignored), as local days in `Scope.tz`.
- `day_series` returns one bar per day (or per local Monday) of the window, zeros included. A bar sums the
  buckets inside its epoch range; a week clipped at the window edge covers only its in-window days and its
  `from_day`/`to_day` name the dates covered (D41 M10). The bar's `day` is the Monday.
- `list_sessions`: sort key nulls last; ties by `first_ms` descending, then session op_id. Offset paging
  is disjoint and complete. Grouped mode returns every group with its first `group_limit` rows and
  `rows = []`; with `group_key` it returns that group's rows paged, `total` = the group's size,
  `groups = None`. `stats.max_cost_usd` and `stats.max_activity_tok` cover all in-scope rows (D45, QRY-26).
- `facets` counts with the scope minus the facet's own filter (QRY-8); zero-count values are listed.
- `palette` with blank `text` returns three empty lists. `projects` rank by all-time cost descending, then
  key. `in_filters` includes the scope's `q`.
- `in_scope_roots(conn, scope)` is the one scope evaluation other tasks reuse (Q3b).
EXPECTS: a v8 store (`projects`, 15-minute `rollup_time`, `sessions` derived columns).
FAILURE BEHAVIOR: `QueryError` for a bad window, sort, direction, group, offset, limit; store state errors
  from `botsworth.query._conn`. A session without a `projects` row asserts (DM-60).
DOES NOT: compute names or colour slots itself (views.common), read source files, or write.
"""

from __future__ import annotations

import json
import math
import sqlite3
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from botsworth.query import Db, QueryError, _conn, shapes, wire, zone
from botsworth.query.scope import Scope, check_day
from botsworth.query.views import common

MAX_WINDOW_DAYS = 400
MAX_LIMIT = 1000
MAX_GROUP_LIMIT = 50
HOUR_STRIP_MAX_DAYS = 14
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
SORTS = {
    "start": "s.first_ms",
    "cost": "COALESCE(s.incl_cost_usd, 0)",
    "wall": "s.wall_ms",
    "active": "s.active_ms",
    "tokens": "s.incl_tok_total",
}
FILTER_KINDS = {"model": "models", "branch": "branches", "provider": "providers", "project": "projects"}

_FROM = "FROM sessions s JOIN ops o ON o.rowid = s.op_rowid LEFT JOIN projects p ON p.project = s.project"
_NAME = "COALESCE(s.display_title, p.name || ' · ' || substr(s.session_id, 1, 8))"
# the display names of QRY-7; an unknown provider is searched by its key only
_PROVIDER_LABEL = (
    "CASE s.provider WHEN 'claude_code' THEN 'Claude Code' WHEN 'codex' THEN 'Codex' "
    "WHEN 'omp' THEN 'omp' ELSE s.provider END"
)
_LABELS = {"claude_code": "Claude Code", "codex": "Codex", "omp": "omp"}
_LLM_BUCKETS = "SELECT root_rowid FROM rollup_time WHERE n_llm_calls > 0"
_ROW_SELECT = """
SELECT s.op_rowid, o.op_id, s.session_id, s.provider, s.project, p.name AS project_name,
  s.display_title, s.title_source, s.first_ms, s.last_ms, s.incl_cost_usd, s.incl_tok_total,
  s.cost_lower_bound, s.wall_ms, s.active_ms, s.is_automated, s.git_branch,
  s.activity_bin_ms, s.activity_tok,
  (SELECT COALESCE(SUM(u.n_unpriced), 0) FROM rollup_time u
    WHERE u.root_rowid = s.op_rowid) AS n_unpriced
"""


# ── scope evaluation (QRY-5, QRY-7, QRY-8) ───────────────────────────────────


def _in(column: str, values: Sequence[str]) -> tuple[str, list[str]]:
    return f"{column} IN ({', '.join('?' * len(values))})", list(values)


def _term_sql(term: str) -> tuple[str, list[str]]:
    """One `q` term (QRY-7): a substring of any search field; `%` and `_` are literal."""
    fields = (
        _NAME,
        "p.name",
        "s.git_branch",
        "s.session_id",
        "s.provider",
        _PROVIDER_LABEL,
    )
    hits = [f"instr(lower({f}), lower(?)) > 0" for f in fields]
    hits.append(
        "EXISTS (SELECT 1 FROM ops t WHERE t.root_rowid = s.op_rowid AND t.kind = 'turn' "
        "AND t.turn_kind = 'human' AND instr(lower(t.label), lower(?)) > 0)"
    )
    return "(" + " OR ".join(hits) + ")", [term] * len(hits)


def _where(
    scope: Scope,
    *,
    period: bool = True,
    ignore: Sequence[str] = (),
    automated: bool = True,
    filters: bool = True,
) -> tuple[str, list[Any]]:
    """The WHERE of QRY-5/QRY-7 over `sessions s`, `projects p`. `ignore` names a filter kind to leave out
    (QRY-8). `filters=False` keeps only the period and the automated rule."""
    parts: list[str] = []
    args: list[Any] = []
    if period:
        if scope.from_day is None:
            parts.append(f"s.op_rowid IN ({_LLM_BUCKETS})")
        else:
            parts.append(f"s.op_rowid IN ({_LLM_BUCKETS} AND bucket_start_ms >= ? AND bucket_start_ms < ?)")
            args += _period_range(scope)
    if automated and not scope.show_automated:
        parts.append("s.is_automated = 0")
    if filters:
        if scope.providers and "providers" not in ignore:
            sql, a = _in("s.provider", scope.providers)
            parts.append(sql)
            args += a
        if scope.models and "models" not in ignore:
            sql, a = _in("m.model", scope.models)
            parts.append(f"EXISTS (SELECT 1 FROM rollup_time m WHERE m.root_rowid = s.op_rowid AND {sql})")
            args += a
        if scope.branches and "branches" not in ignore:
            sql, a = _in("s.git_branch", scope.branches)
            parts.append(sql)
            args += a
        if scope.projects and "projects" not in ignore:
            sql, a = _in("s.project", scope.projects)
            parts.append(sql)
            args += a
        for term in scope.q.split():
            sql, a = _term_sql(term)
            parts.append(sql)
            args += a
    return (" AND ".join(parts) or "1"), args


def _rows(conn: sqlite3.Connection, scope: Scope, order: str = "s.op_rowid", **kw: Any) -> list[sqlite3.Row]:
    where, args = _where(scope, **kw)
    return conn.execute(f"{_ROW_SELECT} {_FROM} WHERE {where} ORDER BY {order}", args).fetchall()


def _count(conn: sqlite3.Connection, scope: Scope, **kw: Any) -> int:
    where, args = _where(scope, **kw)
    return conn.execute(f"SELECT COUNT(*) {_FROM} WHERE {where}", args).fetchone()[0]


def in_scope_roots(conn: sqlite3.Connection, scope: Scope) -> list[int]:
    """The session root rowids of the in-scope sessions (QRY-5, QRY-7), ascending."""
    where, args = _where(scope)
    return [
        r[0]
        for r in conn.execute(f"SELECT s.op_rowid {_FROM} WHERE {where} ORDER BY s.op_rowid", args).fetchall()
    ]


# ── figures ──────────────────────────────────────────────────────────────────


def _fresh(conn: sqlite3.Connection) -> wire.Freshness:
    return wire.Freshness(**shapes.freshness(conn))


def _cost(row: sqlite3.Row) -> float:
    cost = row["incl_cost_usd"]
    # FALLBACK-OK: interfaces.md QRY-11 — an in-scope session has an owned llm_call, so all-unpriced reads 0
    return 0.0 if cost is None else cost


def _figures(rows: Sequence[sqlite3.Row]) -> tuple[wire.Money, int | None]:
    """Full-figure money and tokens of `rows` (QRY-9, QRY-11); null when there are no sessions."""
    if not rows:
        return common.money(None, False, 0), None
    toks = [r["incl_tok_total"] for r in rows if r["incl_tok_total"] is not None]
    money = common.money(
        sum(_cost(r) for r in rows),
        any(r["cost_lower_bound"] for r in rows),
        sum(r["n_unpriced"] for r in rows),
    )
    return money, (sum(toks) if toks else None)


def _period_range(scope: Scope) -> list[int]:
    """The epoch range `[local midnight of from_day, local midnight after to_day)` in `scope.tz` (QRY-5)."""
    assert scope.from_day is not None and scope.to_day is not None
    return [zone.day_bounds(scope.from_day, scope.tz)[0], zone.day_bounds(scope.to_day, scope.tz)[1]]


def _llm_extent(conn: sqlite3.Connection, tz: str) -> tuple[str | None, str | None]:
    """First and last local day (in `tz`) holding a bucket with an owned llm_call; None when there is none."""
    lo, hi = conn.execute(
        "SELECT MIN(bucket_start_ms), MAX(bucket_start_ms) FROM rollup_time WHERE n_llm_calls > 0"
    ).fetchone()
    if lo is None:
        return None, None
    return zone.local_day(lo, tz), zone.local_day(hi, tz)


def _add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _json_ids(ids: Sequence[int]) -> str:
    return json.dumps(list(ids))


# ── period_summary (QRY-23, QRY-24; API-10) ──────────────────────────────────


def period_summary(scope: Scope, db: Db = None) -> wire.PeriodSummary:
    """QRY-23, QRY-24; API-10."""
    with _conn(db) as conn:
        rows = _rows(conn, scope)
        money, tok = _figures(rows)
        slots = common.color_slots(conn, scope.tz)
        panel: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for r in _rows(conn, scope, ignore=("projects",)):
            panel[r["project"]].append(r)
        projects = []
        for key, group in panel.items():
            assert group[0]["project_name"] is not None, f"project {key!r} has no projects row (DM-60)"
            gm, gt = _figures(group)
            projects.append(
                wire.PeriodProject(
                    key, group[0]["project_name"], slots.get(key), *_money_args(gm), gt, len(group)
                )
            )
        projects.sort(key=lambda p: (-(p.cost_usd or 0.0), p.project))
        hidden = 0 if scope.show_automated else _automated_count(conn, scope)
        totals = wire.PeriodTotals(
            *_money_args(money),
            tok,
            len(rows),
            _count(conn, scope, filters=False),
            hidden,
        )
        first, last = _llm_extent(conn, scope.tz)
        return wire.PeriodSummary(
            _fresh(conn), first, last, totals, projects, _empty(conn, scope) if not rows else None
        )


def _money_args(m: wire.Money) -> tuple[float | None, bool, int]:
    return m.cost_usd, m.cost_lower_bound, m.n_unpriced


def _empty(conn: sqlite3.Connection, scope: Scope) -> wire.PeriodEmpty:
    """QRY-24: what the empty state offers (EXP-79, EXP-80)."""
    matches = None
    if scope.q.split():
        matches = _count(conn, scope, period=False)
    where, args = _where(scope, period=False)
    prev_day = next_day = None
    if scope.from_day is not None:
        lo, hi = _period_range(scope)
        base = (
            "SELECT {agg}(r.bucket_start_ms) FROM rollup_time r "
            "JOIN sessions s ON s.op_rowid = r.root_rowid JOIN ops o ON o.rowid = s.op_rowid "
            f"LEFT JOIN projects p ON p.project = s.project WHERE r.n_llm_calls > 0 AND {where} AND "
        )
        before = conn.execute(base.format(agg="MAX") + "r.bucket_start_ms < ?", [*args, lo]).fetchone()[0]
        after = conn.execute(base.format(agg="MIN") + "r.bucket_start_ms >= ?", [*args, hi]).fetchone()[0]
        prev_day = (
            _day_count(conn, where, args, zone.local_day(before, scope.tz), scope.tz) if before else None
        )
        next_day = _day_count(conn, where, args, zone.local_day(after, scope.tz), scope.tz) if after else None
    return wire.PeriodEmpty(matches, prev_day, next_day)


def _day_count(conn: sqlite3.Connection, where: str, args: list[Any], day: str, tz: str) -> wire.DayCount:
    """A local day and the number of in-filter sessions with an llm_call bucket inside it (QRY-24)."""
    lo, hi = zone.day_bounds(day, tz)
    n = conn.execute(
        "SELECT COUNT(DISTINCT r.root_rowid) FROM rollup_time r "
        "JOIN sessions s ON s.op_rowid = r.root_rowid JOIN ops o ON o.rowid = s.op_rowid "
        f"LEFT JOIN projects p ON p.project = s.project WHERE r.n_llm_calls > 0 AND {where} "
        "AND r.bucket_start_ms >= ? AND r.bucket_start_ms < ?",
        [*args, lo, hi],
    ).fetchone()[0]
    return wire.DayCount(day, n)


# ── day_series (QRY-25; API-11) ──────────────────────────────────────────────


def day_series(
    scope: Scope, window_from: str, window_to: str, bucket: str = "day", db: Db = None
) -> wire.DaySeries:
    """QRY-25; API-11. `bucket` is `day` or `week`; the scope's period is ignored."""
    check_day("window_from", window_from)
    check_day("window_to", window_to)
    if window_from > window_to:
        raise QueryError(f"window_from {window_from} is after window_to {window_to}")
    n_days = (date.fromisoformat(window_to) - date.fromisoformat(window_from)).days + 1
    if n_days > MAX_WINDOW_DAYS:
        raise QueryError(f"window is {n_days} days; the limit is {MAX_WINDOW_DAYS}")
    if bucket not in ("day", "week"):
        raise QueryError(f"bucket must be day or week, got {bucket!r}")
    with _conn(db) as conn:
        spans = _day_spans(window_from, window_to) if bucket == "day" else _week_spans(window_from, window_to)
        slots = common.color_slots(conn, scope.tz)
        names = dict(conn.execute("SELECT project, name FROM projects").fetchall())
        rows = _span_rows(conn, scope, spans)
        bars = [
            _bar(key, from_day, to_day, rows.get(i, []), slots, names)
            for i, (key, from_day, to_day) in enumerate(spans)
        ]
        return wire.DaySeries(_fresh(conn), bars)


def _day_spans(first: str, last: str) -> list[tuple[str, str, str]]:
    n = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    return [(d, d, d) for d in (_add_days(first, i) for i in range(n))]


def _week_spans(first: str, last: str) -> list[tuple[str, str, str]]:
    """(Monday, first day covered, last day covered) per week; a week clipped by the window covers less."""
    monday = date.fromisoformat(first) - timedelta(days=date.fromisoformat(first).weekday())
    spans = []
    while monday.isoformat() <= last:
        sunday = monday + timedelta(days=6)
        spans.append((monday.isoformat(), max(monday.isoformat(), first), min(sunday.isoformat(), last)))
        monday += timedelta(days=7)
    return spans


def _span_rows(
    conn: sqlite3.Connection, scope: Scope, spans: Sequence[tuple[str, str, str]]
) -> dict[int, list[sqlite3.Row]]:
    """Per (session, span) sums of the llm buckets inside each span's epoch range in `scope.tz`, for in-filter
    sessions, keyed by span index. The spans are contiguous days, so one edge list holds them all."""
    where, args = _where(scope, period=False)
    bounds = [(zone.day_bounds(f, scope.tz)[0], zone.day_bounds(t, scope.tz)[1]) for _, f, t in spans]
    assert all(hi == nxt for (_, hi), (nxt, _) in zip(bounds, bounds[1:], strict=False)), (
        "spans are not contiguous"
    )
    edges = [bounds[0][0], *(hi for _, hi in bounds)]
    ranges, edge_arg = common.edge_ranges(edges)
    found = conn.execute(
        "SELECT d.i, d.root_rowid, s.project, d.cost, d.tok, d.unpriced, d.calls "
        "FROM (SELECT e.i AS i, r.root_rowid AS root_rowid, SUM(r.cost_usd) AS cost, "
        "SUM(r.tok_total) AS tok, SUM(r.n_unpriced) AS unpriced, SUM(r.n_llm_calls) AS calls "
        f"FROM {ranges} e JOIN rollup_time r ON r.bucket_start_ms >= e.lo AND r.bucket_start_ms < e.hi "
        "WHERE r.n_llm_calls > 0 GROUP BY e.i, r.root_rowid) d "
        "JOIN sessions s ON s.op_rowid = d.root_rowid JOIN ops o ON o.rowid = s.op_rowid "
        f"LEFT JOIN projects p ON p.project = s.project WHERE {where}",
        [edge_arg, *args],
    ).fetchall()
    out: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in found:
        out[r["i"]].append(r)
    return out


def _bar(
    day: str,
    from_day: str,
    to_day: str,
    rows: Sequence[sqlite3.Row],
    slots: dict[str, int],
    names: dict[str, str],
) -> wire.DayBar:
    # FALLBACK-OK: interfaces.md API-11 — a day cost is the sum of priced calls, 0 when none
    cost = sum(r["cost"] or 0.0 for r in rows)
    unpriced = sum(r["unpriced"] or 0 for r in rows)
    tok = sum(r["tok"] or 0 for r in rows)  # FALLBACK-OK: interfaces.md QRY-11 — sums skip null tokens
    per_project: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for r in rows:
        if r["calls"] > 0:
            acc = per_project[r["project"]]
            acc[0] += r["cost"] or 0.0  # FALLBACK-OK: interfaces.md API-11 — sum of priced calls
            acc[1] += r["tok"] or 0  # FALLBACK-OK: interfaces.md QRY-11 — sums skip null tokens
    by_project = []
    for key, (c, t) in per_project.items():
        assert key in names, f"project {key!r} has no projects row (DM-60)"
        by_project.append(wire.DayProject(key, names[key], slots.get(key), c, t))
    by_project.sort(key=lambda p: (p.color_slot is None, p.color_slot or 0, -p.cost_usd, p.project))
    n_sessions = len({r["root_rowid"] for r in rows if r["calls"] > 0})
    return wire.DayBar(day, from_day, to_day, cost, unpriced > 0, unpriced, tok, n_sessions, by_project)


# ── list_sessions (QRY-15, QRY-16, QRY-26; API-12) ───────────────────────────


def list_sessions(
    scope: Scope,
    sort: str = "start",
    dir: str = "desc",
    offset: int = 0,
    limit: int = 200,
    group: str = "none",
    group_key: str | None = None,
    group_limit: int = 5,
    db: Db = None,
) -> wire.SessionPage:
    """QRY-15, QRY-16, QRY-26; API-12. `group` is `none`, `project` or `day`."""
    if sort not in SORTS:
        raise QueryError(f"sort must be one of {sorted(SORTS)}, got {sort!r}")
    if dir not in ("asc", "desc"):
        raise QueryError(f"dir must be asc or desc, got {dir!r}")
    if group not in ("none", "project", "day"):
        raise QueryError(f"group must be none, project or day, got {group!r}")
    if group_key is not None and group == "none":
        raise QueryError("group_key needs group=project or group=day")
    if offset < 0:
        raise QueryError(f"offset must be >= 0, got {offset}")
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit must be 1..{MAX_LIMIT}, got {limit}")
    if not 1 <= group_limit <= MAX_GROUP_LIMIT:
        raise QueryError(f"group_limit must be 1..{MAX_GROUP_LIMIT}, got {group_limit}")
    sql_dir = "DESC" if dir == "desc" else "ASC"
    key = SORTS[sort]
    # nulls last in both directions; ties by first_ms descending then op_id (QRY-15)
    order = f"({key} IS NULL), {key} {sql_dir}, s.first_ms DESC, o.op_id ASC"
    with _conn(db) as conn:
        tz = scope.tz
        slots = common.color_slots(conn, tz)
        rows = _rows(conn, scope, order)
        costs = sorted(_cost(r) for r in rows)
        stats = wire.SessionStats(
            max(_max_activity(r) for r in rows) if rows else 0,
            costs[-1] if costs else 0.0,
            costs[math.ceil(0.9 * len(costs)) - 1] if costs else None,
        )
        groups = None
        if group == "none":
            page, total = rows[offset : offset + limit], len(rows)
        else:
            keyed = [(_group_key(r, group, tz), r) for r in rows]
            if group_key is not None:
                members = [r for k, r in keyed if k == group_key]
                page, total = members[offset : offset + limit], len(members)
            else:
                page, total = [], len(rows)
                groups = _groups(conn, scope, keyed, group, sort, sql_dir, group_limit, tz, slots)
        return wire.SessionPage(
            _fresh(conn), total, offset, limit, stats, _session_rows(conn, page, slots, tz), groups
        )


def _max_activity(row: sqlite3.Row) -> int:
    tok = json.loads(row["activity_tok"])
    return max(tok) if tok else 0


def _group_key(row: sqlite3.Row, group: str, tz: str) -> str:
    return row["project"] if group == "project" else zone.local_day(row["first_ms"], tz)


def _session_rows(
    conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], slots: dict[str, int], tz: str
) -> list[wire.SessionRow]:
    if not rows:
        return []
    ids = _json_ids([r["op_rowid"] for r in rows])
    per_day: dict[int, dict[str, list[float]]] = defaultdict(dict)
    for s in conn.execute(
        "SELECT root_rowid, bucket_start_ms, SUM(cost_usd) AS cost, SUM(tok_total) AS tok, "
        "SUM(n_llm_calls) AS calls FROM rollup_time "
        "WHERE root_rowid IN (SELECT value FROM json_each(?)) GROUP BY root_rowid, bucket_start_ms",
        [ids],
    ):
        acc = per_day[s["root_rowid"]].setdefault(zone.local_day(s["bucket_start_ms"], tz), [0.0, 0, 0])
        # FALLBACK-OK: interfaces.md API-12 — a day split is the sum of priced calls, 0 when none
        acc[0] += s["cost"] or 0.0
        acc[1] += s["tok"] or 0
        acc[2] += s["calls"]
    splits = {
        root: [wire.DaySplit(day, c, t) for day, (c, t, calls) in sorted(days.items()) if calls > 0]
        for root, days in per_day.items()
    }
    agents = dict(
        conn.execute(
            "SELECT root_rowid, COUNT(DISTINCT agent_id) FROM ops WHERE kind = 'engagement' "
            "AND root_rowid IN (SELECT value FROM json_each(?)) GROUP BY root_rowid",
            [ids],
        ).fetchall()
    )
    out = []
    for r in rows:
        name, title_source = common.session_name(r)
        money = common.money(_cost(r), r["cost_lower_bound"], r["n_unpriced"])
        crossed = (
            date.fromisoformat(zone.local_day(r["last_ms"], tz))
            - date.fromisoformat(zone.local_day(r["first_ms"], tz))
        ).days
        out.append(
            wire.SessionRow(
                r["project"],
                r["project_name"],
                slots.get(r["project"]),
                *_money_args(money),
                r["op_id"],
                r["session_id"],
                r["provider"],
                name,
                title_source,
                r["first_ms"],
                r["last_ms"],
                crossed,
                r["incl_tok_total"],
                r["wall_ms"],
                r["active_ms"],
                bool(r["is_automated"]),
                agents.get(r["op_rowid"], 0),  # FALLBACK-OK: interfaces.md API-12 — no engagement, no agents
                r["git_branch"],  # FALLBACK-OK: interfaces.md API-12 — git_branch is null-able on the wire
                wire.Activity(r["activity_bin_ms"], json.loads(r["activity_tok"])),
                splits.get(r["op_rowid"], []),
            )
        )
    return out


def _groups(
    conn: sqlite3.Connection,
    scope: Scope,
    keyed: Sequence[tuple[str, sqlite3.Row]],
    group: str,
    sort: str,
    sql_dir: str,
    group_limit: int,
    tz: str,
    slots: dict[str, int],
) -> list[wire.SessionGroup]:
    members: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for k, r in keyed:
        members[k].append(r)
    strips = _strips(conn, scope, members, tz)

    def measure(rows: list[sqlite3.Row]) -> float | None:
        """QRY-26: the sum of the sort field; for `start`, the latest `first_ms` in the group."""
        if sort == "start":
            return max(r["first_ms"] for r in rows)
        field = {"cost": None, "wall": "wall_ms", "active": "active_ms", "tokens": "incl_tok_total"}[sort]
        vals = [_cost(r) if field is None else r[field] for r in rows]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    sign = -1 if sql_dir == "DESC" else 1
    measures = {k: measure(rows) for k, rows in members.items()}
    ordered = sorted(
        members.items(),
        key=lambda kv: (
            measures[kv[0]] is None,
            sign * (measures[kv[0]] or 0),
            -max(r["first_ms"] for r in kv[1]),
            kv[0],
        ),
    )
    out = []
    for k, rows in ordered:
        money, tok = _figures(rows)
        name = rows[0]["project_name"] if group == "project" else k
        assert name is not None, f"project {k!r} has no projects row (DM-60)"
        out.append(
            wire.SessionGroup(
                *_money_args(money),
                k,
                name,
                slots.get(k) if group == "project" else None,
                len(rows),
                tok,
                sum(r["wall_ms"] for r in rows),
                sum(r["active_ms"] for r in rows),
                strips[k],
                _session_rows(conn, rows[:group_limit], slots, tz),
            )
        )
    return out


def _period_extent(conn: sqlite3.Connection, scope: Scope) -> tuple[str, str]:
    if scope.from_day is not None:
        return scope.from_day, scope.to_day
    first, last = _llm_extent(conn, scope.tz)
    assert first is not None, "sessions are in scope but the store has no llm bucket"
    return first, last


def _strips(
    conn: sqlite3.Connection, scope: Scope, members: dict[str, list[sqlite3.Row]], tz: str
) -> dict[str, wire.PeriodActivity]:
    """SYS-37 group strips: hour sums for a period of 14 days or less, else day sums (API-12)."""
    first, last = _period_extent(conn, scope)
    n_days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    hourly = n_days <= HOUR_STRIP_MAX_DAYS
    day_edges = zone.day_edges(first, last, tz)
    start, end = day_edges[0], day_edges[-1]
    bin_ms = HOUR_MS if hourly else DAY_MS
    n_bins = math.ceil((end - start) / HOUR_MS) if hourly else n_days
    owner = {r["op_rowid"]: k for k, rows in members.items() for r in rows}
    tok: dict[str, list[int]] = {k: [0] * n_bins for k in members}
    for s in conn.execute(
        "SELECT root_rowid, bucket_start_ms, SUM(tok_total) AS tok FROM rollup_time "
        "WHERE bucket_start_ms >= ? AND bucket_start_ms < ? "
        "AND root_rowid IN (SELECT value FROM json_each(?)) GROUP BY root_rowid, bucket_start_ms",
        [start, end, _json_ids(list(owner))],
    ):
        at = s["bucket_start_ms"]
        i = (at - start) // HOUR_MS if hourly else bisect_right(day_edges, at) - 1
        assert 0 <= i < n_bins, f"activity row outside the period: bin {i} of {n_bins}"
        tok[owner[s["root_rowid"]]][i] += s["tok"] or 0  # FALLBACK-OK: interfaces.md QRY-11 — null adds 0
    return {k: wire.PeriodActivity(bin_ms, start, v) for k, v in tok.items()}


# ── facets (QRY-27; API-13) ──────────────────────────────────────────────────


def _facet_counts(conn: sqlite3.Connection, scope: Scope) -> dict[str, dict[str, int]]:
    """Every value of each facet with its in-scope count under the scope minus that facet's filter (QRY-8)."""
    out: dict[str, dict[str, int]] = {}
    specs = (
        ("providers", "SELECT DISTINCT provider FROM sessions", "s.provider", None),
        (
            "models",
            "SELECT DISTINCT model FROM rollup_time WHERE model IS NOT NULL",
            "m.model",
            "JOIN rollup_time m ON m.root_rowid = s.op_rowid",
        ),
        (
            "branches",
            "SELECT DISTINCT git_branch FROM sessions WHERE git_branch IS NOT NULL",
            "s.git_branch",
            None,
        ),
        ("projects", "SELECT project FROM projects", "s.project", None),
    )
    for kind, values_sql, column, join in specs:
        where, args = _where(scope, ignore=(kind,))
        counts = dict(
            conn.execute(
                f"SELECT {column}, COUNT(DISTINCT s.op_rowid) {_FROM} {join or ''} "
                f"WHERE {where} AND {column} IS NOT NULL GROUP BY {column}",
                args,
            ).fetchall()
        )
        out[kind] = {r[0]: counts.get(r[0], 0) for r in conn.execute(values_sql).fetchall()}
    return out


def _ranked(counts: dict[str, int]) -> list[wire.FacetValue]:
    return [wire.FacetValue(v, n) for v, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def facets(scope: Scope, db: Db = None) -> wire.Facets:
    """QRY-27; API-13."""
    with _conn(db) as conn:
        counts = _facet_counts(conn, scope)
        automated = _automated_count(conn, scope)
        return wire.Facets(
            _fresh(conn),
            _ranked(counts["providers"]),
            _ranked(counts["models"]),
            _ranked(counts["branches"]),
            wire.AutomatedFacet(automated),
        )


def _automated_count(conn: sqlite3.Connection, scope: Scope) -> int:
    """Automated sessions in the period under the other filters (EXP-24), whatever `show_automated` says."""
    where, args = _where(scope, automated=False)
    return conn.execute(f"SELECT COUNT(*) {_FROM} WHERE {where} AND s.is_automated = 1", args).fetchone()[0]


# ── palette (QRY-28; API-14) ─────────────────────────────────────────────────


def palette(
    text: str, scope: Scope, limit_sessions: int = 8, limit_projects: int = 4, db: Db = None
) -> wire.PaletteResult:
    """QRY-28; API-14."""
    text = text.strip()
    with _conn(db) as conn:
        if not text:
            return wire.PaletteResult(_fresh(conn), [], [], [])
        return wire.PaletteResult(
            _fresh(conn),
            _filter_values(conn, text, scope),
            _palette_projects(conn, text, limit_projects, scope.tz),
            _palette_sessions(conn, text, scope, limit_sessions),
        )


def _filter_values(conn: sqlite3.Connection, text: str, scope: Scope) -> list[wire.FilterValue]:
    kind, colon, rest = text.partition(":")
    if kind.lower() not in FILTER_KINDS or not colon:
        return []
    kind, needle = kind.lower(), rest.strip().lower()
    counts = _facet_counts(conn, scope)[FILTER_KINDS[kind]]
    labels: dict[str, str] = {}
    if kind == "provider":
        labels = _LABELS
    elif kind == "project":
        labels = dict(conn.execute("SELECT project, name FROM projects").fetchall())
    out = [
        wire.FilterValue(kind, v, labels.get(v, v), n)
        for v, n in counts.items()
        # FALLBACK-OK: interfaces.md QRY-28 — a value without a display name is labelled by itself
        if needle in v.lower() or needle in labels.get(v, v).lower()
    ]
    out.sort(key=lambda f: (-f.n, f.value))
    return out


def _palette_projects(conn: sqlite3.Connection, text: str, limit: int, tz: str) -> list[wire.PaletteProject]:
    slots = common.color_slots(conn, tz)
    out = []
    for p in conn.execute(
        "SELECT project, name, n_sessions, n_automated FROM projects WHERE instr(lower(name), lower(?)) > 0",
        [text],
    ).fetchall():
        rows = conn.execute(
            f"{_ROW_SELECT} {_FROM} WHERE s.project = ? AND s.op_rowid IN ({_LLM_BUCKETS})", [p["project"]]
        ).fetchall()
        money, _tok = _figures(rows)
        out.append(
            wire.PaletteProject(
                p["project"],
                p["name"],
                slots.get(p["project"]),
                *_money_args(money),
                p["n_sessions"],
                p["n_automated"],
            )  # fmt: skip
        )
    out.sort(key=lambda p: (-(p.cost_usd or 0.0), p.project))
    return out[:limit]


def _palette_sessions(
    conn: sqlite3.Connection, text: str, scope: Scope, limit: int
) -> list[wire.PaletteSession]:
    slots = common.color_slots(conn, scope.tz)
    terms = text.split()
    match_sql, match_args = [], []
    for t in terms:
        sql, a = _term_sql(t)
        match_sql.append(sql)
        match_args += a
    in_filters, filter_args = _where(scope, period=False)
    if scope.from_day is None:
        in_period, period_args = "1", []
    else:
        in_period = (
            "EXISTS (SELECT 1 FROM rollup_time d WHERE d.root_rowid = s.op_rowid AND d.n_llm_calls > 0 "
            "AND d.bucket_start_ms >= ? AND d.bucket_start_ms < ?)"
        )
        period_args = _period_range(scope)
    name_hits = " + ".join(f"(instr(lower({_NAME}), lower(?)) > 0)" for _ in terms)
    rows = conn.execute(
        f"{_ROW_SELECT}, ({in_period}) AS in_period, ({in_filters}) AS in_filters, "
        "(SELECT MIN(f.bucket_start_ms) FROM rollup_time f WHERE f.root_rowid = s.op_rowid "
        "AND f.n_llm_calls > 0) AS first_ms "
        f"{_FROM} WHERE s.op_rowid IN ({_LLM_BUCKETS}) AND {' AND '.join(match_sql)} "
        f"ORDER BY ({name_hits}) DESC, (lower(p.name) = lower(?)) DESC, s.first_ms DESC, o.op_id ASC LIMIT ?",
        [*period_args, *filter_args, *match_args, *terms, text, limit],
    ).fetchall()
    out = []
    for r in rows:
        name, title_source = common.session_name(r)
        out.append(
            wire.PaletteSession(
                r["op_id"],
                r["session_id"],
                r["provider"],
                name,
                title_source,
                r["project"],
                r["project_name"],
                slots.get(r["project"]),
                r["first_ms"],
                *_money_args(common.money(_cost(r), r["cost_lower_bound"], r["n_unpriced"])),
                bool(r["is_automated"]),
                zone.local_day(r["first_ms"], scope.tz),
                bool(r["in_period"]),
                bool(r["in_filters"]),
            )  # fmt: skip
        )
    return out
