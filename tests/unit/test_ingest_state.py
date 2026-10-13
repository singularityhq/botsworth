"""Q1 ingest_state (QRY-39, QRY-40; API-30): never raises, reads the lock file without taking the flock."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from botsworth.query import ingest_state, wire
from botsworth.store import ingest


def lock_of(db: Path) -> Path:
    return Path(f"{db}.lock")


@pytest.fixture
def ready(qstore: Path, tmp_path: Path) -> Path:
    db = tmp_path / "botsworth.sqlite"
    src = sqlite3.connect(f"{qstore.as_uri()}?mode=ro", uri=True)
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    dst.close()
    return db


def test_ingest_state_missing_mismatch_ready(tmp_path, ready):
    missing = ingest_state(db=tmp_path / "none.sqlite")
    assert isinstance(missing, wire.IngestStatus)
    assert (missing.store_exists, missing.store_state, missing.sources, missing.last_run) == (
        False, "missing", [], None
    )  # fmt: skip
    assert missing.freshness.last_ingest_ms is None and missing.ingest == wire.IngestRunning(
        False, None, None
    )

    old = tmp_path / "old.sqlite"
    ingest.ingest(db=old)
    conn = sqlite3.connect(old)
    conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    mismatch = ingest_state(db=old)
    assert (mismatch.store_exists, mismatch.store_state, mismatch.sources, mismatch.last_run) == (
        True, "schema_mismatch", [], None
    )  # fmt: skip
    assert mismatch.freshness.last_ingest_ms is None and mismatch.freshness.schema_version != 6

    state = ingest_state(db=ready)
    assert (state.store_exists, state.store_state) == (True, "ready")
    assert state.freshness.last_ingest_ms is not None
    assert state.last_run is not None and state.last_run.rejected == []
    assert state.sources, "a store built from streams lists its sources"
    assert state.ingest == wire.IngestRunning(False, None, None)


def test_running_from_lock_pid(ready):
    lock_of(ready).write_text(json.dumps({"pid": os.getpid(), "started_ms": 1791786600000}))
    assert ingest_state(db=ready).ingest == wire.IngestRunning(True, 1791786600000, os.getpid())
    # the signal does not touch the store or the lock: the same file answers twice
    assert ingest_state(db=ready).ingest.running is True
    assert json.loads(lock_of(ready).read_text())["pid"] == os.getpid()


def test_dead_pid_not_running(ready):
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
    )
    dead = int(done.stdout)
    lock_of(ready).write_text(json.dumps({"pid": dead, "started_ms": 5}))
    assert ingest_state(db=ready).ingest == wire.IngestRunning(False, None, None)
    lock_of(ready).write_text("")  # truncated on release (QRY-40)
    assert ingest_state(db=ready).ingest.running is False
    lock_of(ready).write_text('{"pid": ')  # half-written payload: not running, no error
    assert ingest_state(db=ready).ingest.running is False
    lock_of(ready).unlink()
    assert ingest_state(db=ready).ingest.running is False
