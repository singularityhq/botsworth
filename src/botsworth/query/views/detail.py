"""CONTRACT: detail blade view (owner Q2c).

`detail(op)` returns one of four kinds (QRY-35, API-24) chosen by what the op is. Spec ids: QRY-35, QRY-36,
QRY-37; API-24; DET-13, DET-23, DET-24, DET-32, DET-34; D42.

GUARANTEES:
- Kind. An llm_call is `call`. A tool_call is `tool`, unless it caused engagements (a spawn or SendMessage
  call): its row is an agent row (DET-25, QRY-38a), so it is `agent`. An engagement that has a causing call is
  `agent`, for any engagement of the group (`focus_engagement` is the one asked for; `id` is the first). A
  main turn, harness included, is `message`. Anything else (a session, a child turn, a step of no row) ->
  QueryError; an engagement with no causing call (DM-45) -> NotFound.
- `position` = `{session, turn, display_id, row, depth}` (QRY-36). `session` is the session op_id, `turn` the
  op_id of the turn the op's cost is attributed to (`attr_turn_rowid`; null for a fork copy or unattributed
  agent work, API-24), `row` the RowRef the blade shows, `depth` the engagement nesting level (at most 3).
- `call`: the `CallRow` fields (Q2b `call_row`) plus the cost split and `context_tokens` from the op,
  `speed_tok_s` = `out_tok` / `dur_ms` when `dur_ms > 0` and `out_tok` is not null (basis = the call's
  `time_prov`, else null and `missing`), `turn_cost_usd` = the turn's `attr_cost_usd`. `sections` lists the
  call's own think, say and answer steps and the steps of its tool_calls, in step order (DET-13): `thinking`,
  `text`, or `tool_use` with `row` = the ToolRow or AgentRow it opens. `src` is the step's reference
  (`src_uri`), `snippet` its stored snippet (a tool_use section: its summary), `bytes` the length of a
  `B<start>+<len>` locator, else null. Building `sections` reads no source file.
- `tool`: the `ToolRow` fields (Q2b `tool_row`), `input_src` = the tool_call reference (read with
  `part=input`), `output_src` = the reference whose last line is the result (the tool_call's own when its
  range reaches the result line, else the step's when that names a later line; read with `part=output`),
  `neighbours` = the two rows before and after it in the list it sits in (the turn's rows, or the agent's).
- `agent`: the `AgentRow` of the cause's group, `turn_cost_usd`, `runs` with `ask_src` = the causing call's
  reference (read with `part=input`), `top_calls` = the 5 costliest included llm_calls as CallRows (priced
  first, ties by start), `other_calls` = `{n, cost_usd}` of the rest. The included calls are found by the same
  engagement walk as the row's cost, so top_calls + other_calls.cost_usd equal the row's cost.
- `message`: `turn_kind`, `turn_origin`, `label`, `display_id`, `text_src` (the turn reference, read with
  `part=text`), `text_bytes` = the turn's `result_bytes`.
EXPECTS: schema v7 filled by B3a/B3b; a connection with `row_factory = sqlite3.Row`.
FAILURE BEHAVIOR: see Kind. An owned llm_call or tool_call without times, or a walk that loops, raises
  AssertionError (as Q2b's builders).
DOES NOT: read source files, write, or build SQL in `http/` or `cli/`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import fields

from botsworth import pointer
from botsworth.query import Db, NotFound, QueryError, _conn, shapes, wire
from botsworth.query.outline import resolve_op
from botsworth.query.views import rows as R
from botsworth.query.views.session import MAX_DEPTH, engagement_depth
from botsworth.query.views.stretches import display_id
from botsworth.query.views.text import src_uri

TOP_CALLS = 5  # DET-34
NEIGHBOURS = 2  # QRY-35
_BYTES = re.compile(r"B\d+\+(\d+)")


def _fresh(conn: sqlite3.Connection) -> wire.Freshness:
    return wire.Freshness(**shapes.freshness(conn))


def _row(conn: sqlite3.Connection, rowid: int) -> sqlite3.Row:
    return conn.execute(f"{R.OP_SELECT} WHERE o.rowid = ?", (rowid,)).fetchone()


def _first_engagement(conn: sqlite3.Connection, cause_rowid: int) -> str | None:
    # FALLBACK-OK: interfaces.md QRY-35 (a tool_use section opens an agent row only for a causing call)
    r = conn.execute(
        "SELECT op_id FROM ops WHERE caused_by_rowid = ? AND kind = 'engagement' "
        "ORDER BY start_ms IS NULL, start_ms, op_id LIMIT 1",
        (cause_rowid,),
    ).fetchone()
    return r["op_id"] if r is not None else None


def _position(
    conn: sqlite3.Connection, op: sqlite3.Row, row: wire.RowRef, turn_rowid: int | None, depth: int
) -> wire.Position:
    session = conn.execute(
        "SELECT o.op_id FROM sessions s JOIN ops o ON o.rowid = s.op_rowid WHERE s.rowid = ?",
        (op["root_rowid"],),
    ).fetchone()[0]
    if turn_rowid is None:
        # FALLBACK-OK: interfaces.md API-24 (`Position.turn` is nullable: a copy has no turn)
        return wire.Position(session, None, None, row, depth)
    turn = conn.execute("SELECT * FROM ops WHERE rowid = ?", (turn_rowid,)).fetchone()
    return wire.Position(session, turn["op_id"], display_id(turn), row, depth)


def _turn_cost(conn: sqlite3.Connection, turn_rowid: int | None) -> float | None:
    if turn_rowid is None:
        # FALLBACK-OK: interfaces.md API-24 (`turn_cost_usd` is nullable: no turn, no turn cost)
        return None
    return conn.execute("SELECT attr_cost_usd FROM ops WHERE rowid = ?", (turn_rowid,)).fetchone()[0]


def _own(row: object, drop: tuple[str, ...]) -> dict[str, object]:
    return {f.name: getattr(row, f.name) for f in fields(row) if f.name not in drop}  # type: ignore[arg-type]


# ── call ─────────────────────────────────────────────────────────────────────


def _section(conn: sqlite3.Connection, step: sqlite3.Row) -> wire.CallSection:
    verb = step["verb"]
    part = "thinking" if verb == "think" else "text" if verb in ("say", "answer") else "tool_use"
    ref: wire.RowRef | None = None
    if part == "tool_use":
        tool = conn.execute("SELECT rowid, op_id FROM ops WHERE rowid = ?", (step["op_rowid"],)).fetchone()
        first = _first_engagement(conn, tool["rowid"])
        ref = wire.RowRef("agent", first) if first is not None else wire.RowRef("tool", tool["op_id"])
    m = _BYTES.match(step["src_loc"])
    return wire.CallSection(
        part=part,
        src=src_uri(conn, step),
        snippet=R._text(step["summary"] if part == "tool_use" else step["snippet"]),
        bytes=int(m.group(1)) if m and part != "tool_use" else None,
        row=ref,
    )


def _sections(conn: sqlite3.Connection, call: sqlite3.Row) -> list[wire.CallSection]:
    steps = conn.execute(
        """
        SELECT s.* FROM steps s
        WHERE s.op_rowid = :call
           OR s.op_rowid IN (SELECT rowid FROM ops
                             WHERE parent_rowid = :call AND kind = 'tool_call' AND duplicate_of IS NULL)
        ORDER BY s.turn_rowid, s.ordinal
        """,
        {"call": call["rowid"]},
    ).fetchall()
    return [_section(conn, s) for s in steps]


def _call(conn: sqlite3.Connection, o: sqlite3.Row) -> wire.CallDetail:
    depth = engagement_depth(conn, o)
    own = R.steps_by_op(conn, [o["rowid"]]).get(o["rowid"], [])
    row = R.call_row(o, own, min(depth, MAX_DEPTH))
    if o["dur_ms"] and o["dur_ms"] > 0 and o["out_tok"] is not None:
        speed, basis = o["out_tok"] / (o["dur_ms"] / 1000), row.time_prov
    else:
        # FALLBACK-OK: interfaces.md QRY-33 (speed is null with no model time or no output tokens)
        speed, basis = None, "missing"
    turn = o["attr_turn_rowid"]
    return wire.CallDetail(
        freshness=_fresh(conn),
        position=_position(conn, o, wire.RowRef("call", o["op_id"]), turn, row.depth),
        turn_cost_usd=_turn_cost(conn, turn),
        cost_in_usd=o["cost_in_usd"],
        cost_out_usd=o["cost_out_usd"],
        cost_cache_read_usd=o["cost_cache_read_usd"],
        cost_cache_write_usd=o["cost_cache_write_usd"],
        cost_basis=o["cost_basis"],
        context_tokens=o["context_tokens"],
        speed_tok_s=speed,
        speed_basis=basis,
        sections=_sections(conn, o),
        **_own(row, ("type",)),
    )


# ── tool ─────────────────────────────────────────────────────────────────────


def output_src(op_src: str, step_src: str | None) -> str:
    """The reference to read with `part=output` (see the CONTRACT): the one whose last line is the result."""
    mine = pointer.parse(op_src)
    if isinstance(mine.locator, pointer.Lines) and mine.locator.last > mine.locator.first:
        return op_src
    if step_src is not None:
        theirs = pointer.parse(step_src)
        if (
            theirs.relpath == mine.relpath
            and isinstance(mine.locator, pointer.Lines)
            and isinstance(theirs.locator, pointer.Lines)
            and theirs.locator.first > mine.locator.last
        ):
            return step_src
    return op_src


def _siblings(
    conn: sqlite3.Connection, o: sqlite3.Row, forest: R.Forest, builder: R.RowBuilder, turn_rowid: int
) -> tuple[list[R._Entry], int]:
    """The ordered entries of the list `o` sits in, and the depth its rows carry."""
    near = forest.containing(o["pre"])
    if near is None:
        t = conn.execute("SELECT op_id FROM ops WHERE rowid = ?", (turn_rowid,)).fetchone()
        return builder.turn_entries(turn_rowid, R.turn_gaps(conn, o["root_rowid"], t["op_id"])), 0
    causes = forest.chain_causes(near["rowid"])
    assert causes, f"tool_call {o['op_id']} sits under unattributed engagement {near['op_id']}"
    return builder.group_entries(causes[0]), forest.level(causes[0]) + 1


def _neighbours(conn: sqlite3.Connection, o: sqlite3.Row) -> wire.Neighbours:
    turn = o["attr_turn_rowid"]
    if turn is None:
        # FALLBACK-OK: interfaces.md API-24 (a copy or unattributed op sits in no row list: no neighbours)
        return wire.Neighbours([], [])
    forest = R.Forest(conn, o["root_rowid"])
    builder = R.RowBuilder(conn, o["root_rowid"], forest, R.scan_turn(conn, forest, turn))
    entries, depth = _siblings(conn, o, forest, builder, turn)
    at = next((i for i, e in enumerate(entries) if e.row["rowid"] == o["rowid"] and e.kind != "gap"), None)
    if at is None:
        # FALLBACK-OK: interfaces.md API-24 (a duplicate is in no row list: no neighbours)
        return wire.Neighbours([], [])
    return wire.Neighbours(
        builder.build(entries[max(0, at - NEIGHBOURS) : at], depth),
        builder.build(entries[at + 1 : at + 1 + NEIGHBOURS], depth),
    )


def _tool(conn: sqlite3.Connection, o: sqlite3.Row) -> wire.ToolDetail:
    sel = _row(conn, o["rowid"])
    steps = R.steps_by_op(conn, [o["rowid"]]).get(o["rowid"], [])
    via = None
    if steps and steps[0]["skill_load"]:
        # FALLBACK-OK: interfaces.md API-22 (`skill_via` is nullable)
        via = R.skill_via_by_step(conn, o["root_rowid"]).get(R.step_id(steps[0]))
    row = R.tool_row(sel, steps, min(engagement_depth(conn, o), MAX_DEPTH), via)
    return wire.ToolDetail(
        freshness=_fresh(conn),
        position=_position(conn, o, wire.RowRef("tool", o["op_id"]), o["attr_turn_rowid"], row.depth),
        input_src=src_uri(conn, o),
        output_src=output_src(src_uri(conn, o), src_uri(conn, steps[0]) if steps else None),
        neighbours=_neighbours(conn, o),
        **_own(row, ("type",)),
    )


# ── agent ────────────────────────────────────────────────────────────────────


def _included_calls(
    conn: sqlite3.Connection, forest: R.Forest, turn_rowid: int, cause_rowid: int
) -> list[sqlite3.Row]:
    """The owned llm_calls the agent row of `cause_rowid` counts: the walk of `scan_turn`, per call."""
    out = []
    for leaf in conn.execute(
        "SELECT * FROM ops WHERE attr_turn_rowid = ? AND kind = 'llm_call' AND duplicate_of IS NULL",
        (turn_rowid,),
    ):
        near = forest.containing(leaf["pre"])
        if near is not None and cause_rowid in forest.chain_causes(near["rowid"]):
            out.append(leaf)
    return out


def _agent(conn: sqlite3.Connection, o: sqlite3.Row) -> wire.AgentDetail:
    cause_rowid = o["caused_by_rowid"] if o["kind"] == "engagement" else o["rowid"]
    if cause_rowid is None:
        raise NotFound(f"engagement {o['op_id']!r} has no causing call, so it has no agent row (DM-45)")
    cause = _row(conn, cause_rowid)
    turn = cause["attr_turn_rowid"]
    if turn is None:
        raise NotFound(f"agent row of {o['op_id']!r} belongs to no turn (agent work with no causing turn)")
    forest = R.Forest(conn, o["root_rowid"])
    builder = R.RowBuilder(conn, o["root_rowid"], forest, R.scan_turn(conn, forest, turn))
    level = forest.level(cause_rowid)
    entry = R._Entry((cause["start_ms"], 2, cause["pre"]), "agent", cause)
    row = builder.build([entry], level)[0]
    assert isinstance(row, wire.AgentRow)
    ask = src_uri(conn, cause)
    calls = sorted(
        _included_calls(conn, forest, turn, cause_rowid),
        key=lambda c: (c["cost_usd"] is None, -(c["cost_usd"] or 0.0), c["start_ms"], c["op_id"]),
    )
    top, rest = calls[:TOP_CALLS], calls[TOP_CALLS:]
    steps = R.steps_by_op(conn, [c["rowid"] for c in top])
    top_rows = [
        R.call_row(c, steps.get(c["rowid"], []), min(engagement_depth(conn, c), MAX_DEPTH)) for c in top
    ]
    other_cost = sum((c["cost_usd"] for c in rest if c["cost_usd"] is not None), 0.0)
    return wire.AgentDetail(
        freshness=_fresh(conn),
        position=_position(conn, o, wire.RowRef("agent", row.id), turn, row.depth),
        turn_cost_usd=_turn_cost(conn, turn),
        focus_engagement=o["op_id"] if o["kind"] == "engagement" else row.id,
        runs=[
            wire.AgentDetailRun(r.engagement, r.agent_name, r.start_ms, r.end_ms, r.wall_ms, ask)
            for r in row.runs
        ],
        top_calls=top_rows,
        other_calls=wire.OtherCalls(len(rest), other_cost if row.n_llm_calls else None),
        **_own(row, ("type", "runs")),
    )


# ── message ──────────────────────────────────────────────────────────────────


def _message(conn: sqlite3.Connection, o: sqlite3.Row) -> wire.MessageDetail:
    sel = _row(conn, o["rowid"])
    return wire.MessageDetail(
        freshness=_fresh(conn),
        position=_position(conn, o, wire.RowRef("turn", o["op_id"]), o["rowid"], 0),
        turn=o["op_id"],
        display_id=display_id(o),
        turn_kind=o["turn_kind"],
        turn_origin=o["turn_origin"],
        label=R._text(o["label"]),  # FALLBACK-OK: interfaces.md API-24 (`label` is a string; see R._text)
        text_src=src_uri(conn, sel),
        text_bytes=o["result_bytes"],
    )


# ── entry point ──────────────────────────────────────────────────────────────


def _is_main_turn(conn: sqlite3.Connection, o: sqlite3.Row) -> bool:
    if o["parent_rowid"] is None:
        return False
    return (
        conn.execute("SELECT kind FROM ops WHERE rowid = ?", (o["parent_rowid"],)).fetchone()[0] == "session"
    )


def detail(
    op: str, db: Db = None
) -> wire.CallDetail | wire.ToolDetail | wire.AgentDetail | wire.MessageDetail:
    """QRY-35, QRY-36; API-24. The result's `kind` is `call`, `tool`, `agent` or `message`."""
    with _conn(db) as conn:
        o = resolve_op(conn, op)
        kind = o["kind"]
        if kind == "llm_call":
            return _call(conn, o)
        if kind == "tool_call":
            caused = conn.execute(
                "SELECT 1 FROM ops WHERE caused_by_rowid = ? AND kind = 'engagement' LIMIT 1", (o["rowid"],)
            ).fetchone()
            return _agent(conn, o) if caused is not None else _tool(conn, o)
        if kind == "engagement":
            return _agent(conn, o)
        if kind == "turn" and _is_main_turn(conn, o):
            return _message(conn, o)
        raise QueryError(f"{op!r} is not a call, tool, agent or main turn ({kind})")
