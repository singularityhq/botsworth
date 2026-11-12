"""`botsworth ingest --since` (CLI-12, DM-93, D59): a date window by file mtime per discovered unit."""

import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from botsworth import pipeline
from botsworth.cli import main as cli
from botsworth.cli.render import render_table

FIXTURES = Path(__file__).parents[1] / "fixtures"
DAY = 86400


@pytest.fixture(autouse=True)
def no_mtime_live(monkeypatch):
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", -3_600_000)


def age(path: Path, days: float) -> None:
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def make_root(tmp_path):
    """Three Claude sessions: `new` (1 day old, main + subagent), `mixed` (main 1 day, subagent 30 days),
    `old` (30 days)."""
    root = tmp_path / "claude"
    shutil.copytree(FIXTURES / "F2" / "project", root / "pa")
    (root / "pa" / "s1.jsonl").rename(root / "pa" / "new.jsonl")
    (root / "pa" / "s1").rename(root / "pa" / "new")
    shutil.copytree(FIXTURES / "F2" / "project", root / "pb")
    (root / "pb" / "s1.jsonl").rename(root / "pb" / "mixed.jsonl")
    (root / "pb" / "s1").rename(root / "pb" / "mixed")
    shutil.copy(FIXTURES / "F1" / "project" / "s1.jsonl", root / "pc.jsonl.tmp")
    (root / "pc").mkdir()
    (root / "pc.jsonl.tmp").rename(root / "pc" / "old.jsonl")
    for f in (root / "pa").rglob("*"):
        age(f, 1)
    age(root / "pb" / "mixed.jsonl", 1)
    for f in (root / "pb" / "mixed").rglob("*.jsonl"):
        age(f, 30)
    age(root / "pc" / "old.jsonl", 30)
    return root


def run(tmp_path, root, **kw):
    return pipeline.run(["claude_code"], {"claude_code": root}, db=tmp_path / "s.db", **kw)


def meta(tmp_path) -> dict:
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        return dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()


def stored_sessions(tmp_path) -> set[str]:
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        return {b for (b,) in conn.execute("SELECT DISTINCT bundle_id FROM source_files")}
    finally:
        conn.close()


def test_window_loads_only_units_with_a_recent_file(tmp_path):
    root = make_root(tmp_path)
    rep = run(tmp_path, root, since="10d")
    claude = rep["providers"]["claude_code"]
    assert (claude["discovered"], claude["rebuilt"], claude["outside_window"]) == (3, 2, 1)
    assert stored_sessions(tmp_path) == {"claude_code/new", "claude_code/mixed"}
    assert rep["window"]["since"] == "10d"
    assert rep["window"]["outside_window"] == 1
    expected = int(time.time() * 1000) - 10 * DAY * 1000
    assert abs(rep["window"]["cutoff_ms"] - expected) < 60_000


def test_session_with_old_subagent_file_is_kept_whole(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        paths = {
            Path(p).name
            for (p,) in conn.execute("SELECT path FROM source_files WHERE bundle_id='claude_code/mixed'")
        }
    finally:
        conn.close()
    assert {
        "mixed.jsonl",
        "agent-a.jsonl",
    } <= paths  # the old subagent file is read with its recent main file


def test_unit_whose_only_recent_file_is_a_subagent_is_kept(tmp_path):
    root = make_root(tmp_path)
    age(root / "pb" / "mixed.jsonl", 30)
    age(root / "pb" / "mixed" / "subagents" / "agent-a.jsonl", 1)
    rep = run(tmp_path, root, since="10d")
    assert rep["providers"]["claude_code"]["rebuilt"] == 2
    assert "claude_code/mixed" in stored_sessions(tmp_path)


def test_next_ingest_without_the_flag_keeps_the_stored_window(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    assert meta(tmp_path)["ingest_since"] == "10d"
    again = run(tmp_path, root)
    assert again["window"]["since"] == "10d"
    assert again["providers"]["claude_code"]["outside_window"] == 1
    assert meta(tmp_path)["ingest_since"] == "10d"
    assert stored_sessions(tmp_path) == {"claude_code/new", "claude_code/mixed"}


def test_stored_rolling_window_is_resolved_again(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    age(root / "pa" / "new.jsonl", 20)  # time passes: the session is now outside the 10 days
    for f in (root / "pa" / "new").rglob("*.jsonl"):
        age(f, 20)
    again = run(tmp_path, root)
    assert again["providers"]["claude_code"]["outside_window"] == 2


def test_since_all_brings_old_units_in_and_clears_the_window(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    rep = run(tmp_path, root, since="all")
    assert rep["window"] is None
    assert rep["providers"]["claude_code"]["outside_window"] == 0
    assert "ingest_since" not in meta(tmp_path)
    assert stored_sessions(tmp_path) == {"claude_code/new", "claude_code/mixed", "claude_code/old"}
    assert run(tmp_path, root)["window"] is None  # and it stays cleared


def test_rebuild_keeps_the_stored_window_unless_given_another(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    rep = run(tmp_path, root, rebuild=True)
    assert rep["mode"] == "rebuild"
    assert rep["window"]["since"] == "10d"
    assert meta(tmp_path)["ingest_since"] == "10d"
    assert stored_sessions(tmp_path) == {"claude_code/new", "claude_code/mixed"}
    rep = run(tmp_path, root, rebuild=True, since="all")
    assert rep["window"] is None
    assert stored_sessions(tmp_path) == {"claude_code/new", "claude_code/mixed", "claude_code/old"}
    assert "ingest_since" not in meta(tmp_path)


def test_schema_mismatch_rebuild_keeps_the_stored_window(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="10d")
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    rep = run(tmp_path, root)
    assert rep["mode"] == "rebuild"
    assert rep["window"]["since"] == "10d"
    assert meta(tmp_path)["ingest_since"] == "10d"


def test_stored_unit_outside_the_window_is_not_deleted(tmp_path):
    root = make_root(tmp_path)
    run(tmp_path, root, since="all")
    conn = sqlite3.connect(tmp_path / "s.db")
    before = conn.execute("SELECT count(*) FROM ops").fetchone()[0]
    sess_before = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    conn.close()
    rep = run(tmp_path, root, since="10d")
    assert rep["providers"]["claude_code"]["outside_window"] == 1
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        assert conn.execute("SELECT count(*) FROM ops").fetchone()[0] == before
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == sess_before
        statuses = {Path(p).name: s for p, s in conn.execute("SELECT path, status FROM source_files")}
    finally:
        conn.close()
    assert statuses["old.jsonl"] == "settled"  # skipped, not source_missing


def test_fixed_date_form(tmp_path):
    root = make_root(tmp_path)
    cutoff = datetime.fromtimestamp(time.time() - 10 * DAY, UTC).date().isoformat()
    rep = run(tmp_path, root, since=cutoff)
    assert rep["window"]["since"] == cutoff
    assert rep["window"]["cutoff_ms"] == int(
        datetime.fromisoformat(cutoff + "T00:00:00+00:00").timestamp() * 1000
    )
    assert rep["providers"]["claude_code"]["outside_window"] == 1
    assert meta(tmp_path)["ingest_since"] == cutoff
    # a date later than every file leaves nothing in the window
    rep = run(tmp_path, root, since="2099-01-01", force=True)
    assert rep["providers"]["claude_code"]["outside_window"] == 3


@pytest.mark.parametrize("bad", ["", "10", "0d", "-3d", "d", "10days", "2026-13-01", "2026-1-1", "week"])
def test_bad_value_is_refused(tmp_path, bad):
    with pytest.raises(pipeline.SinceInvalid):
        pipeline.parse_since(bad, 0)


def test_bad_value_exits_2_with_the_accepted_forms(tmp_path, monkeypatch, capsys):
    roots = {p: tmp_path / p for p in ("claude_code", "codex", "omp")}
    monkeypatch.setattr(pipeline, "DEFAULT_ROOTS", roots)
    code = cli.main(["--db", str(tmp_path / "s.db"), "ingest", "--since", "soon"])
    err = capsys.readouterr().err
    assert code == 2
    assert "Nd" in err and "YYYY-MM-DD" in err and "all" in err
    assert not (tmp_path / "s.db").exists()  # refused before any store work


def test_cli_report_states_window_and_skipped_count(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    roots = {"claude_code": root, "codex": tmp_path / "x1", "omp": tmp_path / "x2"}
    monkeypatch.setattr(pipeline, "DEFAULT_ROOTS", roots)
    code = cli.main(["--db", str(tmp_path / "s.db"), "ingest", "--since", "10d"])
    out = capsys.readouterr().out
    assert code == 0
    data = json.loads(out)
    assert data["window"]["since"] == "10d"
    assert data["window"]["outside_window"] == 1
    text = render_table("ingest", data)
    assert "window" in text and "10d" in text and "1 outside window" in text
