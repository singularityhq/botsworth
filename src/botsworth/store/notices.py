"""CONTRACT: the stored task notices of one session, as the time split needs them (DM-41, DM-50 rule 5; D55).

GUARANTEES:
- `stored(conn, root_rowid)` returns `(ts_ms, picked_ms)` for every `notice` event of the root, ordered by
  `ts_ms`: `ts_ms` is the queue time, `picked_ms` the start of the thread's next llm_call or None.
  Load and the query layer both call it, so the stalls they feed to `timesplit.collect` are equal.
EXPECTS: the schema of `store.schema`; every `notice` event has an integer `ts_ms`.
FAILURE BEHAVIOR: a `notice` event without `ts_ms` raises AssertionError.
DOES NOT: decide what a stall is (`store.timesplit`), write events.
"""

from __future__ import annotations

import sqlite3


def stored(conn: sqlite3.Connection, root_rowid: int) -> list[tuple[int, int | None]]:
    rows = conn.execute(
        "SELECT ts_ms, json_extract(fields, '$.picked_ms') FROM events"
        " WHERE root_rowid = ? AND kind = 'notice' ORDER BY ts_ms",
        (root_rowid,),
    ).fetchall()
    assert all(r[0] is not None for r in rows), "notice event without ts_ms"
    return [(r[0], r[1]) for r in rows]
