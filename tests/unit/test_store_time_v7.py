"""B3b phase 2: time split, spans, stretches, max_silence_ms and projects through load/refold/ingest."""

import dataclasses

from store_builders import T0, B, put, row, split_rows, store
from stream_load import load_streams

from botsworth import stream
from botsworth.model import SourceFile
from botsworth.store import load

M = 60_000


def _split_session(sid="a", **kw):
    """t1 0-30 min: model 0-10, Agent tool 10-15 (agent runs 15-25), nothing 25-30; t2 60-70: model 60-65."""
    b = B(sid, start=0, end=70 * M, **kw)
    t1 = b.turn("t1", start=0, end=30 * M, turn_kind="human")
    b.llm("l1", t1, 0, 10 * M, occ=f"{sid}:l1")
    tool = b.tool("spawn", t1, 10 * M, 15 * M, tool_name="Agent", occ=f"{sid}:spawn")
    eng = b.engagement("e1", tool, "ag", start=15 * M, end=25 * M)
    b.llm("al", eng, 15 * M, 25 * M, agent="ag", occ=f"{sid}:al")
    t2 = b.turn("t2", start=60 * M, end=70 * M, turn_kind="human")
    b.llm("l2", t2, 60 * M, 65 * M, occ=f"{sid}:l2")
    return b


def _stretches(conn):
    return [
        (r["turn_op"], r["state"], r["substate"], r["start_ms"] - T0, r["end_ms"] - T0)
        for r in conn.execute(
            "SELECT o.op_id AS turn_op, s.* FROM stretches s LEFT JOIN ops o ON o.rowid = s.turn_rowid"
            " ORDER BY s.start_ms"
        )
    ]


def _rows(conn, op_id, scope):
    return split_rows(conn, row(conn, op_id)["rowid"], scope)


def _session_rows(conn, session_id="a"):
    rowid = conn.execute("SELECT op_rowid FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]
    return split_rows(conn, rowid, "session")


def test_columns_written_on_load(tmp_path):
    conn = store(tmp_path)
    put(conn, _split_session())
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert _session_rows(conn) == {
        ("model", None): 15 * M,
        ("tool", None): 5 * M,
        ("agents", None): 10 * M,
        ("you", "reply"): 35 * M,  # 25-60: idle, ends where the human turn t2 starts
        ("none", "none"): 5 * M,  # 65-70: idle at the session's end
    }
    assert sum(_session_rows(conn).values()) == s["wall_ms"] == 70 * M
    t1, t2 = "claude_code/a/turn/t1", "claude_code/a/turn/t2"
    # DM-53: span figures over the turn's own bounds; DM-51: windows run to the next main turn
    assert row(conn, t1)["span_wall_ms"] == 30 * M
    assert _rows(conn, t1, "span") == {
        ("model", None): 10 * M,
        ("tool", None): 5 * M,
        ("agents", None): 10 * M,
        ("you", "reply"): 5 * M,
    }
    assert _rows(conn, t1, "window") == {
        ("model", None): 10 * M,
        ("tool", None): 5 * M,
        ("agents", None): 10 * M,
        ("you", "reply"): 35 * M,
    }
    assert row(conn, t2)["span_wall_ms"] == 10 * M
    assert _rows(conn, t2, "span") == {("model", None): 5 * M, ("none", "none"): 5 * M}
    assert _rows(conn, t2, "window") == {("model", None): 5 * M, ("none", "none"): 5 * M}
    # DM-52
    assert _stretches(conn) == [
        (t1, "agents", None, 15 * M, 25 * M),
        (t1, "you", "reply", 25 * M, 60 * M),
        (t2, "none", "none", 65 * M, 70 * M),
    ]
    # span_wall_ms exists on main turns only; time_split rows hang on the session op and main turns
    assert row(conn, "claude_code/a/llm_call/l1")["span_wall_ms"] is None
    ops = {r[0] for r in conn.execute("SELECT DISTINCT op_rowid FROM time_split")}
    assert ops == {row(conn, x)["rowid"] for x in (t1, t2, "claude_code/a/session/-")}
    assert not conn.execute("SELECT 1 FROM time_split WHERE ms <= 0").fetchone()  # 0-ms rows are not stored


def test_user_wait_ask_is_you_ask_in_session_window_and_span(tmp_path):
    # DM-50 rule 4, DM-53 test: a turn 0-30 with a 12 min ask and 18 min model -> span you/ask 12
    b = B("a", start=0, end=30 * M)
    t = b.turn("t", start=0, end=30 * M, turn_kind="human")
    b.llm("l", t, 12 * M, 30 * M, occ="a:l")
    b.tool("ask", t, 0, 12 * M, tool_name="AskUserQuestion", user_wait="ask", occ="a:ask")
    conn = store(tmp_path)
    put(conn, b)
    want = {("you", "ask"): 12 * M, ("model", None): 18 * M}
    assert _session_rows(conn) == want
    assert _rows(conn, "claude_code/a/turn/t", "span") == want
    assert _rows(conn, "claude_code/a/turn/t", "window") == want
    assert row(conn, "claude_code/a/tool_call/ask")["user_wait"] == "ask"
    assert _stretches(conn) == []  # a main-thread wait is a tool row, not a stretch


def test_agent_permission_wait_is_a_you_permission_stretch(tmp_path):
    b = B("a", start=0, end=20 * M)
    t = b.turn("t", start=0, end=20 * M, turn_kind="human")
    b.llm("l", t, 0, 5 * M, occ="a:l")
    spawn = b.tool("spawn", t, 5 * M, 6 * M, tool_name="Agent", occ="a:spawn")
    eng = b.engagement("e", spawn, "ag", start=6 * M, end=20 * M)
    b.tool("sh", eng, 6 * M, 10 * M, agent="ag", tool_name="Bash", user_wait="permission", occ="a:sh")
    b.llm("al", eng, 10 * M, 20 * M, agent="ag", occ="a:al")
    conn = store(tmp_path)
    put(conn, b)
    assert _session_rows(conn)[("you", "permission")] == 4 * M
    assert _stretches(conn) == [
        ("claude_code/a/turn/t", "you", "permission", 6 * M, 10 * M),
        ("claude_code/a/turn/t", "agents", None, 10 * M, 20 * M),
    ]


def test_turn_keeps_stream_bounds_and_span_excludes_agent_tail(tmp_path):
    # DM-53, D38 H1: the agent ends 10 min after the turn; the turn's end_ms and span stay, the session widens
    b = B("a", start=0, end=20 * M)
    t = b.turn("t", start=0, end=20 * M, turn_kind="human")
    b.llm("l", t, 0, 20 * M, occ="l")
    tool = b.tool("spawn", t, 0, 5 * M, tool_name="Agent")
    eng = b.engagement("e", tool, "ag", start=10 * M, end=30 * M)
    b.llm("al", eng, 10 * M, 30 * M, agent="ag", occ="al")
    conn = store(tmp_path)
    put(conn, b)
    turn = row(conn, "claude_code/a/turn/t")
    assert turn["end_ms"] == T0 + 20 * M and turn["span_wall_ms"] == 20 * M
    assert _rows(conn, "claude_code/a/turn/t", "span") == {("model", None): 20 * M}
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert s["last_ms"] == T0 + 30 * M
    assert _session_rows(conn) == {("model", None): 20 * M, ("agents", None): 10 * M}


def test_load_and_refold_same_wall_ms(tmp_path):
    # B3a finding 1: a container without segments must give the same wall_ms after load and after refold
    b = B("a", start=0, end=100 * M)
    t = b.turn("t", start=0, end=100 * M, turn_kind="human")
    tool = b.tool("spawn", t, 0, M, tool_name="Agent")
    eng = b.engagement("e", tool, "ag", start=12 * M, end=14 * M)  # adapter bounds narrower than its leaves
    b.llm("a1", eng, 5 * M, 8 * M, agent="ag", occ="a1")
    b.llm("a2", eng, 20 * M, 25 * M, agent="ag", occ="a2")
    conn = store(tmp_path)
    put(conn, b)
    q = "SELECT op_id, wall_ms FROM ops ORDER BY op_id"
    everything = (
        "SELECT root_rowid, op_rowid, scope, state, substate, ms FROM time_split ORDER BY 1, 2, 3, 4, 5"
    )
    before = [tuple(r) for r in conn.execute(q)]
    split_before = [tuple(r) for r in conn.execute(everything)]
    stretches_before = _stretches(conn)
    wall = conn.execute("SELECT wall_ms FROM sessions").fetchone()[0]
    with load.write_txn(conn):
        load.refold(conn, "claude_code/a")
    assert [tuple(r) for r in conn.execute(q)] == before
    assert [tuple(r) for r in conn.execute(everything)] == split_before
    assert _stretches(conn) == stretches_before
    assert sum(_session_rows(conn).values()) == wall


def test_refold_after_ownership_flip_recomputes_split(tmp_path):
    # DM-43: the later bundle loads first and owns the call; the earlier one takes it and refolds the later
    later = B("z", start=0, end=10 * M, first_line_ms=T0 + 10 * M)
    t = later.turn("t", start=0, end=10 * M, turn_kind="human")
    later.llm("c1x", t, 0, 4 * M, occ="c1")
    conn = store(tmp_path)
    put(conn, later)
    assert _session_rows(conn, "z") == {("model", None): 4 * M, ("none", "none"): 6 * M}
    first = B("a", start=0, end=10 * M, first_line_ms=T0)
    ft = first.turn("t", start=0, end=10 * M, turn_kind="human")
    first.llm("c1", ft, 0, 4 * M, occ="c1")
    assert put(conn, first)[0].refolded == ["claude_code/z"]
    assert _session_rows(conn, "z") == {("none", "none"): 10 * M}
    assert _rows(conn, t, "window") == {("none", "none"): 10 * M}
    assert _rows(conn, t, "span") == {("none", "none"): 10 * M}
    assert [
        r[0]
        for r in conn.execute(
            "SELECT state FROM stretches s JOIN sessions z ON z.rowid=s.root_rowid WHERE z.session_id='z'"
        )
    ] == ["none"]


def test_stretches_and_time_split_replaced_on_reload(tmp_path):
    conn = store(tmp_path)
    other = _split_session("o")
    put(conn, other)
    n_other = conn.execute("SELECT count(*) FROM stretches").fetchone()[0]
    n_split_other = conn.execute("SELECT count(*) FROM time_split").fetchone()[0]
    b = B("a", start=0, end=40 * M)
    t = b.turn("t", start=0, end=40 * M, turn_kind="human")
    b.llm("l", t, 0, M, occ="a:l")
    put(conn, b)
    mine = (
        "SELECT start_ms - ?, end_ms - ?, state, substate FROM stretches s"
        " JOIN sessions z ON z.rowid=s.root_rowid WHERE z.session_id='a'"
    )
    assert [tuple(r) for r in conn.execute(mine, (T0, T0))] == [(M, 40 * M, "none", "none")]
    b2 = B("a", start=0, end=40 * M)
    t = b2.turn("t", start=0, end=40 * M, turn_kind="human")
    b2.llm("l", t, 0, 40 * M, occ="a:l")  # the gap is now covered
    put(conn, b2)
    assert conn.execute(mine, (T0, T0)).fetchall() == []
    assert conn.execute("SELECT count(*) FROM stretches").fetchone()[0] == n_other
    assert _session_rows(conn) == {("model", None): 40 * M}
    # the other session's rows are untouched, and a's old rows are gone (session + window + span = 3 rows)
    assert conn.execute("SELECT count(*) FROM time_split").fetchone()[0] == n_split_other + 3


def _bundle_with_stamps(sid, stamps):
    b = B(sid, start=0, end=60 * M)
    t = b.turn("t", start=0, end=60 * M, turn_kind="human")
    b.tool("slow", t, 0, 40 * M, tool_name="Read", time_prov="derived", occ="slow")
    b.tool("quick", t, 50 * M, 50 * M + 4000, tool_name="Read", time_prov="derived", occ="quick")
    b.tool("timed", t, 41 * M, 45 * M, tool_name="Read", time_prov="present", occ="timed")
    bundle = b.bundle()
    if stamps is not None:
        bundle.source_files = [
            SourceFile(
                provider="claude_code", path=f"/x/{sid}.jsonl", size=1, ts_list=[T0 + s for s in stamps]
            )
        ]
    return bundle


def test_max_silence_written_at_load_and_kept_on_refold(tmp_path):
    # DM-40: a record 10 min into a 40 min call leaves 30 min of silence; non-derived calls stay NULL
    conn = store(tmp_path)
    load.load_bundle(conn, _bundle_with_stamps("a", [0, 10 * M, 40 * M, 50 * M, 50 * M + 4000]))
    get = lambda k: row(conn, f"claude_code/a/tool_call/{k}")["max_silence_ms"]  # noqa: E731
    assert (get("slow"), get("quick"), get("timed")) == (30 * M, 4000, None)
    with load.write_txn(conn):
        load.refold(conn, "claude_code/a")
    assert (get("slow"), get("quick"), get("timed")) == (30 * M, 4000, None)


def test_max_silence_without_ts_list_uses_op_times(tmp_path):
    conn = store(tmp_path)
    load.load_bundle(conn, _bundle_with_stamps("a", None))
    assert row(conn, "claude_code/a/tool_call/slow")["max_silence_ms"] == 40 * M - 0  # only its own ends
    assert row(conn, "claude_code/a/tool_call/quick")["max_silence_ms"] == 4000


def test_projects_refreshed_each_ingest(tmp_path):
    d, db = tmp_path / "streams", tmp_path / "s.db"
    d.mkdir()
    stream.write_bundle(_split_session("a").bundle(), d)
    load_streams(db=db, streams=[d])
    conn = store(tmp_path)
    assert [
        tuple(r) for r in conn.execute("SELECT project, name, n_sessions, n_automated FROM projects")
    ] == [("/repo", "repo", 1, 0)]
    conn.execute("UPDATE projects SET name='stale', n_sessions=99")
    conn.commit()
    load_streams(db=db, streams=[d])  # nothing changed: the table is still rewritten
    assert [tuple(r) for r in conn.execute("SELECT name, n_sessions FROM projects")] == [("repo", 1)]
    other = _split_session("b")
    other.ops[0] = dataclasses.replace(other.ops[0], project="/elsewhere/repo")
    stream.write_bundle(other.bundle(), d)
    load_streams(db=db, streams=[d])
    names = {r["project"]: r["name"] for r in conn.execute("SELECT * FROM projects")}
    assert names == {"/repo": "repo", "/elsewhere/repo": "elsewhere/repo"}
