"""CONTRACT: turn blade views (owner Q2b): turn rows, agent rows, histogram.

GUARANTEES:
- `turn_rows(turn, from_ms, to_ms)` (QRY-31; API-22): the main turn's depth-0 rows as they ran
  (`rows.py`): one `CallRow` per owned llm_call parented by the turn, one `ToolRow` per tool_call of
  those calls that caused no engagement, one `AgentRow` per tool_call that caused engagements, and
  the `GapRow`s of the 60 s or longer stretches that SES-37 attaches to the turn. Σ
  `CallRow.cost_usd` + Σ `AgentRow.cost_usd` = the turn's `attr_cost_usd` (both sides skip nulls).
  `turn` holds the `TurnRow` fields of `session.turn_row`, then `cost` (`main` = main-thread calls,
  `agents` = the rest), `models_full` over the calls attributed to the turn, `text_src`,
  `text_bytes` (`ops.result_bytes` of the turn op; null when the store holds no size) and
  `n_turns_in_session` (numbered main turns). With `from_ms`/`to_ms` (closed range), `range` holds
  the clock rule (SES-13): the calls that start in the range, main and agents, the DM-50 time split
  clipped to the range, and per agent row its in-range cost, tokens and call counts. Without them
  `range` is null.
- `agent_rows(engagement, offset, limit)` (QRY-32; API-32): the direct rows of the agent row that
  holds the engagement (any of its engagements, or its causing tool_call): the calls and tools run
  by its engagements and the agent rows they started, as they ran, at depth = the row's nesting
  level + 1 (capped at 3). Rows of a nested agent are fetched by asking for that agent's row.
  `total` counts the direct rows (= `AgentRow.n_rows`). An agent row holds no gap rows: stretches
  belong to the session's main thread (SES-37).
- `histogram(scope_op, bins, from_ms, to_ms)` (QRY-32a; API-31): `bins` equal bins (default 600 for
  a session, 120 for a turn) of `bin_ms` = ceil(span / bins) (at least 1) from the range start. A
  call counts in the bin that holds its `start_ms` (a start at the range end counts in the last
  bin). Session scope: every owned llm_call of the session; turn scope: the calls attributed to the
  turn (DM-45), agents included. Default range: session `[first_ms, last_ms]`; turn `[start_ms,
  max(end_ms, latest agent run end)]`, with `tail_from_ms` = `end_ms` when an agent run ends later.
  Σ `cost_usd` over the default range = the session's cost or the turn's `attr_cost_usd`;
  `main_cost_usd` counts owned calls with `agent_id IS NULL` (D47). `state_ms` is the DM-50 state
  time inside the bin (and the range), rebuilt from the session's leaves and container segments;
  over a session's whole range the four states equal the stored `model_ms`..`none_ms`. `stretches`
  are the session's stretches of 60 s or more that overlap the range.
EXPECTS: schema v7 filled by B3a/B3b. A turn id is an op_id or a display id (QRY-2); a session key
  is any key QRY-6 accepts.
FAILURE BEHAVIOR: unknown id -> NotFound; a turn id that is not a main turn, `from_ms` without
  `to_ms`, `to_ms` before `from_ms`, `bins` outside 1-1000, `limit` outside 1-2000 or a negative
  `offset` -> QueryError; a turn or session without a start time has no histogram -> QueryError; an
  engagement with no causing call, or whose cause has no turn (unattributed agent work, DM-45) ->
  NotFound.
DOES NOT: write, read source files, or hold SQL outside this module and `rows.py`.
"""

from __future__ import annotations

import json
import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import fields

from botsworth.query import Db, NotFound, QueryError, _conn, shapes, wire
from botsworth.query.outline import resolve_op
from botsworth.query.views import rows as R
from botsworth.query.views.common import lookup_session, money
from botsworth.query.views.session import model_lines, turn_row
from botsworth.query.views.stretches import display_id, stretches_for
from botsworth.store import timesplit
from botsworth.store.intervals import merge

MAX_BINS = 1000
DEFAULT_BINS = {"session": 600, "turn": 120}
MAX_LIMIT = 2000
STATES = ("model", "tool", "agents", "none")
_LEAVES = "('llm_call', 'tool_call')"

# ── lookups ──────────────────────────────────────────────────────────────────


def _op(conn: sqlite3.Connection, ident: str) -> sqlite3.Row:
    """The `ops` row an id names (op_id, step id or display id, QRY-2)."""
    resolved = resolve_op(conn, ident)
    return conn.execute("SELECT * FROM ops WHERE rowid = ?", (resolved["rowid"],)).fetchone()


def _is_main_turn(conn: sqlite3.Connection, op: sqlite3.Row) -> bool:
    if op["kind"] != "turn" or op["parent_rowid"] is None:
        return False
    return (
        conn.execute("SELECT kind FROM ops WHERE rowid = ?", (op["parent_rowid"],)).fetchone()[0] == "session"
    )


def _main_turn(conn: sqlite3.Connection, ident: str) -> sqlite3.Row:
    op = _op(conn, ident)
    if not _is_main_turn(conn, op):
        raise QueryError(f"{ident!r} is not a main turn ({op['kind']})")
    return op


def _fresh(conn: sqlite3.Connection) -> wire.Freshness:
    return wire.Freshness(**shapes.freshness(conn))


# ── DM-50 states at query time ───────────────────────────────────────────────


def state_runs(conn: sqlite3.Connection, root_rowid: int) -> list[tuple[int, int, str]]:
    """The session's elementary state runs inside its wall (DM-50), as the fold found them (DM-51).

    The wall is the union of the containers' segments (a container without `segments` counts as its own
    `[start_ms, end_ms]`) and the owned, non-inverted leaf intervals, the rule of `store.load`.
    """
    wall: list[tuple[int, int]] = []
    llm: list[tuple[int, int]] = []
    tool: list[tuple[int, int]] = []
    agents: list[tuple[int, int]] = []
    for r in conn.execute(
        f"""
        SELECT kind, start_ms, end_ms, agent_id, segments, n_inverted FROM ops
        WHERE root_rowid = ? AND (kind NOT IN {_LEAVES} OR duplicate_of IS NULL)
        """,
        (root_rowid,),
    ):
        if r["kind"] not in ("llm_call", "tool_call"):
            if r["segments"]:
                wall += [(a, b) for a, b in json.loads(r["segments"]) if b >= a]
            elif r["start_ms"] is not None and r["end_ms"] is not None and r["end_ms"] >= r["start_ms"]:
                wall.append((r["start_ms"], r["end_ms"]))
            continue
        if r["n_inverted"] or r["start_ms"] is None or r["end_ms"] is None:
            continue
        iv = (r["start_ms"], r["end_ms"])
        wall.append(iv)
        if r["agent_id"] is not None:
            agents.append(iv)
        elif r["kind"] == "llm_call":
            llm.append(iv)
        else:
            tool.append(iv)
    # `_runs` is the one place where the four states are decided (store.timesplit); the fold's split() uses it
    return timesplit._runs(merge(wall), merge(llm), merge(tool), merge(agents))


def _split_bins(runs: Sequence[tuple[int, int, str]], edges: Sequence[int]) -> list[wire.TimeSplit]:
    """State time inside each interval `[edges[i], edges[i + 1])`."""
    starts = [a for a, _, _ in runs]
    ends = [b for _, b, _ in runs]
    out = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        acc = dict.fromkeys(STATES, 0)
        i = max(bisect_right(ends, lo), 0)  # the first run that ends after lo
        while i < len(runs) and starts[i] < hi:
            a, b, state = runs[i]
            acc[state] += min(b, hi) - max(a, lo)
            i += 1
        out.append(wire.TimeSplit(acc["model"], acc["tool"], acc["agents"], acc["none"]))
    return out


# ── turn_rows ────────────────────────────────────────────────────────────────


def _range_args(from_ms: int | None, to_ms: int | None) -> tuple[int, int] | None:
    if (from_ms is None) != (to_ms is None):
        raise QueryError("from_ms and to_ms must be given together")
    if from_ms is None:
        return None
    assert to_ms is not None
    if to_ms < from_ms:
        raise QueryError(f"to_ms {to_ms} is before from_ms {from_ms}")
    return from_ms, to_ms


def _header(conn: sqlite3.Connection, t: sqlite3.Row, scan: R.TurnScan) -> wire.TurnHeader:
    base = turn_row(conn, t)
    has_calls = base.n_llm_calls > 0
    cost = wire.TurnCost(
        base.cost_usd,
        base.cost_lower_bound,
        base.n_unpriced,
        main=scan.main.cost if has_calls else None,
        agents=scan.agents.cost if has_calls else None,
    )
    src = conn.execute(f"{R.OP_SELECT} WHERE o.rowid = ?", (t["rowid"],)).fetchone()["src"]
    n_turns = conn.execute(
        "SELECT count(*) FROM ops WHERE parent_rowid = ? AND kind = 'turn' AND turn_kind <> 'harness'",
        (t["parent_rowid"],),
    ).fetchone()[0]
    return wire.TurnHeader(
        **{f.name: getattr(base, f.name) for f in fields(base)},
        cost=cost,
        models_full=model_lines(conn, "o.attr_turn_rowid = ?", (t["rowid"],)),
        text_src=src,
        text_bytes=t["result_bytes"],
        n_turns_in_session=n_turns,
    )


def _range(
    conn: sqlite3.Connection,
    forest: R.Forest,
    scan: R.TurnScan,
    entries: Sequence[R._Entry],
    rng: tuple[int, int],
    root: int,
) -> wire.TurnRange:
    main, agents = scan.main, scan.agents
    n = main.rng_n + agents.rng_n
    priced = main.rng_priced + agents.rng_priced
    total = money(main.rng_cost + agents.rng_cost if n else None, priced < n, n - priced)
    split = _split_bins(state_runs(conn, root), [rng[0], rng[1]])[0]
    per_agent = []
    for e in entries:
        if e.kind != "agent":
            continue
        agg = scan.by_cause[e.row["rowid"]]
        per_agent.append(
            wire.RangeAgent(
                row=forest.groups[e.row["rowid"]][0]["op_id"],
                cost_usd=agg.rng_money.cost_usd,
                tok_total=agg.rng_tok if agg.rng_n_tok else None,
                n_calls_in_range=agg.rng_n,
                n_calls=agg.n_llm,
            )
        )
    return wire.TurnRange(
        rng[0],
        rng[1],
        total,
        cost_main=main.rng_cost if n else None,
        cost_agents=agents.rng_cost if n else None,
        time_split=split,
        agents=per_agent,
    )


def turn_rows(
    turn: str, from_ms: int | None = None, to_ms: int | None = None, db: Db = None
) -> wire.TurnRows:
    """QRY-31; API-22. `from_ms` and `to_ms` are given together or not at all."""
    rng = _range_args(from_ms, to_ms)
    with _conn(db) as conn:
        t = _main_turn(conn, turn)
        root = t["root_rowid"]
        forest = R.Forest(conn, root)
        scan = R.scan_turn(conn, forest, t["rowid"], rng)
        builder = R.RowBuilder(conn, root, forest, scan)
        entries = builder.turn_entries(t["rowid"], R.turn_gaps(conn, root, t["op_id"]))
        return wire.TurnRows(
            freshness=_fresh(conn),
            turn=_header(conn, t, scan),
            rows=builder.build(entries, 0),
            range=_range(conn, forest, scan, entries, rng, root) if rng is not None else None,
        )


# ── agent_rows ───────────────────────────────────────────────────────────────


def agent_rows(engagement: str, offset: int = 0, limit: int = 500, db: Db = None) -> wire.AgentRowPage:
    """QRY-32; API-32. `limit` is at most 2000."""
    if offset < 0:
        raise QueryError(f"offset {offset} is negative")
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit {limit} is outside 1-{MAX_LIMIT}")
    with _conn(db) as conn:
        op = _op(conn, engagement)
        forest = R.Forest(conn, op["root_rowid"])
        if op["kind"] == "engagement":
            cause = op["caused_by_rowid"]
            if cause is None:
                raise NotFound(
                    f"engagement {engagement!r} has no causing call, so it has no agent row (DM-45)"
                )
        elif op["kind"] == "tool_call" and op["rowid"] in forest.groups:
            cause = op["rowid"]
        else:
            raise QueryError(f"{engagement!r} is not an engagement or an agent row ({op['kind']})")
        cause_op = conn.execute("SELECT attr_turn_rowid FROM ops WHERE rowid = ?", (cause,)).fetchone()
        if cause_op["attr_turn_rowid"] is None:
            raise NotFound(
                f"agent row of {engagement!r} belongs to no turn (agent work with no causing turn)"
            )
        scan = R.scan_turn(conn, forest, cause_op["attr_turn_rowid"])
        builder = R.RowBuilder(conn, op["root_rowid"], forest, scan)
        entries = builder.group_entries(cause)
        page = entries[offset : offset + limit]
        return wire.AgentRowPage(
            freshness=_fresh(conn),
            total=len(entries),
            offset=offset,
            limit=limit,
            rows=builder.build(page, forest.level(cause) + 1),
        )


# ── histogram ────────────────────────────────────────────────────────────────


def _scope(conn: sqlite3.Connection, scope_op: str) -> tuple[str, sqlite3.Row]:
    """`('turn', turn op)` or `('session', sessions row)`."""
    try:
        op = _op(conn, scope_op)
    except QueryError:
        # FALLBACK-OK: interfaces.md QRY-32a (`scope_op` is a main turn or a session; a bare session key or
        # prefix is not an op id, so QRY-6 resolves it)
        return "session", lookup_session(conn, scope_op)
    if op["kind"] == "session":
        return "session", lookup_session(conn, op["op_id"])
    if not _is_main_turn(conn, op):
        raise QueryError(f"{scope_op!r} is not a session or a main turn ({op['kind']})")
    return "turn", op


def _skill_marks(
    conn: sqlite3.Connection, root: int, turn_rowid: int | None, lo: int, hi: int
) -> list[wire.SkillMark]:
    """SES-29 pins: steps with `skill_load` (a tool or call row), and slash skill events with no such step in
    their turn (the turn row, DM-30)."""
    scope = "" if turn_rowid is None else "AND o.attr_turn_rowid = ?"
    extra = [] if turn_rowid is None else [turn_rowid]
    marks: list[tuple[int, str, wire.SkillMark]] = []
    for s in conn.execute(
        f"""
        SELECT s.skill_load AS skill, s.start_ms AS step_start, o.op_id AS op_id, o.kind AS kind,
               o.start_ms AS op_start
        FROM steps s JOIN ops o ON o.rowid = s.op_rowid
        WHERE s.root_rowid = ? AND s.skill_load IS NOT NULL AND o.duplicate_of IS NULL {scope}
        """,
        [root, *extra],
    ):
        # FALLBACK-OK: data-model.md DM-30 (the step's op anchors a skill load when the step has no start)
        start = s["step_start"] if s["step_start"] is not None else s["op_start"]
        if start is not None and lo <= start <= hi:
            ref = wire.RowRef("call" if s["kind"] == "llm_call" else "tool", s["op_id"])
            marks.append((start, s["op_id"], wire.SkillMark(ref, start, s["skill"])))
    turn_scope = "" if turn_rowid is None else "AND t.rowid = ?"
    for e in conn.execute(
        f"""
        SELECT t.op_id AS op_id, COALESCE(ev.ts_ms, t.start_ms) AS start,
               json_extract(ev.fields, '$.skill') AS skill
        FROM events ev JOIN ops t ON t.op_id = ev.op_id
        WHERE ev.root_rowid = ? AND ev.kind = 'skill_invocation' AND ev.copied = 0
          AND json_extract(ev.fields, '$.via') = 'slash' AND t.kind = 'turn' {turn_scope}
          AND NOT EXISTS (SELECT 1 FROM steps s WHERE s.turn_rowid = t.rowid AND s.skill_load IS NOT NULL)
        """,
        [root, *extra],
    ):
        if e["start"] is not None and lo <= e["start"] <= hi:
            marks.append(
                (
                    e["start"],
                    e["op_id"],
                    wire.SkillMark(wire.RowRef("turn", e["op_id"]), e["start"], e["skill"]),
                )
            )
    return [m for _, _, m in sorted(marks, key=lambda x: (x[0], x[1]))]


def _turn_starts(
    conn: sqlite3.Connection, session_op_rowid: int, lo: int, hi: int
) -> list[wire.TurnStartMark]:
    turns = conn.execute(
        "SELECT * FROM ops WHERE parent_rowid = ? AND kind = 'turn' AND start_ms IS NOT NULL "
        "ORDER BY start_ms, COALESCE(seq, pre)",
        (session_op_rowid,),
    )
    return [
        wire.TurnStartMark(t["op_id"], display_id(t), t["start_ms"])
        for t in turns
        if lo <= t["start_ms"] <= hi
    ]


def histogram(
    scope_op: str,
    bins: int | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    db: Db = None,
) -> wire.Histogram:
    """QRY-32a; API-31. `scope_op` is a session key or a main turn; `bins` defaults to 600 / 120."""
    rng = _range_args(from_ms, to_ms)
    if bins is not None and not 1 <= bins <= MAX_BINS:
        raise QueryError(f"bins {bins} is outside 1-{MAX_BINS}")
    with _conn(db) as conn:
        scope, op = _scope(conn, scope_op)
        n = bins if bins is not None else DEFAULT_BINS[scope]
        tail_from: int | None = None
        if scope == "session":
            root, lo, hi = op["rowid"], op["first_ms"], op["last_ms"]
            if lo is None or hi is None:
                raise QueryError(f"session {op['op_id']} has no records, so it has no histogram")
            where, args = "root_rowid = ?", [root]
        else:
            root, lo, hi = op["root_rowid"], op["start_ms"], op["end_ms"]
            if lo is None or hi is None:
                raise QueryError(f"turn {op['op_id']} has no start time, so it has no histogram")
            latest = max((r.end_ms for r in turn_row(conn, op).agent_runs), default=hi)
            if latest > hi:
                tail_from, hi = hi, latest
            where, args = "attr_turn_rowid = ?", [op["rowid"]]
        if rng is not None:
            lo, hi = rng
        bin_ms = max(1, -(-(hi - lo) // n))
        cost = [0.0] * n
        main = [0.0] * n
        tok = [0] * n
        by_model: list[dict[str, list[float]]] = [{} for _ in range(n)]
        for c in conn.execute(
            f"""
            SELECT start_ms, cost_usd, tok_total, model, agent_id FROM ops
            WHERE {where} AND kind = 'llm_call' AND duplicate_of IS NULL
            """,
            args,
        ):
            # the range is not in the SQL: a start_ms bound would make the planner pick ops_kind_start (§4)
            assert c["start_ms"] is not None, "an owned llm_call has no start"
            if not lo <= c["start_ms"] <= hi:
                continue
            i = min((c["start_ms"] - lo) // bin_ms, n - 1)
            usd, tokens = c["cost_usd"] or 0.0, c["tok_total"] or 0
            cost[i] += usd
            tok[i] += tokens
            if c["agent_id"] is None:
                main[i] += usd
            if scope == "turn":
                # FALLBACK-OK: interfaces.md QRY-32a keys `by_model` by model; a call with no model is keyed
                # `unknown`, as session.model_lines does (spec gap, gates/Q2a.md finding 4b, gates/Q2b.md)
                slot = by_model[i].setdefault(c["model"] if c["model"] is not None else "unknown", [0.0, 0])
                slot[0] += usd
                slot[1] += tokens
        edges = [min(lo + i * bin_ms, hi) for i in range(n + 1)]
        states = _split_bins(state_runs(conn, root), edges)
        if scope == "turn":
            bin_list: list[wire.HistBin | wire.HistBinByModel] = [
                wire.HistBinByModel(cost[i], main[i], tok[i], states[i], by_model[i]) for i in range(n)
            ]
            marks: wire.HistMarks | wire.SessionHistMarks = wire.HistMarks(
                _skill_marks(conn, root, op["rowid"], lo, hi)
            )
        else:
            bin_list = [wire.HistBin(cost[i], main[i], tok[i], states[i]) for i in range(n)]
            marks = wire.SessionHistMarks(
                _turn_starts(conn, op["op_rowid"], lo, hi), _skill_marks(conn, root, None, lo, hi)
            )
        return wire.Histogram(
            freshness=_fresh(conn),
            start_ms=lo,
            bin_ms=bin_ms,
            tail_from_ms=tail_from,
            bins=bin_list,
            marks=marks,
            stretches=stretches_for(conn, root, lo, hi),
        )
