"""CONTRACT: stretch attachment (owner Q2a; shared by the session, turn and use-case views).

No view function. Builders turn the stored `stretches` rows (DM-52) into `wire.Stretch` (QRY-41; D54).

GUARANTEES:
- `stretches_for(conn, root_rowid, from_ms=None, to_ms=None, min_ms=60000)` returns the session's stretches
  of at least `min_ms` (at least the stored floor, 60 s) in start order. With `from_ms` and `to_ms` it keeps
  the stretches that overlap `[from_ms, to_ms]`; it does not clip them. `stretches_for_roots(conn, roots)`
  returns the same lists, 60 s floor and no range, for many sessions in one pass (QRY-41: same result as
  one call per session).
- `state` and `substate` are the stored values; this module decides no label (the load did, DM-50 rule 5).
- `turn` is recomputed by SES-37 and never read from `stretches.turn_rowid` (D38 M11): the latest numbered
  (non-harness) main turn that starts at or before the stretch start; before the first numbered turn it
  is the first numbered turn; `None` only when the session has no numbered turn.
- `next_turn` is set only when a main turn (harness included) starts exactly at the stretch's `end_ms`
  (DM-55; the lowest `seq` at a shared start); `display_id` is `T<display_ix>`, `None` for a harness turn.
- `running` (state `agents`, and state `you` with substate `ask` or `permission`, which is an agent's call
  because a main-thread wait makes no stretch): up to 8 engagements (any depth) whose run overlaps the
  stretch, in start order, each with its whole cost `incl_cost_usd` (QRY-34); `[]` for other stretches.
- Nothing is stored or cached here.
EXPECTS: schema v12 with B3a's `turn_kind`, `turn_origin`, `display_ix` and B3b's `stretches` rows.
FAILURE BEHAVIOR: a numbered main turn without `display_ix` raises AssertionError (DM-24).
DOES NOT: decide which stretches are gap rows (session.py), label a stretch, write, or read
  `stretches.turn_rowid`.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence

from botsworth.query import wire

MIN_STORED_MS = 60_000
MAX_RUNNING = 8

_TURN_SQL = """
SELECT t.rowid AS rowid, t.op_id AS op_id, COALESCE(t.seq, t.pre) AS seq, t.start_ms AS start_ms,
       t.turn_kind AS turn_kind, t.turn_origin AS turn_origin, t.label AS label, t.display_ix AS display_ix,
       s.rowid AS root_rowid
FROM sessions s JOIN ops t ON t.parent_rowid = s.op_rowid AND t.kind = 'turn'
WHERE s.rowid IN ({marks})
ORDER BY s.rowid, t.start_ms, COALESCE(t.seq, t.pre)
"""
_ENGAGEMENT_SQL = """
SELECT root_rowid, op_id, agent_name, start_ms, end_ms, incl_cost_usd
FROM ops
WHERE kind = 'engagement' AND start_ms IS NOT NULL AND end_ms IS NOT NULL AND root_rowid IN ({marks})
ORDER BY root_rowid, start_ms, op_id
"""


def display_id(turn: sqlite3.Row) -> str | None:
    """QRY-2: `T<display_ix>`; harness turns have none."""
    if turn["display_ix"] is None:
        assert turn["turn_kind"] == "harness", f"main turn {turn['op_id']} has no display_ix (DM-24)"
        return None
    return f"T{turn['display_ix']}"


def _has_running(stretch: sqlite3.Row) -> bool:
    """QRY-41: agents stretches and `you` stretches from an agent's call list the engagements running."""
    return stretch["state"] == "agents" or (
        stretch["state"] == "you" and stretch["substate"] in ("ask", "permission")
    )


def _attached(numbered: Sequence[sqlite3.Row], start_ms: int) -> sqlite3.Row | None:
    """SES-37: the latest numbered turn that starts at or before `start_ms`; before the first one, the first
    numbered turn; None when the session has none. `numbered` is sorted by start."""
    chosen = None
    for t in numbered:
        if t["start_ms"] > start_ms:
            break
        chosen = t
    if chosen is not None:
        return chosen
    return numbered[0] if numbered else None


def _build(
    conn: sqlite3.Connection,
    roots: Sequence[int],
    from_ms: int | None,
    to_ms: int | None,
    min_ms: int,
) -> dict[int, list[wire.Stretch]]:
    assert min_ms >= MIN_STORED_MS, f"min_ms {min_ms} is below the stored floor {MIN_STORED_MS} (DM-52)"
    out: dict[int, list[wire.Stretch]] = {r: [] for r in roots}
    if not roots:
        return out
    marks = ",".join("?" * len(roots))
    turns: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for t in conn.execute(_TURN_SQL.format(marks=marks), list(roots)):
        if t["start_ms"] is not None:  # a turn without a start never starts a stretch (DM-55)
            turns[t["root_rowid"]].append(t)
    stored: dict[int, list[sqlite3.Row]] = defaultdict(list)
    rows = conn.execute(
        f"SELECT * FROM stretches WHERE root_rowid IN ({marks}) AND end_ms - start_ms >= ? "
        "ORDER BY root_rowid, start_ms",
        [*roots, min_ms],
    )
    for r in rows:
        if (
            from_ms is not None
            and to_ms is not None
            and not (r["end_ms"] > from_ms and r["start_ms"] < to_ms)
        ):
            continue
        stored[r["root_rowid"]].append(r)
    engagements: dict[int, list[sqlite3.Row]] = defaultdict(list)
    if any(_has_running(r) for rs in stored.values() for r in rs):
        for e in conn.execute(_ENGAGEMENT_SQL.format(marks=marks), list(roots)):
            engagements[e["root_rowid"]].append(e)

    for root in roots:
        by_start = {}
        for t in turns[root]:
            by_start.setdefault(t["start_ms"], t)  # the lowest seq wins (rows are in seq order)
        numbered = [t for t in turns[root] if t["turn_kind"] != "harness"]
        for r in stored[root]:
            turn = _attached(numbered, r["start_ms"])
            nxt = by_start.get(r["end_ms"])
            running = []
            if _has_running(r):
                running = [
                    wire.RunningEngagement(
                        e["op_id"], e["agent_name"], e["start_ms"], e["end_ms"], e["incl_cost_usd"]
                    )
                    for e in engagements[root]
                    if e["start_ms"] < r["end_ms"] and e["end_ms"] > r["start_ms"]
                ][:MAX_RUNNING]
            out[root].append(
                wire.Stretch(
                    start_ms=r["start_ms"],
                    end_ms=r["end_ms"],
                    dur_ms=r["end_ms"] - r["start_ms"],
                    state=r["state"],
                    substate=r["substate"],
                    turn=turn["op_id"] if turn is not None else None,
                    next_turn=_next_turn(nxt),
                    running=running,
                )
            )
    return out


def _next_turn(nxt: sqlite3.Row | None) -> wire.NextTurn | None:
    if nxt is None:
        return None
    # origin and label are set on every main turn once B3a has folded (DM-24)
    return wire.NextTurn(nxt["op_id"], nxt["turn_kind"], nxt["turn_origin"], nxt["label"], display_id(nxt))


def stretches_for(
    conn: sqlite3.Connection,
    root_rowid: int,
    from_ms: int | None = None,
    to_ms: int | None = None,
    min_ms: int = MIN_STORED_MS,
) -> list[wire.Stretch]:
    assert (from_ms is None) == (to_ms is None), "from_ms and to_ms are given together"
    return _build(conn, [root_rowid], from_ms, to_ms, min_ms)[root_rowid]


def stretches_for_roots(
    conn: sqlite3.Connection, root_rowids: Sequence[int]
) -> dict[int, list[wire.Stretch]]:
    return _build(conn, list(dict.fromkeys(root_rowids)), None, None, MIN_STORED_MS)
