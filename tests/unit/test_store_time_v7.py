"""B3b phase 2: time split, spans, stretches, max_silence_ms and projects through load/refold/ingest."""

import dataclasses

from store_builders import T0, B, put, row, store
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
        (r["turn_op"], r["state"], r["start_ms"] - T0, r["end_ms"] - T0, r["next_turn_kind"])
        for r in conn.execute(
            "SELECT o.op_id AS turn_op, s.* FROM stretches s LEFT JOIN ops o ON o.rowid = s.turn_rowid"
            " ORDER BY s.start_ms"
        )
    ]


def test_columns_written_on_load(tmp_path):
    conn = store(tmp_path)
    put(conn, _split_session())
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert (s["model_ms"], s["tool_ms"], s["agents_ms"], s["none_ms"]) == (15 * M, 5 * M, 10 * M, 40 * M)
    assert s["model_ms"] + s["tool_ms"] + s["agents_ms"] + s["none_ms"] == s["wall_ms"] == 70 * M
    t1, t2 = row(conn, "claude_code/a/turn/t1"), row(conn, "claude_code/a/turn/t2")
    # DM-53: span figures over the turn's own bounds; DM-51: windows run to the next main turn
    assert (t1["span_wall_ms"], t1["span_model_ms"], t1["span_tool_ms"]) == (30 * M, 10 * M, 5 * M)
    assert (t1["span_agents_ms"], t1["span_none_ms"]) == (10 * M, 5 * M)
    assert (t1["model_ms"], t1["tool_ms"], t1["agents_ms"], t1["none_ms"]) == (10 * M, 5 * M, 10 * M, 35 * M)
    assert (t2["span_wall_ms"], t2["span_model_ms"], t2["span_none_ms"]) == (10 * M, 5 * M, 5 * M)
    assert (t2["model_ms"], t2["none_ms"]) == (5 * M, 5 * M)
    # DM-52
    assert _stretches(conn) == [
        ("claude_code/a/turn/t1", "agents", 15 * M, 25 * M, None),
        ("claude_code/a/turn/t1", "none", 25 * M, 60 * M, "human"),
        ("claude_code/a/turn/t2", "none", 65 * M, 70 * M, None),
    ]
    # time columns exist on main turns only
    assert row(conn, "claude_code/a/llm_call/l1")["span_wall_ms"] is None
    assert row(conn, "claude_code/a/engagement/e1")["model_ms"] is None


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
    assert turn["span_model_ms"] == 20 * M and turn["span_agents_ms"] == 0
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert s["last_ms"] == T0 + 30 * M
    assert s["agents_ms"] == 10 * M and s["model_ms"] == 20 * M


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
    q = "SELECT op_id, wall_ms, model_ms, agents_ms, none_ms FROM ops ORDER BY op_id"
    s_q = "SELECT wall_ms, model_ms, tool_ms, agents_ms, none_ms FROM sessions"
    before = [tuple(r) for r in conn.execute(q)]
    s_before = tuple(conn.execute(s_q).fetchone())
    with load.write_txn(conn):
        load.refold(conn, "claude_code/a")
    assert [tuple(r) for r in conn.execute(q)] == before
    assert tuple(conn.execute(s_q).fetchone()) == s_before
    assert s_before[0] == sum(s_before[1:])


def test_refold_after_ownership_flip_recomputes_split(tmp_path):
    # DM-43: the later bundle loads first and owns the call; the earlier one takes it and refolds the later
    later = B("z", start=0, end=10 * M, first_line_ms=T0 + 10 * M)
    t = later.turn("t", start=0, end=10 * M, turn_kind="human")
    later.llm("c1x", t, 0, 4 * M, occ="c1")
    conn = store(tmp_path)
    put(conn, later)
    z = "SELECT model_ms, none_ms FROM sessions WHERE session_id='z'"
    assert tuple(conn.execute(z).fetchone()) == (4 * M, 6 * M)
    first = B("a", start=0, end=10 * M, first_line_ms=T0)
    ft = first.turn("t", start=0, end=10 * M, turn_kind="human")
    first.llm("c1", ft, 0, 4 * M, occ="c1")
    assert put(conn, first)[0].refolded == ["claude_code/z"]
    assert tuple(conn.execute(z).fetchone()) == (0, 10 * M)
    turn = row(conn, t)
    assert (turn["model_ms"], turn["none_ms"], turn["span_model_ms"], turn["span_none_ms"]) == (
        0,
        10 * M,
        0,
        10 * M,
    )
    assert [
        r[0]
        for r in conn.execute(
            "SELECT state FROM stretches s JOIN sessions z ON z.rowid=s.root_rowid WHERE z.session_id='z'"
        )
    ] == ["none"]


def test_stretches_replaced_on_reload(tmp_path):
    conn = store(tmp_path)
    other = _split_session("o")
    put(conn, other)
    n_other = conn.execute("SELECT count(*) FROM stretches").fetchone()[0]
    b = B("a", start=0, end=40 * M)
    t = b.turn("t", start=0, end=40 * M, turn_kind="human")
    b.llm("l", t, 0, M, occ="a:l")
    put(conn, b)
    mine = (
        "SELECT start_ms - ?, end_ms - ?, state FROM stretches s JOIN sessions z ON z.rowid=s.root_rowid"
        " WHERE z.session_id='a'"
    )
    assert [tuple(r) for r in conn.execute(mine, (T0, T0))] == [(M, 40 * M, "none")]
    b2 = B("a", start=0, end=40 * M)
    t = b2.turn("t", start=0, end=40 * M, turn_kind="human")
    b2.llm("l", t, 0, 40 * M, occ="a:l")  # the gap is now covered
    put(conn, b2)
    assert conn.execute(mine, (T0, T0)).fetchall() == []
    assert conn.execute("SELECT count(*) FROM stretches").fetchone()[0] == n_other


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
