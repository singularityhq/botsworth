"""CONTRACT: helpers that several view modules share, so no two tasks write them.

GUARANTEES:
- `lookup_session(conn, key)` (QRY-6) returns one row: every `sessions` column plus `op_id` (the session
  op's op_id) and `project_name` (`projects.name`). `key` is the session op_id, the bare `session_id`, or a
  prefix of `session_id` of at least 8 characters. More than one match -> `QueryError` naming up to 5
  matches; a prefix under 8 characters that is not a whole session_id -> `QueryError`; none -> `NotFound`.
- `session_name(row)` (QRY-13) returns `(name, title_source)` for a row with `display_title`,
  `title_source`, `project_name`, `session_id`: `display_title`, or `<project_name> · <session_id[:8]>`.
- `color_slots(conn, tz)` and `project_ref(conn, project, tz)` (QRY-14, D50): the slot depends on the store
  and the request zone `tz`. Rank projects by Σ `rollup_time.cost_usd` over the 30 local days in `tz` ending
  at the local day of `meta.last_ingest_ms` (the epoch range from local midnight of the first day to local
  midnight after the last); ranks 1-6 get slots 1-6, ties by project key ascending; projects with no priced
  bucket in the window and every project below rank 6 get None. Before the first ingest every slot is None.
- `edge_ranges(edges)` -> `(sql, arg)`: a derived table `(i, lo, hi)` of the consecutive epoch ranges of
  `edges`, with `arg` the one JSON parameter it binds. Callers join `rollup_time` on
  `bucket_start_ms >= lo AND bucket_start_ms < hi`.
- `money(cost, lower_bound, n_unpriced)` (QRY-11) builds `wire.Money`. Callers pass None for `cost` only
  when the item has no owned llm_call.
- `wire_split(acc)` builds a `wire.TimeSplit` from `{(state, substate): ms}`: the parts above 0 ms in
  SES-15 order (`timesplit.ordered`). `stored_split(conn, root_rowid, scope)` reads the root's `time_split`
  rows of one scope (DM-51, DM-53) as `{op_rowid: {(state, substate): ms}}`; an op with no row is absent.
  `session_parts(conn, roots)` reads the scope `session` rows of many sessions at once, by session op rowid.
- `test_runs(conn, roots)` (CLI-20 `test_runs`, D55) counts the owned tool_calls with `command_class` test of
  `roots`; `run_ms` runs to `bg_end_ms` for a background run, else `dur_ms`. `slow`, `breakdown` and the
  Review (QRY-42) all use it, so their blocks are equal.
EXPECTS: a connection from `botsworth.query._conn` (schema v12) with `row_factory = sqlite3.Row`.
FAILURE BEHAVIOR: as above. A session with no `projects` row, or a null `project_name`, is an ingest
  bug (DM-60 names every project): `session_name` and `project_ref` assert.
DOES NOT: build wire views, paginate, or filter by scope.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from botsworth.query import NotFound, QueryError, wire, zone
from botsworth.store import timesplit

MIN_PREFIX = 8
COLOR_SLOTS = 6
COLOR_WINDOW_DAYS = 30
_MAX_NAMED = 5
_IDS = "(SELECT value FROM json_each(:ids))"

Parts = dict[int, dict[timesplit.Part, int]]

_SESSION_SELECT = """
SELECT s.*, o.op_id AS op_id, p.name AS project_name
FROM sessions s
JOIN ops o ON o.rowid = s.op_rowid
LEFT JOIN projects p ON p.project = s.project
"""


def lookup_session(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    rows = conn.execute(f"{_SESSION_SELECT} WHERE o.op_id = ?", (key,)).fetchall()
    if not rows:
        rows = conn.execute(f"{_SESSION_SELECT} WHERE s.session_id = ? ORDER BY o.op_id", (key,)).fetchall()
    if not rows:
        if len(key) < MIN_PREFIX:
            raise QueryError(
                f"session {key!r} is not a session id; a prefix needs at least {MIN_PREFIX} characters"
            )
        rows = conn.execute(
            f"{_SESSION_SELECT} WHERE substr(s.session_id, 1, ?) = ? ORDER BY o.op_id", (len(key), key)
        ).fetchall()
    if not rows:
        raise NotFound(f"no session {key!r}")
    if len(rows) > 1:
        named = ", ".join(r["op_id"] for r in rows[:_MAX_NAMED])
        more = f" and {len(rows) - _MAX_NAMED} more" if len(rows) > _MAX_NAMED else ""
        raise QueryError(f"session {key!r} is ambiguous: {named}{more}")
    return rows[0]


def session_name(row: sqlite3.Row) -> tuple[str, str]:
    if row["display_title"] is not None:
        return row["display_title"], row["title_source"]
    assert row["project_name"] is not None, f"session {row['session_id']} has no projects row (DM-60)"
    # title_source is 'fallback' whenever display_title is null (DM-10)
    return f"{row['project_name']} · {row['session_id'][:8]}", "fallback"


def edge_ranges(edges: Sequence[int]) -> tuple[str, str]:
    sql = (
        "(SELECT i, lo, hi FROM (SELECT key AS i, value AS lo, LEAD(value) OVER (ORDER BY key) AS hi "
        "FROM json_each(?)) WHERE hi IS NOT NULL)"
    )
    return sql, json.dumps(list(edges))


def color_slots(conn: sqlite3.Connection, tz: str) -> dict[str, int]:
    row = conn.execute("SELECT value FROM meta WHERE key = 'last_ingest_ms'").fetchone()
    if row is None:
        # FALLBACK-OK: interfaces.md QRY-14 — the window ends at last_ingest_ms, which a new store lacks
        return {}
    last = zone.local_day(int(row["value"]), tz)
    first = (date.fromisoformat(last) - timedelta(days=COLOR_WINDOW_DAYS - 1)).isoformat()
    lo, hi = zone.day_bounds(first, tz)[0], zone.day_bounds(last, tz)[1]
    ranked = conn.execute(
        """
        SELECT s.project AS project
        FROM rollup_time r JOIN sessions s ON s.op_rowid = r.root_rowid
        WHERE r.bucket_start_ms >= ? AND r.bucket_start_ms < ?
        GROUP BY s.project
        HAVING SUM(r.cost_usd) IS NOT NULL
        ORDER BY SUM(r.cost_usd) DESC, s.project ASC
        LIMIT ?
        """,
        (lo, hi, COLOR_SLOTS),
    ).fetchall()
    return {r["project"]: i for i, r in enumerate(ranked, start=1)}


def project_ref(conn: sqlite3.Connection, project: str, tz: str) -> wire.ProjectRef:
    row = conn.execute("SELECT name FROM projects WHERE project = ?", (project,)).fetchone()
    assert row is not None, f"project {project!r} has no projects row (DM-60)"
    return wire.ProjectRef(project, row["name"], color_slots(conn, tz).get(project))


def money(cost: float | None, lower_bound: bool, n_unpriced: int) -> wire.Money:
    return wire.Money(cost, bool(lower_bound), int(n_unpriced))


def wire_split(acc: Mapping[timesplit.Part, int]) -> wire.TimeSplit:
    return wire.TimeSplit([wire.TimePart(s, sub, ms) for s, sub, ms in timesplit.ordered(acc)])


def stored_split(
    conn: sqlite3.Connection, root_rowid: int, scope: str
) -> dict[int, dict[timesplit.Part, int]]:
    out: dict[int, dict[timesplit.Part, int]] = defaultdict(dict)
    for r in conn.execute(
        "SELECT op_rowid, state, substate, ms FROM time_split WHERE root_rowid = ? AND scope = ?",
        (root_rowid, scope),
    ):
        out[r["op_rowid"]][r["state"], r["substate"]] = r["ms"]
    return dict(out)


def session_parts(conn: sqlite3.Connection, roots: Sequence[int]) -> Parts:
    """The stored `time_split` rows of scope `session`, by session op rowid (DM-51)."""
    out: Parts = defaultdict(dict)
    for r in conn.execute(
        "SELECT op_rowid, state, substate, ms FROM time_split WHERE scope = 'session' "
        f"AND root_rowid IN {_IDS}",
        {"ids": json.dumps(list(roots))},
    ):
        out[r["op_rowid"]][r["state"], r["substate"]] = r["ms"]
    return out


def test_runs(conn: sqlite3.Connection, roots: Sequence[int]) -> wire.TestRuns:
    """CLI-20 `test_runs` (D55): owned tool_calls with `command_class = 'test'` of `roots`."""
    r = conn.execute(
        "SELECT COUNT(*) AS n,"
        " COALESCE(SUM(CASE WHEN bg_end_ms IS NOT NULL THEN bg_end_ms - start_ms ELSE dur_ms END), 0)"
        " AS run_ms,"
        " COALESCE(SUM(tests_failed > 0), 0) AS n_failed, COALESCE(SUM(tests_failed = 0), 0) AS n_passed,"
        " COALESCE(SUM(tests_passed IS NULL AND tests_failed IS NULL), 0) AS n_no_result,"
        " COALESCE(SUM(bg_end_ms IS NOT NULL), 0) AS n_background"
        " FROM ops WHERE kind = 'tool_call' AND duplicate_of IS NULL AND command_class = 'test'"
        f" AND root_rowid IN {_IDS}",
        {"ids": json.dumps(list(roots))},
    ).fetchone()
    return wire.TestRuns(
        r["n"], r["run_ms"], r["n_failed"], r["n_passed"], r["n_no_result"], r["n_background"]
    )
