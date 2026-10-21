"""CONTRACT: the stored task notices of one session, as the time split needs them (DM-41, DM-50 rule 5; D55).

GUARANTEES:
- `stalling(conn, root_rowids)` returns a `Notice(root_rowid, ts_ms, picked_ms, agent_id)` for every `notice`
  event of the roots that can cause a stall, ordered by root and `ts_ms`: `ts_ms` is the queue time,
  `picked_ms` the start of the thread's next llm_call or None, `agent_id` the thread (None for main). A
  notice with `status` `killed` or `stopped` is left out (DM-41, DM-50 rule 5 [D56]): the task was ended on
  purpose, so no thread waits on it. This is the one place that decides it.
- `stored(conn, root_rowid)` returns `(ts_ms, picked_ms)` of `stalling` for one root. Load and the query
  layer both call it, so the stalls they feed to `timesplit.collect` are equal; the Review's `threads` read
  `stalling`.
EXPECTS: the schema of `store.schema`; every `notice` event has an integer `ts_ms`.
FAILURE BEHAVIOR: a `notice` event without `ts_ms` raises AssertionError.
DOES NOT: decide what a stall is (`store.timesplit`), write events.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import NamedTuple

_NO_STALL_STATUSES = ("killed", "stopped")


class Notice(NamedTuple):
    root_rowid: int
    ts_ms: int
    picked_ms: int | None
    agent_id: str | None


def stalling(conn: sqlite3.Connection, root_rowids: Sequence[int]) -> list[Notice]:
    rows = conn.execute(
        "SELECT root_rowid, ts_ms, json_extract(fields, '$.picked_ms'), json_extract(fields, '$.agent_id')"
        " FROM events WHERE kind = 'notice' AND root_rowid IN (SELECT value FROM json_each(?))"
        # FALLBACK-OK: DM-41 — a notice with no status stalls like a completed one
        " AND COALESCE(json_extract(fields, '$.status'), '') NOT IN (?, ?) ORDER BY root_rowid, ts_ms",
        (json.dumps(list(root_rowids)), *_NO_STALL_STATUSES),
    ).fetchall()
    assert all(r[1] is not None for r in rows), "notice event without ts_ms"
    return [Notice(*r) for r in rows]


def stored(conn: sqlite3.Connection, root_rowid: int) -> list[tuple[int, int | None]]:
    return [(n.ts_ms, n.picked_ms) for n in stalling(conn, [root_rowid])]
