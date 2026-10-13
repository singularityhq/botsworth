"""Rebuild by swap (DM-4), VACUUM (DM-16), ANALYZE (DM-5): pipeline.run against a store of another version."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from botsworth import pipeline
from botsworth.store import ingest, load, schema

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def no_mtime_live(monkeypatch):
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", -3_600_000)


def claude_tree(tmp_path):
    root = tmp_path / "claude"
    shutil.copytree(FIXTURES / "F2" / "project", root / "project")
    return root


def run(tmp_path, root, **kw):
    return pipeline.run(["claude_code"], {"claude_code": root}, db=tmp_path / "s.db", **kw)


def old_store(tmp_path, root, monkeypatch):
    """A store of another schema version, built by the pipeline under `SCHEMA_VERSION - 1`."""
    with monkeypatch.context() as m:
        m.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION - 1)
        assert run(tmp_path, root)["mode"] == "rebuild"
    db = tmp_path / "s.db"
    (tmp_path / "s.db.lock").write_bytes(b"")
    return db


def meta(db):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()


def leftovers(tmp_path):
    return sorted(p.name for p in tmp_path.glob("s.db*") if p.name not in ("s.db", "s.db.lock"))


def test_rebuild_failure_leaves_db_identical(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    db = old_store(tmp_path, root, monkeypatch)
    before = db.read_bytes()
    assert leftovers(tmp_path) == []

    def boom(conn, bundle):
        raise RuntimeError("load failed")

    monkeypatch.setattr(load, "load_bundle", boom)
    with pytest.raises(RuntimeError, match="load failed"):
        run(tmp_path, root)
    assert db.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_rebuild_success_swaps_to_current_version(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    db = old_store(tmp_path, root, monkeypatch)
    assert meta(db)["schema_version"] == str(schema.SCHEMA_VERSION - 1)
    report = run(tmp_path, root)
    assert report["mode"] == "rebuild"
    assert meta(db)["schema_version"] == str(schema.SCHEMA_VERSION)
    assert leftovers(tmp_path) == []
    assert run(tmp_path, root)["mode"] == "incremental"


def test_leftover_rebuild_file_is_removed_first(tmp_path):
    root = claude_tree(tmp_path)
    (tmp_path / "s.db.rebuild").write_bytes(b"junk")
    (tmp_path / "s.db.rebuild-wal").write_bytes(b"junk")
    assert run(tmp_path, root)["mode"] == "rebuild"
    assert leftovers(tmp_path) == []
    assert meta(tmp_path / "s.db")["schema_version"] == str(schema.SCHEMA_VERSION)


def test_reader_open_during_build_reads_old_store(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    db = old_store(tmp_path, root, monkeypatch)
    reader = sqlite3.connect(db)
    old_ops = reader.execute("SELECT count(*) FROM ops").fetchone()[0]
    assert old_ops > 0
    seen = []
    real = load.load_bundle

    def spy(conn, bundle):
        seen.append(reader.execute("SELECT count(*) FROM ops").fetchone()[0])  # mid-build read
        return real(conn, bundle)

    monkeypatch.setattr(load, "load_bundle", spy)
    try:
        assert run(tmp_path, root)["mode"] == "rebuild"
    finally:
        reader.close()
    assert seen == [old_ops]


def test_rebuild_is_vacuumed(tmp_path):
    root = claude_tree(tmp_path)
    run(tmp_path, root)
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    finally:
        conn.close()


def test_mode_check_does_not_touch_v6_store(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    db = old_store(tmp_path, root, monkeypatch)
    tables = lambda: sorted(  # noqa: E731
        r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")
    )
    before = tables()
    assert ingest.needs_rebuild(db)
    assert ingest.source_files(db) == {}
    assert tables() == before
    monkeypatch.setattr(ingest, "finish_rebuild", lambda db: (_ for _ in ()).throw(RuntimeError("swap")))
    with pytest.raises(RuntimeError, match="swap"):
        run(tmp_path, root)
    assert tables() == before
    assert meta(db)["schema_version"] == str(schema.SCHEMA_VERSION - 1)


@pytest.mark.parametrize("second_mode", ["rebuild", "incremental"])
def test_analyze_after_changed_ingest(tmp_path, monkeypatch, second_mode):
    root = claude_tree(tmp_path)
    db = tmp_path / "s.db"
    if second_mode == "incremental":
        run(tmp_path, root)
        # change a bundle so the second run has work: force reloads it
        run(tmp_path, root, force=True)
    else:
        old_store(tmp_path, root, monkeypatch)
        run(tmp_path, root)
    conn = sqlite3.connect(db)
    try:
        stat1 = {r[0] for r in conn.execute("SELECT tbl FROM sqlite_stat1")}
    finally:
        conn.close()
    assert {"ops", "sessions"} <= stat1


def test_analyze_runs_in_place_after_changed_bundle(tmp_path):
    root = claude_tree(tmp_path)
    run(tmp_path, root)
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("DELETE FROM sqlite_stat1")
    conn.commit()
    conn.close()
    unchanged = run(tmp_path, root)
    assert unchanged["store"]["bundles_changed"] == 0
    conn = sqlite3.connect(tmp_path / "s.db")
    assert conn.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] == 0  # no change, no ANALYZE
    conn.close()
    assert run(tmp_path, root, force=True)["store"]["bundles_changed"] == 1
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        assert {r[0] for r in conn.execute("SELECT tbl FROM sqlite_stat1")} >= {"ops", "sessions"}
    finally:
        conn.close()


def test_missing_store_rebuilds_and_lock_is_empty_after(tmp_path):
    root = claude_tree(tmp_path)
    assert run(tmp_path, root)["mode"] == "rebuild"
    assert (tmp_path / "s.db.lock").read_bytes() == b""
    assert json.loads(json.dumps(meta(tmp_path / "s.db")))["parser_version"] == pipeline.PARSER_VERSION
