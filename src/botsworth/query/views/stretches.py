"""CONTRACT: stretch labelling and attachment (owner Q2a; shared by the session, turn and use-case views).

No view function. Builders turn the stored `stretches` rows (DM-52) into `wire.Stretch` (QRY-41).

GUARANTEES:
- `stretches_for(conn, root_rowid, from_ms=None, to_ms=None, min_ms=60000)` returns the session's stretches
  of at least `min_ms` (at least the stored floor, 60 s) in start order. With `from_ms` and `to_ms` it keeps
  the stretches that overlap `[from_ms, to_ms]`; it does not clip them. `stretches_for_roots(conn, roots)`
  returns the same lists, 60 s floor and no range, for many sessions in one pass (QRY-41: same result as
  one call per session).
- `turn` is recomputed by SES-37 and never read from `stretches.turn_rowid` (D38 M11): the latest numbered
  (non-harness) main turn that starts at or before the stretch start; before the first numbered turn it
  is the first numbered turn; `None` only when the session has no numbered turn.
- `next_turn` is set only when a main turn (harness included) starts exactly at the stretch's `end_ms`
  (DM-55); `display_id` is `T<display_ix>`, `None` for a harness turn.
- `label`: `agents` for state `agents`. For `none`, in this order: `before_first` when no numbered turn starts
  at or before the stretch start (SES-18; D41 M11), `limit` when the next turn is `human` and its label
  starts with a SES-17 prefix, `you` when the next turn is `human`, `agentmsg` when the next turn is a
  `message` with origin `peer`, `coordinator`, `teammate` or `agent`, else `none`.
- `running` (state `agents` only): up to 8 engagements (any depth) whose run overlaps the stretch, in start
  order, each with its whole cost `incl_cost_usd` (QRY-34); `[]` for `none`.
- Nothing is stored or cached: labels are computed per request.
EXPECTS: schema v7 with B3a's `turn_kind`, `turn_origin`, `display_ix` and B3b's `stretches` rows. The
  stored `state` is `none` or `agents` (table CHECK).
FAILURE BEHAVIOR: a numbered main turn without `display_ix`, or a `human` next turn without a label,
  raises AssertionError (DM-24).
DOES NOT: decide which stretches are gap rows (session.py), write, or read `stretches.turn_rowid`.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence

from botsworth.query import wire

MIN_STORED_MS = 60_000
MAX_RUNNING = 8
LIMIT_PREFIXES = (  # SES-17
    "i hit my usage limit",
    "please continue from where you left off",
    "continue from where you left off",
)
AGENT_ORIGINS = frozenset({"peer", "coordinator", "teammate", "agent"})  # QRY-41 `agentmsg`

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


def is_limit_text(label: str) -> bool:
    """SES-17: the text, trimmed and lower-cased, starts with a usage-limit prefix."""
    return label.strip().lower().startswith(LIMIT_PREFIXES)


def display_id(turn: sqlite3.Row) -> str | None:
    """QRY-2: `T<display_ix>`; harness turns have none."""
    if turn["display_ix"] is None:
        assert turn["turn_kind"] == "harness", f"main turn {turn['op_id']} has no display_ix (DM-24)"
        return None
    return f"T{turn['display_ix']}"


def _label(state: str, before_first: bool, nxt: sqlite3.Row | None) -> str:
    if state == "agents":
        return "agents"
    if before_first:
        return "before_first"
    if nxt is None:
        return "none"
    if nxt["turn_kind"] == "human":
        assert nxt["label"] is not None, f"human turn {nxt['op_id']} has no label"
        return "limit" if is_limit_text(nxt["label"]) else "you"
    if nxt["turn_kind"] == "message" and nxt["turn_origin"] in AGENT_ORIGINS:
        return "agentmsg"
    return "none"


def _attached(numbered: Sequence[sqlite3.Row], start_ms: int) -> tuple[sqlite3.Row | None, bool]:
    """SES-37: `(turn, before_first)`. `numbered` is sorted by start."""
    chosen = None
    for t in numbered:
        if t["start_ms"] > start_ms:
            break
        chosen = t
    if chosen is not None:
        return chosen, False
    return (numbered[0] if numbered else None), True


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
    if any(r["state"] == "agents" for rs in stored.values() for r in rs):
        for e in conn.execute(_ENGAGEMENT_SQL.format(marks=marks), list(roots)):
            engagements[e["root_rowid"]].append(e)

    for root in roots:
        by_start = {}
        for t in turns[root]:
            by_start.setdefault(t["start_ms"], t)  # the lowest seq wins (rows are in seq order)
        numbered = [t for t in turns[root] if t["turn_kind"] != "harness"]
        for r in stored[root]:
            turn, before_first = _attached(numbered, r["start_ms"])
            nxt = by_start.get(r["end_ms"])
            running = []
            if r["state"] == "agents":
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
                    label=_label(r["state"], before_first, nxt),
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
