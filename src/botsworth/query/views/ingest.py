"""CONTRACT: ingest state (QRY-39, QRY-40; API-30). Real code, owned by Q0 (the HTTP core needs it).

GUARANTEES:
- `ingest_state(db)` never raises for a missing or old store. `store_state` is `missing`,
  `schema_mismatch` or `ready`; `store_exists` = state is not `missing`. With a state other than `ready`,
  `sources` is `[]` and `last_run` is None. `freshness` is the QRY-17 envelope (`last_ingest_ms` None and
  the code's `schema_version` when not ready).
- `ingest_lock(db)` reads `<db>.lock` without taking the flock (QRY-40): `running` is true when the file
  holds a pid and that process exists. An empty or unreadable file means not running.
EXPECTS: `<db>.lock` written by `store.ingest.lock` as `{"pid", "started_ms"}`.
FAILURE BEHAVIOR: none; every outcome is a value.
DOES NOT: take the flock, write, or ingest.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from botsworth.query import Db, store_state, wire
from botsworth.store import ingest as store_ingest
from botsworth.store.location import resolve_db


def _path(db: Db) -> Path:
    if isinstance(db, sqlite3.Connection):
        # the main database file of the connection
        return Path(db.execute("PRAGMA database_list").fetchone()[2])
    return Path(db) if db is not None else resolve_db()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # the process exists but belongs to another user
    return True


def ingest_lock(db: Db = None) -> wire.IngestRunning:
    lock = Path(f"{_path(db)}.lock")
    try:
        raw = lock.read_bytes()
    except FileNotFoundError:
        return wire.IngestRunning(
            False, None, None
        )  # FALLBACK-OK: interfaces.md QRY-40 — no lock file, no ingest
    if not raw:
        return wire.IngestRunning(
            False, None, None
        )  # FALLBACK-OK: interfaces.md QRY-40 — truncated on release
    try:
        held: Any = json.loads(raw)
        pid, started = (
            int(held["pid"]),
            held.get("started_ms"),
        )  # FALLBACK-OK: QRY-40 — started_ms is informational
    except (ValueError, KeyError, TypeError):
        # FALLBACK-OK: interfaces.md QRY-40 — a half-written payload is read again by the next request
        return wire.IngestRunning(False, None, None)
    if not _pid_alive(pid):
        return wire.IngestRunning(False, None, None)  # FALLBACK-OK: interfaces.md QRY-40 — stale pid
    return wire.IngestRunning(True, started, pid)


def ingest_state(db: Db = None) -> wire.IngestStatus:
    state, fresh = store_state(db)
    running = ingest_lock(db)
    sources: list[wire.IngestSource] = []
    last_run = None
    if state == "ready":
        status = store_ingest.status(_path(db))
        sources = [wire.IngestSource(**s) for s in status["sources"]]
        last_run = wire.LastRun(**status["last_run"]) if "last_run" in status else None
    return wire.IngestStatus(
        store_exists=state != "missing",
        store_state=state,
        freshness=wire.Freshness(**fresh),
        ingest=running,
        sources=sources,
        last_run=last_run,
    )
