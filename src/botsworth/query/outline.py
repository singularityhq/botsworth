"""CONTRACT: display ids, id resolution and outline rows (design §6.1a, R-outline).

Display id grammar (anchored at one session; 1-based):
  T<t>[.<s>]            main turn with `ops.display_ix` = t (QRY-2: harness turns have none); step s of that
                        turn (ordinal s-1)
  E<k>[/<t>[.<s>]]      k-th engagement parented directly by the session (link basis none)
  <path>.<s>/<t>[.<s>]  turn t among the turns of every engagement caused by that step's
                        tool_call, ordered (engagement seq, turn seq); repeats per nesting
A display id is qualified by its session as `<anchor>:<display>` where anchor
is a session_id or any op_id of that session; a bare display id needs an
anchor from the call (outline/steps scope).

GUARANTEES:
- `resolve(conn, ident, anchor)` accepts an op_id, a step_id
  (`<turn_id>#<ordinal>`) or a display id and returns ("op", row) or
  ("step", row); `display_of(conn, op_or_step)` is its inverse for turns,
  steps and session-level engagements: resolve(display_of(x)) == x.
- When several steps share one spawning tool_call (batched spawns), the
  lowest-ordinal step names the path; every such step resolves to the same turns.
- `outline(conn, scope, depth)` returns `{scope, rows}`; rows carry both the
  display id and the stable id (`OUTLINE_ROW_KEYS`); turn rows carry the
  turn's inclusive cost and wall time, step rows the step's dur and outcome;
  `depth` counts levels of engagement nesting (1 = this transcript only).
- `resolve_src` serves a duplicate's text through its owner's pointer (§4.9).
EXPECTS: steps and ops of one session in the store; every non-harness main turn has a `display_ix`
  (B3a's fold, DM-24). A main turn without one that is not a harness turn raises AssertionError.
FAILURE BEHAVIOR: unknown id, unknown anchor or a position past the end ->
  `NotFound`; bad grammar or a bare display id without an anchor -> `QueryError`.
DOES NOT: read source files.
KNOWN LIMITATIONS: an engagement whose causing tool_call has no step gets no
  display id (`display_id` null); it stays reachable by op_id.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from botsworth.query import NotFound, QueryError
from botsworth.query.shapes import freshness

_SEG = re.compile(r"^(\d+)(?:\.(\d+))?$")
_HEAD = re.compile(r"^(?:T(\d+)(?:\.(\d+))?|E(\d+))$")


def is_display(text: str) -> bool:
    head, *rest = text.split("/")
    return bool(_HEAD.match(head)) and all(_SEG.match(s) for s in rest)


def _op(conn, op_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ops_v WHERE op_id = ?", (op_id,)).fetchone()


def _by_rowid(conn, rowid: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM ops_v WHERE rowid = ?", (rowid,)).fetchone()


def _session_turns(conn, session_rowid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ops_v WHERE parent_rowid = ? AND kind = 'turn' "
        "ORDER BY COALESCE(seq, pre), start_ms, op_id",
        (session_rowid,),
    ).fetchall()


def _session_engagements(conn, session_rowid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ops_v WHERE parent_rowid = ? AND kind = 'engagement' "
        "ORDER BY COALESCE(seq, pre), start_ms, op_id",
        (session_rowid,),
    ).fetchall()


def _engagement_turns(conn, eng_rowid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ops_v WHERE parent_rowid = ? AND kind = 'turn' "
        "ORDER BY COALESCE(seq, pre), start_ms, op_id",
        (eng_rowid,),
    ).fetchall()


def _caused_turns(conn, tool_call_id: str) -> list[sqlite3.Row]:
    """Turns of every engagement whose parent is this tool_call."""
    tc = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (tool_call_id,)).fetchone()
    if tc is None:
        return []
    # CROSS JOIN fixes e as the outer loop and +t.kind keeps the planner off ops_kind_cost:
    # otherwise it scans every turn in the store (40 s for one depth-2 outline on the real corpus).
    return conn.execute(
        "SELECT t.* FROM ops e CROSS JOIN ops_v t ON t.parent_rowid = e.rowid AND +t.kind = 'turn' "
        "WHERE e.parent_rowid = ? AND e.kind = 'engagement' "
        "ORDER BY COALESCE(e.seq, e.pre), e.start_ms, e.op_id, COALESCE(t.seq, t.pre), t.start_ms, t.op_id",
        (tc[0],),
    ).fetchall()


def _step(conn, turn_id: str, ordinal: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM steps_v WHERE turn_id = ? AND ordinal = ?", (turn_id, ordinal)
    ).fetchone()


def _main_turn_by_ix(conn, session_rowid: int, n: int, text: str) -> sqlite3.Row:
    """QRY-2: the main turn whose `display_ix` is n."""
    row = conn.execute(
        "SELECT * FROM ops_v WHERE parent_rowid = ? AND kind = 'turn' AND display_ix = ?",
        (session_rowid, n),
    ).fetchone()
    if row is None:
        top = conn.execute(
            "SELECT max(display_ix) FROM ops WHERE parent_rowid = ? AND kind = 'turn'", (session_rowid,)
        ).fetchone()[0]
        raise NotFound(f"{text}: turn {n} out of range (1..{top or 0})")
    return row


def _pick(items: list, n: int, what: str, text: str):
    if not 1 <= n <= len(items):
        raise NotFound(f"{text}: {what} {n} out of range (1..{len(items)})")
    return items[n - 1]


# ── display -> stable ───────────────────────────────────────────────────────


def _resolve_display(conn, text: str, session_rowid: int) -> tuple[str, sqlite3.Row]:
    head, *rest = text.split("/")
    m = _HEAD.match(head)
    if m is None or not all(_SEG.match(s) for s in rest):
        raise QueryError(f"bad display id {text!r}")
    if m.group(3) is not None:
        eng = _pick(_session_engagements(conn, session_rowid), int(m.group(3)), "engagement", text)
        if not rest:
            return "op", eng
        turns = _engagement_turns(conn, eng["rowid"])
        segs = [_SEG.match(s).groups() for s in rest]
    else:
        turns = None  # the first segment is a main turn: found by display_ix (QRY-2)
        segs = [(m.group(1), m.group(2))] + [_SEG.match(s).groups() for s in rest]
    for i, (t, s) in enumerate(segs):
        if i == 0 and turns is None:
            turn = _main_turn_by_ix(conn, session_rowid, int(t), text)
        else:
            turn = _pick(turns, int(t), "turn", text)
        if s is None:
            if i != len(segs) - 1:
                raise QueryError(f"{text}: a nested segment must follow a step (T<t>.<s>)")
            return "op", turn
        step = _step(conn, turn["op_id"], int(s) - 1)
        if step is None:
            raise NotFound(f"{text}: turn {t} has no step {s}")
        if i == len(segs) - 1:
            return "step", step
        turns = _caused_turns(conn, step["op_id"])
    raise AssertionError("unreachable")


def _anchor_session(conn, anchor: str) -> int:
    row = _op(conn, anchor)
    if row is None:
        row = conn.execute(
            "SELECT * FROM ops_v WHERE kind = 'session' AND session_id = ?", (anchor,)
        ).fetchone()
    if row is None:
        raise NotFound(f"unknown display-id anchor {anchor!r}")
    return row["root_rowid"]


def resolve(conn, ident: str, anchor_session: int | None = None) -> tuple[str, sqlite3.Row]:
    if not isinstance(ident, str) or not ident:
        raise QueryError("id must be a nonempty string")
    row = _op(conn, ident)
    if row is not None:
        return "op", row
    if "#" in ident:
        turn_id, _, ordinal = ident.rpartition("#")
        if ordinal.isdigit():
            step = _step(conn, turn_id, int(ordinal))
            if step is not None:
                return "step", step
    anchor, sep, display = ident.rpartition(":")
    if sep and is_display(display):
        return _resolve_display(conn, display, _anchor_session(conn, anchor))
    if is_display(ident):
        if anchor_session is None:
            only = conn.execute("SELECT rowid FROM ops WHERE kind = 'session' LIMIT 2").fetchall()
            if len(only) != 1:
                raise QueryError(f"display id {ident!r} needs a session: use <session_id>:{ident}")
            anchor_session = only[0][0]
        return _resolve_display(conn, ident, anchor_session)
    raise NotFound(f"unknown id {ident!r}")


def resolve_op(conn, ident: str, anchor_session: int | None = None) -> sqlite3.Row:
    """An op row; a step resolves to the op that owns its cost/time."""
    what, row = resolve(conn, ident, anchor_session)
    if what == "step":
        op = _op(conn, row["op_id"])
        if op is None:
            raise QueryError(f"step {ident!r} references missing op {row['op_id']!r}")
        return op
    return row


def resolve_src(conn, ident: str) -> str:
    """The pointer for an id; duplicates serve their owner's pointer; events by event_id."""
    ev = conn.execute("SELECT src FROM events_v WHERE event_id = ?", (ident,)).fetchone()
    if ev is not None:
        return ev["src"]
    what, row = resolve(conn, ident)
    if what == "op" and row["duplicate_of"] is not None:
        owner = _op(conn, row["duplicate_of"])
        if owner is None:
            raise QueryError(f"{ident!r} is a duplicate of a missing op {row['duplicate_of']!r}")
        return owner["src"]
    return row["src"]


# ── stable -> display ───────────────────────────────────────────────────────


def _index(rows: list[sqlite3.Row], op_id: str) -> int | None:
    for i, r in enumerate(rows, 1):
        if r["op_id"] == op_id:
            return i
    return None


def _display_turn(conn, turn: sqlite3.Row) -> str | None:
    parent = _by_rowid(conn, turn["parent_rowid"])
    if parent["kind"] == "session":
        if turn["display_ix"] is None:
            # QRY-2: harness turns have no display id; every other main turn has a display_ix (DM-24)
            assert turn["turn_kind"] == "harness", f"main turn {turn['op_id']} has no display_ix (DM-24)"
            return None
        return f"T{turn['display_ix']}"
    if parent["kind"] != "engagement":
        return None
    cause = _by_rowid(conn, parent["parent_rowid"])
    if cause["kind"] == "session":
        k = _index(_session_engagements(conn, cause["rowid"]), parent["op_id"])
        return f"E{k}/{_index(_engagement_turns(conn, parent['rowid']), turn['op_id'])}"
    if cause["kind"] != "tool_call":
        return None
    step = conn.execute(
        "SELECT * FROM steps_v WHERE op_id = ? ORDER BY ordinal LIMIT 1", (cause["op_id"],)
    ).fetchone()
    if step is None:
        return None
    prefix = _display_step(conn, step)
    if prefix is None:
        return None
    return f"{prefix}/{_index(_caused_turns(conn, cause['op_id']), turn['op_id'])}"


def _display_step(conn, step: sqlite3.Row) -> str | None:
    turn = _op(conn, step["turn_id"])
    head = _display_turn(conn, turn) if turn is not None else None
    return f"{head}.{step['ordinal'] + 1}" if head is not None else None


def display_of(conn, what: str, row: sqlite3.Row) -> str | None:
    if what == "step":
        return _display_step(conn, row)
    if row["kind"] == "turn":
        return _display_turn(conn, row)
    if row["kind"] == "engagement":
        parent = _by_rowid(conn, row["parent_rowid"])
        if parent["kind"] == "session":
            return f"E{_index(_session_engagements(conn, parent['rowid']), row['op_id'])}"
    return None


# ── outline ─────────────────────────────────────────────────────────────────


def _turn_rows(conn, turn: sqlite3.Row, display: str | None, level: int, depth: int) -> list[dict[str, Any]]:
    steps = conn.execute(
        "SELECT * FROM steps_v WHERE turn_id = ? ORDER BY ordinal", (turn["op_id"],)
    ).fetchall()
    answer = next((s for s in reversed(steps) if s["verb"] == "answer"), None)
    rows = [
        {
            "display_id": display,
            "id": turn["op_id"],
            "kind": "turn",
            "verb": None,
            "target": turn["label"],
            "outcome": answer["outcome"] if answer is not None else None,
            "dur_ms": turn["wall_ms"],
            "cost_usd": turn["incl_cost_usd"],
            "level": level,
        }
    ]
    expanded: set[str] = set()
    for s in steps:
        sd = f"{display}.{s['ordinal'] + 1}" if display is not None else None
        rows.append(
            {
                "display_id": sd,
                "id": f"{s['turn_id']}#{s['ordinal']}",
                "kind": "step",
                "verb": s["verb"],
                "target": s["target"],
                "outcome": s["outcome"],
                "dur_ms": s["dur_ms"],
                "cost_usd": None,
                "level": level,
            }
        )
        if depth > 1 and s["op_id"] not in expanded:
            expanded.add(s["op_id"])
            for i, child in enumerate(_caused_turns(conn, s["op_id"]), 1):
                cd = f"{sd}/{i}" if sd is not None else None
                rows += _turn_rows(conn, child, cd, level + 1, depth - 1)
    return rows


def outline(conn, ident: str, depth: int) -> dict[str, Any]:
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= 16:
        raise QueryError("depth must be 1..16")
    what, row = resolve(conn, ident)
    if what == "step":
        turn = _op(conn, row["turn_id"])
        scope_display = display_of(conn, "step", row)
    else:
        turn = row
        scope_display = display_of(conn, "op", row)
        if row["kind"] in ("llm_call", "tool_call"):
            turn = _enclosing_turn(conn, row)
    rows: list[dict[str, Any]] = []
    if turn is not None and turn["kind"] == "turn":
        rows = _turn_rows(conn, turn, display_of(conn, "op", turn), 0, depth)
        if what == "step":
            keep = f"{row['turn_id']}#{row['ordinal']}"
            rows = [r for r in rows if r["kind"] == "turn" or r["id"] == keep]
    elif row["kind"] == "session":
        for t in _session_turns(conn, row["rowid"]):
            rows += _turn_rows(conn, t, display_of(conn, "op", t), 0, depth)
        if depth > 1:
            for k, eng in enumerate(_session_engagements(conn, row["rowid"]), 1):
                for i, t in enumerate(_engagement_turns(conn, eng["rowid"]), 1):
                    rows += _turn_rows(conn, t, f"E{k}/{i}", 1, depth - 1)
    elif row["kind"] == "engagement":
        for t in _engagement_turns(conn, row["rowid"]):
            rows += _turn_rows(conn, t, display_of(conn, "op", t), 0, depth)
    return {
        "scope": {
            "id": _stable(what, row),
            "display_id": scope_display,
            "kind": row["kind"] if what == "op" else "step",
        },
        "rows": rows,
        "freshness": freshness(conn),
    }


def _stable(what: str, row: sqlite3.Row) -> str:
    return f"{row['turn_id']}#{row['ordinal']}" if what == "step" else row["op_id"]


def _enclosing_turn(conn, row: sqlite3.Row) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT t.* FROM ops_v t WHERE t.root_rowid = ? AND t.pre <= ? AND t.post >= ? AND t.kind = 'turn' "
        "ORDER BY t.depth DESC LIMIT 1",  # DM-15: the nearest enclosing turn
        (row["root_rowid"], row["pre"], row["post"]),
    ).fetchone()
