import sqlite3
from dataclasses import fields

import pytest

from botsworth.model import Event, Op, Step
from botsworth.store import schema


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_ddl_creates_all_tables_and_integrity_ok(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(schema.TABLES) <= names
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_columns_carry_canonical_field_names(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    # v7 stores some Op fields differently (DM-8, DM-13, DM-14): the view `ops_v` still carries every
    # canonical name except the three that are not stored on ops and the two that are never stored.
    not_on_ops = {"occ_key", "args_json", "title_source", "entrypoint", "originator"}
    assert {f.name for f in fields(Op)} - not_on_ops <= columns(conn, "ops_v")
    assert {"entrypoint", "originator"} <= columns(conn, "sessions")
    assert {f.name for f in fields(Step)} <= columns(conn, "steps_v")
    assert {f.name for f in fields(Event)} <= columns(conn, "events_v")


def test_version_mismatch_drops_and_recreates(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    conn.execute("INSERT INTO meta VALUES ('last_ingest_ms', '1')")
    conn.commit()
    schema.ensure(conn)  # same version: data kept
    assert conn.execute("SELECT count(*) FROM meta").fetchone()[0] == 2
    conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
    conn.commit()
    schema.ensure(conn)
    assert conn.execute("SELECT key, value FROM meta").fetchall() == [
        ("schema_version", str(schema.SCHEMA_VERSION))
    ]


def test_owner_key_one_owner_per_occurrence(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    row = (
        "INSERT INTO ops(op_id, kind, provider, src_file, src_loc, occ_hash, duplicate_of)"
        " VALUES (?,?,?,0,'L1-L1',?,?)"
    )
    conn.execute(row, ("a", "llm_call", "claude_code", b"req1", None))
    conn.execute(row, ("b", "llm_call", "claude_code", b"req1", "a"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(row, ("c", "llm_call", "claude_code", b"req1", None))


def test_ops_segments_column_holds_json(tmp_path):
    import json

    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    assert "segments" in columns(conn, "ops")
    conn.execute(
        "INSERT INTO ops(op_id, kind, provider, src_file, src_loc, segments) VALUES (?,?,?,0,'L1-L1',?)",
        ("t", "turn", "omp", json.dumps([[1, 5]])),
    )
    assert json.loads(conn.execute("SELECT segments FROM ops").fetchone()[0]) == [[1, 5]]


def test_session_metadata_columns_on_ops_and_sessions(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    meta = {"title", "git_branch", "harness_version"}
    assert meta | {"first_line_ms"} <= columns(conn, "ops") and meta | {"status"} <= columns(conn, "sessions")


def test_time_split_and_stretches_reject_states_outside_the_spec(tmp_path):
    # DM-51, DM-52 [D54]: the tables carry the state lists of SES-15 as CHECK constraints
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    ok = "INSERT INTO time_split VALUES (1, 1, ?, ?, NULL, 5)"
    conn.execute(ok, ("session", "you"))
    for scope, state in (("turn", "you"), ("session", "waiting")):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ok, (scope, state))
    stretch = "INSERT INTO stretches VALUES (1, 1, ?, NULL, 0, 9)"
    for state in ("agents", "tool", "you", "agentmsg", "limit", "none"):  # D55: `tool` is `background` only
        conn.execute(stretch, (state,))
    for state in ("model",):  # a main-thread model call is never a stretch
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(stretch, (state,))
