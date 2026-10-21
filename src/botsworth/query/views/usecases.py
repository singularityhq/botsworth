"""CONTRACT: use-case commands (owner Q3b): slow, breakdown, tasks. CLI only, no HTTP route in v4.

Spec ids: CLI-20, CLI-21, CLI-22, CLI-23 (interfaces.md §5.2; D55 amends 20 to 22,
D56 adds `review`); D42 (token split basis).

GUARANTEES:
- Facts only: numbers, ids, labels and stored previews. No prose, no cause, no "slow" threshold.
- `project_slow(scope, compare, n)` (CLI-20): sessions = `explorer.in_scope_roots` (QRY-5, QRY-7), every
  figure the session's full figure (QRY-9). `current.time_split` is Σ per (state, substate) of the sessions'
  stored `time_split` rows of scope `session`, so it sums to `wall_ms`. `previous` is the same block for
  the `d` days ending the day before `from_day` (`d` = the scope's day count), same filters; null when
  `compare` is false or the scope is all time. `tools` / `slowest_tools` skip calls with `user_wait` set
  (DM-40, D54) and count them in `user_waits` (`ask`, `permission`: `{n, dur_ms}`). Percentiles are
  nearest-rank (rank = ceil(p * N)) over non-null `dur_ms`. `models` is Q2a's `model_lines`.
  A wait (D54) is a stretch of 2 min or more, or a main-thread tool_call with `user_wait` set and
  `dur_ms` of 2 min or more, as `Wait` (`row` = the call, null for a stretch): per session the hit set of
  the `waits` finder (QRY-38). `waits` = the `n` longest across the sessions; `longest_wait` = the
  session's longest wait of state `you`, `agentmsg`, `limit` or `none` (not `agents` or `tool`, D55).
  `test_runs` (D55) counts the owned tool_calls with `command_class` test: `run_ms` runs to `bg_end_ms` for a
  background run, else `dur_ms`; `n_failed` / `n_passed` / `n_no_result` by the test counts (DM-29)
  (`common.test_runs`). `review` (D56) is `review.build` over the same sessions (`session_breakdown`: this
  session only, no `sessions` lists), so its `test_runs` equals the block above.
- `session_breakdown(session, n)` (CLI-21) composes: `session` copies `session_detail`; the turn lists
  are `session_turns` rows (harness turns and turns without the figure left out); `rows_by_*` are the
  first `n` hits of `find(.., 'biggest', measure)`; `agents` are the agent rows `find(.., 'agents')`
  names, any depth, with Q2b's `RowBuilder` figures, by cost; `waits` are the session's CLI-20 waits;
  `models` is Q2a's.
- `top_tasks(scope, by, measure, n)` (CLI-22): one SQL statement per `by` over owned leaves.
  `issuing_call_split`:
  a call's `tok_total` and `cost_usd` are divided evenly among its owned tool_calls (for `verb`, then among
  that tool_call's steps); `by=tool` also lists calls that issued no tool_call as `"no tool"`; for
  `command_key`, `command_class` and `verb` those calls, and the share of tool_calls without a key or class,
  are in `unassigned`.
  `turn_split`: the attributed turn's `attr_*` and `span_wall_ms` divided evenly among the distinct skills
  loaded in it (a skill load in an agent counts for the main turn it is attributed to). `nearest_engagement`:
  each owned llm_call goes to its nearest engagement ancestor's `agent_type` (`(none)` for a null type);
  none above it -> `(main)`. `turn_attr`: whole turn figures. `unassigned` = `scope_totals` - Σ all groups.
  `n_unpriced` of a group counts the source units (calls; turns for `skill`/`slash`) whose cost is unknown.
  Groups sort by `measure` descending (nulls last), then key ascending; `count` sorts by `n`.
EXPECTS: schema v12 filled by B3a/B3b (`user_wait`, `max_silence_ms`, `time_prov`, `command_key`,
  `command_class`,
  `tests_passed`, `tests_failed`, `bg_end_ms`,
  `steps.verb`, `steps.skill_load`, `turn_origin`, `label`, `attr_*`, `span_wall_ms`, `time_split`,
  `stretches`).
FAILURE BEHAVIOR: `by`, `measure` or `n` outside the lists -> `QueryError`; unknown session -> `NotFound`;
  a session whose `time_split` rows do not sum to `wall_ms` asserts (DM-51).
DOES NOT: write, hold prose, label anything slow, or use `op_closure`.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import fields, replace
from datetime import date, timedelta
from typing import Any

from botsworth.query import Db, QueryError, _conn, shapes, wire
from botsworth.query.scope import Scope
from botsworth.query.views import common, review
from botsworth.query.views import rows as R
from botsworth.query.views.explorer import _figures, in_scope_roots
from botsworth.query.views.find import find
from botsworth.query.views.session import model_lines, session_detail, session_turns
from botsworth.query.views.stretches import stretches_for_roots
from botsworth.store import timesplit

MAX_SLOW_N = 100
MAX_TASKS_N = 500
WAIT_FLOOR_MS = 120_000  # SES-27, CLI-20 `longest_wait` and `waits`
BY = ("command_key", "command_class", "tool", "verb", "skill", "agent_type", "slash")
MEASURES = ("tokens", "cost", "time", "count")
BASIS = {
    "tool": "issuing_call_split",
    "command_key": "issuing_call_split",
    "command_class": "issuing_call_split",
    "verb": "issuing_call_split",
    "skill": "turn_split",
    "agent_type": "nearest_engagement",
    "slash": "turn_attr",
}
_IDS = "(SELECT value FROM json_each(:ids))"
_CHUNK = 500  # roots per stretches_for_roots call (SQLite variable limit)


def _json_ids(roots: Sequence[int]) -> str:
    return json.dumps(list(roots))


def _check_n(n: int, high: int) -> None:
    if not 1 <= n <= high:
        raise QueryError(f"n must be between 1 and {high}, got {n}")


def _fresh(conn: sqlite3.Connection) -> wire.Freshness:
    return wire.Freshness(**shapes.freshness(conn))


# ── CLI-20: project_slow ─────────────────────────────────────────────────────

_SESSION_ROWS = f"""
SELECT s.op_rowid, o.op_id, s.session_id, s.display_title, s.title_source, p.name AS project_name,
  s.first_ms, s.wall_ms, s.active_ms,
  s.n_llm_calls, s.n_tool_calls, s.incl_tok_total, s.incl_cost_usd, s.cost_lower_bound,
  (SELECT COALESCE(SUM(u.n_unpriced), 0) FROM rollup_time u
    WHERE u.root_rowid = s.op_rowid) AS n_unpriced
FROM sessions s JOIN ops o ON o.rowid = s.op_rowid LEFT JOIN projects p ON p.project = s.project
WHERE s.op_rowid IN {_IDS}
"""


def _waits(conn: sqlite3.Connection, roots: Sequence[int]) -> dict[int, list[wire.Wait]]:
    """CLI-20 waits (D54) per root, longest first: stretches of 2 min or more, and main-thread tool_calls with
    `user_wait` set and `dur_ms` of 2 min or more (the hit set of the `waits` finder, QRY-38)."""
    out: dict[int, list[wire.Wait]] = defaultdict(list)
    for i in range(0, len(roots), _CHUNK):
        for root, sts in stretches_for_roots(conn, roots[i : i + _CHUNK]).items():
            out[root] += [
                wire.Wait(**{f.name: getattr(s, f.name) for f in fields(wire.Stretch)}, row=None)
                for s in sts
                if s.dur_ms >= WAIT_FLOOR_MS
            ]
    for r in conn.execute(
        "SELECT o.root_rowid AS root, o.op_id AS op_id, o.start_ms AS start_ms, o.end_ms AS end_ms, "
        "o.dur_ms AS dur_ms, o.user_wait AS user_wait, t.op_id AS turn "
        "FROM ops o LEFT JOIN ops t ON t.rowid = o.attr_turn_rowid "
        f"WHERE o.root_rowid IN {_IDS} AND o.kind = 'tool_call' AND o.agent_id IS NULL "
        "AND o.duplicate_of IS NULL AND o.user_wait IS NOT NULL AND o.dur_ms >= :floor",
        {"ids": _json_ids(roots), "floor": WAIT_FLOOR_MS},
    ):
        assert r["turn"] is not None, f"main-thread tool {r['op_id']} has no attributed turn (DM-45)"
        assert r["end_ms"] is not None, f"tool {r['op_id']} has a duration but no end (DM-40)"
        out[r["root"]].append(
            wire.Wait(
                r["start_ms"],
                r["end_ms"],
                r["dur_ms"],
                "you",
                r["user_wait"],
                r["turn"],
                None,
                [],
                wire.RowRef("tool", r["op_id"]),
            )  # fmt: skip
        )
    for ws in out.values():
        ws.sort(key=lambda w: (-w.dur_ms, w.start_ms))
    return out


Parts = common.Parts


def _split_row(row: sqlite3.Row, parts: Parts) -> wire.TimeSplit:
    for col in ("wall_ms", "active_ms"):
        assert row[col] is not None, f"session {row['op_id']} has no {col} (DM-51)"
    acc = parts[row["op_rowid"]]
    assert sum(acc.values()) == row["wall_ms"], f"session {row['op_id']}: time_split does not sum to wall_ms"
    return common.wire_split(acc)


def _block(rows: Sequence[sqlite3.Row], parts: Parts) -> wire.SlowBlock:
    money, tok = _figures(rows)
    total: dict[timesplit.Part, int] = defaultdict(int)
    for r in rows:
        _split_row(r, parts)  # the asserts of DM-51
        for part, ms in parts[r["op_rowid"]].items():
            total[part] += ms
    return wire.SlowBlock(
        n_sessions=len(rows),
        wall_ms=sum(r["wall_ms"] for r in rows),
        active_ms=sum(r["active_ms"] for r in rows),
        time_split=common.wire_split(total),
        n_llm_calls=sum(r["n_llm_calls"] for r in rows),
        n_tool_calls=sum(r["n_tool_calls"] for r in rows),
        tok_total=tok,
        cost=money,
    )


_TOOL_GROUPS = f"""
WITH base AS (
  SELECT o.root_rowid AS r, o.tool_name AS tn, o.command_key AS ck, o.dur_ms AS d, o.time_prov AS tp,
         o.max_silence_ms AS ms
  FROM ops o
  WHERE o.kind = 'tool_call' AND o.duplicate_of IS NULL AND o.user_wait IS NULL
    AND o.root_rowid IN {_IDS}
), ranked AS (
  SELECT tn, ck, d, ROW_NUMBER() OVER (PARTITION BY tn, ck ORDER BY d) AS rn,
         COUNT(*) OVER (PARTITION BY tn, ck) AS cn
  FROM base WHERE d IS NOT NULL
), pct AS (
  SELECT tn, ck, MAX(CASE WHEN rn = (cn * 50 + 99) / 100 THEN d END) AS p50,
         MAX(CASE WHEN rn = (cn * 90 + 99) / 100 THEN d END) AS p90, MAX(d) AS mx
  FROM ranked GROUP BY tn, ck
), agg AS (
  SELECT tn, ck, COUNT(*) AS n, COUNT(DISTINCT r) AS ns, COALESCE(SUM(d), 0) AS dur,
         COALESCE(SUM(tp = 'derived'), 0) AS nd, COALESCE(SUM(ms >= 300000), 0) AS nr
  FROM base GROUP BY tn, ck
)
SELECT agg.*, pct.p50, pct.p90, pct.mx
FROM agg LEFT JOIN pct ON pct.tn IS agg.tn AND pct.ck IS agg.ck
ORDER BY agg.dur DESC, agg.tn, agg.ck LIMIT :n
"""


def tool_groups(conn: sqlite3.Connection, roots: Sequence[int], n: int) -> list[wire.ToolGroup]:
    """CLI-20 `tools`: owned tool_calls of `roots` without `user_wait` by (tool_name, command_key)."""
    rows = conn.execute(_TOOL_GROUPS, {"ids": _json_ids(roots), "n": n}).fetchall()
    return [
        wire.ToolGroup(
            tool_name=r["tn"],
            command_key=r["ck"],
            n=r["n"],
            n_sessions=r["ns"],
            dur_ms=r["dur"],
            p50_ms=r["p50"],
            p90_ms=r["p90"],
            max_ms=r["mx"],
            n_derived=r["nd"],
            n_no_records=r["nr"],
        )
        for r in rows
    ]


_SLOWEST = f"""
SELECT t.op_id, t.tool_name, t.command_key, t.dur_ms, t.time_prov, t.max_silence_ms,
       so.op_id AS session, tu.op_id AS turn, tu.display_ix AS display_ix,
       (SELECT st.summary FROM steps st WHERE st.op_rowid = t.rowid ORDER BY st.ordinal LIMIT 1) AS summary
FROM ops t JOIN ops so ON so.rowid = t.root_rowid LEFT JOIN ops tu ON tu.rowid = t.attr_turn_rowid
WHERE t.kind = 'tool_call' AND t.duplicate_of IS NULL AND t.user_wait IS NULL
  AND t.dur_ms IS NOT NULL AND t.root_rowid IN {_IDS}
ORDER BY t.dur_ms DESC, t.op_id LIMIT :n
"""


def _slowest_tools(conn: sqlite3.Connection, roots: Sequence[int], n: int) -> list[wire.SlowestTool]:
    out = []
    for r in conn.execute(_SLOWEST, {"ids": _json_ids(roots), "n": n}):
        out.append(
            wire.SlowestTool(
                row=wire.RowRef("tool", r["op_id"]),
                session=r["session"],
                turn=r["turn"],
                display_id=f"T{r['display_ix']}" if r["display_ix"] is not None else None,
                tool_name=r["tool_name"],
                command_key=r["command_key"],
                # FALLBACK-OK: interfaces.md API-22 — a tool with no stored step has an empty summary
                summary=r["summary"] or "",
                dur_ms=r["dur_ms"],
                # FALLBACK-OK: interfaces.md §3.3 — a tool with no time provenance reads `missing`
                time_prov=r["time_prov"] or "missing",
                max_silence_ms=r["max_silence_ms"],
            )
        )
    return out


def _user_waits(conn: sqlite3.Connection, roots: Sequence[int]) -> wire.UserWaits:
    """CLI-20 `user_waits`: owned tool_calls with each `user_wait`, counted and summed."""
    got = {
        r["user_wait"]: wire.UserWaitCount(r["n"], r["d"])
        for r in conn.execute(
            "SELECT user_wait, COUNT(*) AS n, COALESCE(SUM(dur_ms), 0) AS d FROM ops "
            f"WHERE kind = 'tool_call' AND duplicate_of IS NULL AND user_wait IS NOT NULL "
            f"AND root_rowid IN {_IDS} GROUP BY user_wait",
            {"ids": _json_ids(roots)},
        )
    }
    # FALLBACK-OK: CLI-20 (a category with no call counts n 0, dur_ms 0)
    none = wire.UserWaitCount(0, 0)
    return wire.UserWaits(ask=got.get("ask", none), permission=got.get("permission", none))


def _display_ids(conn: sqlite3.Connection, turn_ids: Sequence[str]) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT op_id, display_ix FROM ops WHERE op_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(set(turn_ids))),),
    )
    return {r["op_id"]: f"T{r['display_ix']}" if r["display_ix"] is not None else None for r in rows}


def _longest_wait(waits: Sequence[wire.Wait]) -> wire.Wait | None:
    """CLI-20: the first wait not of state `agents` or `tool` (running time, D55; longest first)."""
    return next((w for w in waits if w.state not in ("agents", "tool")), None)


def _previous_scope(scope: Scope) -> Scope:
    first, last = date.fromisoformat(scope.from_day), date.fromisoformat(scope.to_day)
    days = (last - first).days + 1
    return replace(
        scope,
        from_day=(first - timedelta(days=days)).isoformat(),
        to_day=(first - timedelta(days=1)).isoformat(),
    )


def project_slow(scope: Scope, compare: bool = True, n: int = 10, db: Db = None) -> wire.SlowReport:
    """CLI-20. `n` is 1 to 100."""
    _check_n(n, MAX_SLOW_N)
    with _conn(db) as conn:
        roots = in_scope_roots(conn, scope)
        rows = conn.execute(_SESSION_ROWS, {"ids": _json_ids(roots)}).fetchall()
        waits_by_root = _waits(conn, roots)
        parts = common.session_parts(conn, roots)
        current = _block(rows, parts)
        previous = None
        if compare and scope.from_day is not None:
            prev_roots = in_scope_roots(conn, _previous_scope(scope))
            prev_rows = conn.execute(_SESSION_ROWS, {"ids": _json_ids(prev_roots)}).fetchall()
            previous = _block(prev_rows, common.session_parts(conn, prev_roots))
        biggest = sorted(rows, key=lambda r: (-r["wall_ms"], r["op_id"]))[:n]
        sessions = []
        for r in biggest:
            name, _ = common.session_name(r)
            money, tok = _figures([r])
            sessions.append(
                wire.SlowSession(
                    session=r["op_id"],
                    session_id=r["session_id"],
                    name=name,
                    project_name=r["project_name"],
                    start_ms=r["first_ms"],
                    wall_ms=r["wall_ms"],
                    active_ms=r["active_ms"],
                    time_split=_split_row(r, parts),
                    cost=money,
                    tok_total=tok,
                    longest_wait=_longest_wait(waits_by_root[r["op_rowid"]]),
                )
            )
        op_ids = {r["op_rowid"]: r["op_id"] for r in rows}
        long = sorted(
            ((root, w) for root, ws in waits_by_root.items() for w in ws),
            key=lambda p: (-p[1].dur_ms, op_ids[p[0]], p[1].start_ms),
        )[:n]
        shown = _display_ids(conn, [st.turn for _, st in long if st.turn is not None])
        waits = [
            wire.SessionWait(
                **{f.name: getattr(st, f.name) for f in fields(wire.Wait)},
                session=op_ids[root],
                display_id=shown.get(st.turn) if st.turn is not None else None,
            )
            for root, st in long
        ]
        return wire.SlowReport(
            freshness=_fresh(conn),
            current=current,
            previous=previous,
            sessions=sessions,
            tools=tool_groups(conn, roots, n),
            slowest_tools=_slowest_tools(conn, roots, n),
            user_waits=_user_waits(conn, roots),
            test_runs=common.test_runs(conn, roots),
            waits=waits,
            models=_models(conn, roots),
            review=review.build(conn, roots, with_sessions=True),
        )


def _models(conn: sqlite3.Connection, roots: Sequence[int]) -> list[wire.ModelLine]:
    marks = ",".join("?" * len(roots))
    return model_lines(conn, f"o.root_rowid IN ({marks})", list(roots)) if roots else []


# ── CLI-21: session_breakdown ────────────────────────────────────────────────


def _agent_figures(
    conn: sqlite3.Connection, root: int, turn_op_ids: Sequence[str]
) -> dict[str, wire.AgentRow]:
    """Q2b's agent rows, any depth, of the given turns of one session, by agent-row id."""
    forest = R.Forest(conn, root)
    out: dict[str, wire.AgentRow] = {}

    def walk(builder: R.RowBuilder, entries: Sequence[Any], depth: int) -> None:
        agents = [e for e in entries if e.kind == "agent"]
        for row, e in zip(builder.build(agents, depth), agents, strict=True):
            assert isinstance(row, wire.AgentRow)
            out[row.id] = row
            walk(builder, builder.group_entries(e.row["rowid"]), depth + 1)

    for turn_id in turn_op_ids:
        turn_rowid = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (turn_id,)).fetchone()[0]
        builder = R.RowBuilder(conn, root, forest, R.scan_turn(conn, forest, turn_rowid))
        walk(builder, builder.turn_entries(turn_rowid), 0)
    return out


def session_breakdown(session: str, n: int = 10, db: Db = None) -> wire.SessionBreakdown:
    """CLI-21. `n` is 1 to 100."""
    _check_n(n, MAX_SLOW_N)
    with _conn(db) as conn:
        detail = session_detail(session, db=conn)
        info = detail.session
        root = common.lookup_session(conn, session)["op_rowid"]
        turns = [
            t
            for t in session_turns(info.session, db=conn).items
            if isinstance(t, wire.TurnRow) and t.kind != "harness"
        ]

        def top_turns(attr: str) -> list[wire.TurnRow]:
            have = [t for t in turns if getattr(t, attr) is not None]
            return sorted(have, key=lambda t: -getattr(t, attr))[:n]  # stable: ties keep time order

        biggest = {
            m: find(info.session, "biggest", m, limit=n, db=conn).hits for m in ("time", "cost", "tokens")
        }
        hits = find(info.session, "agents", limit=10_000, db=conn).hits
        figures = _agent_figures(conn, root, sorted({h.turn for h in hits}))
        agents = []
        for h in hits:
            a = figures[h.row.id]
            agents.append(
                wire.BreakdownAgent(
                    row=h.row,
                    turn=h.turn,
                    display_id=h.display_id,
                    depth=h.depth,
                    agent_names=a.agent_names,
                    cost_usd=a.cost_usd,
                    cost_lower_bound=a.cost_lower_bound,
                    n_unpriced=a.n_unpriced,
                    tok_total=a.tok_total,
                    run_ms=a.run_ms,
                    n_llm_calls=a.n_llm_calls,
                )
            )
        agents.sort(key=lambda a: (a.cost_usd is None, -(a.cost_usd or 0.0), a.row.id))
        long = _waits(conn, [root])[root][:n]
        return wire.SessionBreakdown(
            freshness=detail.freshness,
            session=wire.BreakdownSession(
                session=info.session,
                session_id=info.session_id,
                name=info.name,
                project_name=info.project_name,
                provider=info.provider,
                start_ms=info.start_ms,
                end_ms=info.end_ms,
                wall_ms=info.wall_ms,
                active_ms=info.active_ms,
                cost=common.money(info.cost.cost_usd, info.cost.cost_lower_bound, info.cost.n_unpriced),
                tok_total=info.tok_total,
                time_split=info.time_split,
            ),
            turns_by_time=top_turns("wall_ms"),
            turns_by_tokens=top_turns("tok_total"),
            rows_by_time=biggest["time"],
            rows_by_cost=biggest["cost"],
            rows_by_tokens=biggest["tokens"],
            tools=tool_groups(conn, [root], n),
            agents=agents[:n],
            waits=long,
            test_runs=common.test_runs(conn, [root]),
            models=info.models,
            review=review.build(conn, [root], with_sessions=False),
        )


# ── CLI-22: top_tasks ────────────────────────────────────────────────────────

# Items: one row per counted thing. key, root (session), ex/ex_type (example row), nw (weight in `n`),
# tok / cost (the share that reaches the key), unp (id of a source unit with unknown cost), dur.
_TC = f"""
tc AS (
  SELECT t.rowid AS trow, t.op_id AS op_id, t.root_rowid AS root, t.tool_name AS tool_name,
         t.command_key AS command_key, t.command_class AS command_class, t.dur_ms AS dur,
         t.parent_rowid AS parent,
         COUNT(*) OVER (PARTITION BY t.parent_rowid) AS k
  FROM ops t
  WHERE t.kind = 'tool_call' AND t.duplicate_of IS NULL AND t.root_rowid IN {_IDS}
)"""
_TOOL_ITEMS = """
SELECT {key} AS key, tc.root AS root, tc.op_id AS ex, 'tool' AS ex_type, 1 AS nw,
       p.tok_total * 1.0 / tc.k AS tok, p.cost_usd / tc.k AS cost,
       CASE WHEN p.rowid IS NOT NULL AND p.cost_usd IS NULL THEN p.rowid END AS unp, tc.dur * 1.0 AS dur
FROM tc LEFT JOIN ops p ON p.rowid = tc.parent AND p.kind = 'llm_call' AND p.duplicate_of IS NULL
{where}
"""
_NO_TOOL_ITEMS = f"""
SELECT 'no tool', c.root_rowid, c.op_id, 'call', 1, c.tok_total * 1.0, c.cost_usd,
       CASE WHEN c.cost_usd IS NULL THEN c.rowid END, NULL
FROM ops c
WHERE c.kind = 'llm_call' AND c.duplicate_of IS NULL AND c.root_rowid IN {_IDS}
  AND NOT EXISTS (SELECT 1 FROM ops t WHERE t.parent_rowid = c.rowid AND t.kind = 'tool_call'
                  AND t.duplicate_of IS NULL)
"""
_VERB_CTE = """
sv AS (
  SELECT tc.*, st.verb AS verb, COUNT(*) OVER (PARTITION BY tc.trow) AS ns
  FROM tc JOIN steps st ON st.op_rowid = tc.trow
)"""
_VERB_ITEMS = """
SELECT sv.verb, sv.root, sv.op_id, 'tool', 1, p.tok_total * 1.0 / sv.k / sv.ns, p.cost_usd / sv.k / sv.ns,
       CASE WHEN p.rowid IS NOT NULL AND p.cost_usd IS NULL THEN p.rowid END, sv.dur * 1.0 / sv.ns
FROM sv LEFT JOIN ops p ON p.rowid = sv.parent AND p.kind = 'llm_call' AND p.duplicate_of IS NULL
"""
_SKILL_CTE = f"""
loads AS (
  SELECT COALESCE(o.attr_turn_rowid, st.turn_rowid) AS turn, st.skill_load AS skill, st.root_rowid AS root,
         COUNT(*) AS nloads, MIN(o.op_id) AS ex, 'tool' AS ex_type
  FROM steps st JOIN ops o ON o.rowid = st.op_rowid
  WHERE st.skill_load IS NOT NULL AND st.root_rowid IN {_IDS}
  GROUP BY 1, 2
  UNION ALL
  SELECT t.rowid, json_extract(ev.fields, '$.skill'), ev.root_rowid, COUNT(*), t.op_id, 'turn'
  FROM events ev JOIN ops t ON t.op_id = ev.op_id AND t.kind = 'turn'
  WHERE ev.root_rowid IN {_IDS} AND ev.kind = 'skill_invocation' AND ev.copied = 0
    AND json_extract(ev.fields, '$.via') = 'slash'
    AND NOT EXISTS (SELECT 1 FROM steps s WHERE s.turn_rowid = t.rowid AND s.skill_load IS NOT NULL)
  GROUP BY 1, 2
), sk AS (SELECT loads.*, COUNT(*) OVER (PARTITION BY turn) AS nd FROM loads)"""
_SKILL_ITEMS = """
SELECT sk.skill, sk.root, sk.ex, sk.ex_type, sk.nloads, t.attr_tok_total * 1.0 / sk.nd,
       t.attr_cost_usd / sk.nd, CASE WHEN t.attr_cost_lower_bound = 1 THEN t.rowid END,
       t.span_wall_ms * 1.0 / sk.nd
FROM sk LEFT JOIN ops t ON t.rowid = sk.turn
"""
_AGENT_CTE = f"""
e AS (
  SELECT rowid AS erow, root_rowid AS root, pre, post, agent_type, op_id, wall_ms
  FROM ops WHERE kind = 'engagement' AND root_rowid IN {_IDS}
), m AS (
  SELECT lr, at FROM (
    SELECT l.rowid AS lr, e.agent_type AS at,
           ROW_NUMBER() OVER (PARTITION BY l.rowid ORDER BY e.pre DESC) AS rn
    FROM e CROSS JOIN ops l ON l.root_rowid = e.root AND l.pre > e.pre AND l.pre <= e.post
                            AND l.kind = 'llm_call' AND l.duplicate_of IS NULL
  ) WHERE rn = 1
)"""
_AGENT_ITEMS = f"""
SELECT COALESCE(e.agent_type, '(none)'), e.root, e.op_id, 'agent', 1, NULL, NULL, NULL, e.wall_ms * 1.0 FROM e
UNION ALL
SELECT CASE WHEN m.lr IS NULL THEN '(main)' ELSE COALESCE(m.at, '(none)') END, l.root_rowid, l.op_id, 'call',
       CASE WHEN m.lr IS NULL THEN 1 ELSE 0 END, l.tok_total * 1.0, l.cost_usd,
       CASE WHEN l.cost_usd IS NULL THEN l.rowid END, NULL
FROM ops l LEFT JOIN m ON m.lr = l.rowid
WHERE l.kind = 'llm_call' AND l.duplicate_of IS NULL AND l.root_rowid IN {_IDS}
"""
_SLASH_ITEMS = f"""
SELECT substr(replace(t.label, char(10), ' '), 1, instr(replace(t.label, char(10), ' ') || ' ', ' ') - 1),
       t.root_rowid, t.op_id, 'turn', 1, t.attr_tok_total * 1.0, t.attr_cost_usd,
       CASE WHEN t.attr_cost_lower_bound = 1 THEN t.rowid END, t.span_wall_ms * 1.0
FROM ops t
WHERE t.kind = 'turn' AND t.parent_rowid IN {_IDS} AND t.label IS NOT NULL
  AND (t.turn_origin = 'slash' OR t.turn_origin LIKE 'builtin:%')
"""


def _items_sql(by: str) -> tuple[str, str]:
    """`(ctes, items)`: the CTEs the items need (with a trailing comma) and the items query."""
    if by == "tool":
        key = (
            "COALESCE(tc.tool_name, '(none)')"  # FALLBACK-OK: interfaces.md CLI-22 (null name reads "(none)")
        )
        return _TC + ",\n", _TOOL_ITEMS.format(key=key, where="") + "UNION ALL" + _NO_TOOL_ITEMS
    if by == "command_key":
        return _TC + ",\n", _TOOL_ITEMS.format(key="tc.command_key", where="WHERE tc.command_key IS NOT NULL")
    if by == "command_class":
        return _TC + ",\n", _TOOL_ITEMS.format(
            key="tc.command_class", where="WHERE tc.command_class IS NOT NULL"
        )
    if by == "verb":
        return _TC + ",\n" + _VERB_CTE + ",\n", _VERB_ITEMS
    if by == "skill":
        return _SKILL_CTE + ",\n", _SKILL_ITEMS
    if by == "agent_type":
        return _AGENT_CTE + ",\n", _AGENT_ITEMS
    return "", _SLASH_ITEMS


_MEASURE_COL = {"tokens": "tok", "cost": "cost", "time": "dur", "count": "n"}

_GROUPS = """
WITH {ctes}items(key, root, ex, ex_type, nw, tok, cost, unp, dur) AS ({items}),
rk AS (
  SELECT key, dur, ROW_NUMBER() OVER (PARTITION BY key ORDER BY dur) AS rn,
         COUNT(*) OVER (PARTITION BY key) AS cn
  FROM items WHERE dur IS NOT NULL
), pc AS (
  SELECT key, MAX(CASE WHEN rn = (cn * 50 + 99) / 100 THEN dur END) AS p50,
         MAX(CASE WHEN rn = (cn * 90 + 99) / 100 THEN dur END) AS p90
  FROM rk GROUP BY key
), ag AS (
  SELECT key, SUM(nw) AS n, COUNT(DISTINCT root) AS ns, SUM(tok) AS tok, SUM(cost) AS cost,
         COUNT(DISTINCT unp) AS unp, SUM(dur) AS dur
  FROM items GROUP BY key
), ex AS (
  SELECT key, ex, ex_type, root FROM (
    SELECT key, ex, ex_type, root,
           ROW_NUMBER() OVER (PARTITION BY key ORDER BY {pick} DESC NULLS LAST, dur DESC NULLS LAST,
                              tok DESC NULLS LAST, ex) AS r
    FROM items WHERE nw > 0
  ) WHERE r = 1
)
SELECT ag.*, pc.p50, pc.p90, ex.ex, ex.ex_type, so.op_id AS ex_session
FROM ag JOIN ex ON ex.key = ag.key LEFT JOIN pc ON pc.key = ag.key JOIN ops so ON so.rowid = ex.root
ORDER BY ag.{order} DESC NULLS LAST, ag.key
"""
_TOTALS = f"""
SELECT COUNT(*) AS n, COUNT(tok_total) AS n_tok, SUM(tok_total) AS tok, COUNT(cost_usd) AS n_cost,
       SUM(cost_usd) AS cost
FROM ops WHERE kind = 'llm_call' AND duplicate_of IS NULL AND root_rowid IN {_IDS}
"""


def top_tasks(scope: Scope, by: str, measure: str = "tokens", n: int = 20, db: Db = None) -> wire.TaskGroups:
    """CLI-22. `by` is command_key, tool, verb, skill, agent_type or slash; `n` is 1 to 500."""
    if by not in BY:
        raise QueryError(f"by must be one of {list(BY)}, got {by!r}")
    if measure not in MEASURES:
        raise QueryError(f"measure must be one of {list(MEASURES)}, got {measure!r}")
    _check_n(n, MAX_TASKS_N)
    with _conn(db) as conn:
        roots = in_scope_roots(conn, scope)
        ids = {"ids": _json_ids(roots)}
        t = conn.execute(_TOTALS, ids).fetchone()
        wall = conn.execute(f"SELECT SUM(wall_ms) FROM sessions WHERE op_rowid IN {_IDS}", ids).fetchone()[0]
        scope_cost = common.money(t["cost"] if t["n"] else None, t["n_cost"] < t["n"], t["n"] - t["n_cost"])
        totals = wire.ScopeTotals(t["tok"] if t["n_tok"] else None, scope_cost, wall)
        ctes, items = _items_sql(by)
        col = _MEASURE_COL[measure]
        sql = _GROUPS.format(ctes=ctes, items=items, pick="nw" if col == "n" else col, order=col)
        rows = conn.execute(sql, ids).fetchall() if roots else []
        scope_total = {"tokens": totals.tok_total, "cost": scope_cost.cost_usd, "time": totals.dur_ms}.get(
            measure
        )

        groups = []
        for r in rows:
            value = {"tokens": r["tok"], "cost": r["cost"], "time": r["dur"], "count": None}[measure]
            share = value / scope_total if value is not None and scope_total else None
            groups.append(
                wire.TaskGroup(
                    key=r["key"],
                    n=r["n"],
                    n_sessions=r["ns"],
                    tok_total=r["tok"],
                    cost_usd=r["cost"],
                    cost_lower_bound=r["unp"] > 0,
                    n_unpriced=r["unp"],
                    dur_ms=r["dur"],
                    p50_ms=int(r["p50"]) if r["p50"] is not None else None,
                    p90_ms=int(r["p90"]) if r["p90"] is not None else None,
                    share=share,
                    example=wire.TaskExample(wire.RowRef(r["ex_type"], r["ex"]), r["ex_session"]),
                )
            )
        got_tok = sum(g.tok_total or 0.0 for g in groups)
        got_cost = sum(g.cost_usd or 0.0 for g in groups)
        unassigned = wire.Unassigned(
            tok_total=None if totals.tok_total is None else round(totals.tok_total - got_tok, 9),
            cost_usd=None if scope_cost.cost_usd is None else round(scope_cost.cost_usd - got_cost, 9),
        )
        return wire.TaskGroups(
            freshness=_fresh(conn),
            by=by,
            measure=measure,
            basis=BASIS[by],
            scope_totals=totals,
            unassigned=unassigned,
            total_groups=len(groups),
            groups=groups[:n],
        )
