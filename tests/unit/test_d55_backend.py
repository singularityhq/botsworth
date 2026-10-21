# ruff: noqa: F811
"""D55 through the real code path: synthetic Claude transcripts -> adapter -> store -> query / CLI views.

DM-27 (peer and coordinator turns), DM-41 (background runs and notices), DM-29 (counts from output), DM-50
(background and stalled time through load), DM-52 (stretches), CLI-20 / CLI-21 `test_runs`, CLI-22
`command_class`. Every record is synthetic and written to tmp_path."""

from __future__ import annotations

import importlib
import json

import pytest
from store_builders import split_rows, store
from test_adapter_claude import assistant, build, ops, result, tail, tool, user
from test_adapter_codex import pricer  # noqa: F401 (fixture)

from botsworth import stream
from botsworth.model import Bundle
from botsworth.query import QueryError, wire
from botsworth.query.scope import Scope
from botsworth.query.views import stretches as stretch_view
from botsworth.query.views import usecases as U
from botsworth.query.views.find import find
from botsworth.store import load, projects

S = 1000
M = 60 * S
T0 = 1790848800000  # 2026-10-01T10:00:00Z


def at(rec: dict, minute: float) -> dict:
    """`rec` stamped `minute` minutes after 10:00:00 (the helpers of test_adapter_claude stop at one hour)."""
    ms = round(minute * M)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, M)
    sec, milli = divmod(rest, S)
    out = dict(rec)
    out["timestamp"] = f"2026-10-01T{10 + h:02d}:{m:02d}:{sec:02d}.{milli:03d}Z"
    return out


def notice_text(task: str, tool_use: str, status: str = "completed") -> str:
    return (
        f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{tool_use}</tool-use-id>\n"
        f"<status>{status}</status>\n</task-notification>"
    )


def enqueue(minute: float, task: str, tool_use: str) -> dict:
    return at(
        {"type": "queue-operation", "operation": "enqueue", "content": notice_text(task, tool_use)}, minute
    )


def notified(key: str, minute: float, task: str, tool_use: str) -> dict:
    """The `user` record the main thread writes when it picks a notification up."""
    return at(user(key, 0, notice_text(task, tool_use), origin={"kind": "task-notification"}), minute)


def queued_command(key: str, minute: float, task: str, tool_use: str) -> dict:
    """An agent's notification: stamped with the queue time, written when the agent next runs."""
    att = {
        "type": "queued_command",
        "prompt": notice_text(task, tool_use),
        "origin": {"kind": "task-notification"},
    }
    return at({"type": "attachment", "uuid": key, "attachment": att}, minute)


def events(b: Bundle, kind: str):
    return [e for e in b.events if e.kind == kind]


def bash(key: str, command: str, **extra):
    return tool(key, "Bash", command=command, **extra)


def loaded(tmp_path, *bundles: Bundle):
    conn = store(tmp_path)
    for b in bundles:
        load.load_bundle(conn, b)
    projects.refresh(conn)
    return conn


def session_split(conn, session_id: str):
    rowid = conn.execute("SELECT op_rowid FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]
    return split_rows(conn, rowid, "session")


# ---- DM-27: cross-session messages start a turn --------------------------------------------------


def peer_session(origin: str = "peer"):
    return [
        at(user("u0", 0, "start", promptSource="typed", origin={"kind": "human"}), 0),
        at(assistant("r1", 1), 1),
        at(user("peer1", 0, "Q3 is answered", isMeta=True, origin={"kind": origin}), 21),
        at(assistant("r2", 22), 22),
    ]


@pytest.mark.parametrize("origin", ["peer", "coordinator"])
def test_ismeta_peer_or_coordinator_record_starts_a_numbered_message_turn(tmp_path, origin):
    b = build(tmp_path, peer_session(origin))
    turns = [o for o in ops(b, "turn") if o.agent_id is None]
    assert [(t.turn_kind, t.turn_origin, t.label) for t in turns] == [
        ("human", "typed", "start"),
        ("message", origin, "Q3 is answered"),
    ]


def test_ismeta_task_notification_record_starts_no_turn(tmp_path):
    rows = peer_session()
    rows[2] = at(user("tn", 0, notice_text("t1", "x"), isMeta=True, origin={"kind": "task-notification"}), 21)
    b = build(tmp_path, rows)
    assert len([o for o in ops(b, "turn") if o.agent_id is None]) == 1


def test_other_ismeta_records_stay_interior(tmp_path):
    rows = peer_session()
    rows[2] = at(user("meta", 0, "some reminder", isMeta=True, origin={"kind": "system"}), 21)
    b = build(tmp_path, rows)
    assert len([o for o in ops(b, "turn") if o.agent_id is None]) == 1


def test_20_min_before_a_peer_turn_with_nothing_running_is_agentmsg(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, peer_session()))
    split = session_split(conn, "s1")
    # r1 runs 0-1 min, the peer record is at 21 min, r2 runs 21-22 min
    assert split[("agentmsg", None)] == 20 * M
    assert ("you", "reply") not in split
    display = conn.execute(
        "SELECT turn_origin, display_ix FROM ops WHERE kind='turn' ORDER BY start_ms"
    ).fetchall()
    assert [(r[0], r[1]) for r in display] == [("typed", 1), ("peer", 2)]


# ---- DM-41: background runs, notices ---------------------------------------------------------------


def background_session():
    """A main Bash in the background at 1:00; its notice queued at 8:00 (enqueue) and picked up at 8:10 (user
    record); a test run in the foreground 10:00-14:00; a peer message at 35:00."""
    return [
        at(user("u0", 0, "run the tests", promptSource="typed", origin={"kind": "human"}), 0),
        at(
            assistant(
                "r1",
                1,
                stop="tool_use",
                content=[bash("bg1", "cd /repo && uv run pytest -q", run_in_background=True)],
            ),
            1,
        ),
        at(
            result("bg1", 0, toolUseResult={"backgroundTaskId": "t1", "stdout": "", "stderr": ""}),
            1 + 1 / 60,
        ),
        enqueue(8, "t1", "bg1"),
        notified("note1", 8 + 10 / 60, "t1", "bg1"),
        at(
            assistant(
                "r2",
                10,
                stop="tool_use",
                content=[bash("fg1", "cd /repo && uv run pytest -q 2>&1 | tail -3")],
            ),
            10,
        ),
        at(
            result(
                "fg1",
                0,
                toolUseResult={
                    "stdout": "....\n3 failed, 540 passed, 2 errors in 410.2s",
                    "stderr": "",
                },
            ),
            14,
        ),
        at(assistant("r3", 14), 14.5),
        at(user("peer1", 0, "Q3 is answered", isMeta=True, origin={"kind": "peer"}), 35),
        at(assistant("r4", 36), 36),
    ]


def test_background_bash_gets_bg_end_ms_from_the_enqueue_and_keeps_its_launch_dur(tmp_path):
    b = build(tmp_path, background_session())
    bg = next(o for o in ops(b, "tool_call") if tail(o.op_id) == "bg1")
    assert bg.bg_end_ms == T0 + 8 * M
    assert bg.start_ms == T0 + M
    assert bg.dur_ms == S  # the launch, from the call to its result (DM-41: dur_ms stays the launch time)
    fg = next(o for o in ops(b, "tool_call") if tail(o.op_id) == "fg1")
    assert fg.bg_end_ms is None


def test_one_notice_for_an_enqueue_and_its_user_record(tmp_path):
    b = build(tmp_path, background_session())
    (n,) = events(b, "notice")
    assert n.ts_ms == T0 + 8 * M  # the earliest queue time, not the later user record
    assert n.basis == "present"
    assert n.fields == {
        "agent_id": None,
        "task_id": "t1",
        "tool_use_id": "bg1",
        "status": "completed",
        "picked_ms": T0 + 8 * M + 10 * S,  # r2 starts at the user record that carries the notification
    }


def test_user_record_alone_still_gives_the_notice_and_the_bg_end(tmp_path):
    rows = [r for r in background_session() if r.get("type") != "queue-operation"]
    b = build(tmp_path, rows)
    (n,) = events(b, "notice")
    assert n.ts_ms == T0 + 8 * M + 10 * S
    bg = next(o for o in ops(b, "tool_call") if tail(o.op_id) == "bg1")
    assert bg.bg_end_ms == n.ts_ms


def test_notice_without_a_later_llm_call_has_no_picked_ms(tmp_path):
    rows = [
        at(user("u0", 0, "go", promptSource="typed", origin={"kind": "human"}), 0),
        at(assistant("r1", 1), 1),
        enqueue(5, "t9", "toolu_x"),
    ]
    (n,) = events(build(tmp_path, rows), "notice")
    assert n.fields["picked_ms"] is None and n.fields["tool_use_id"] == "toolu_x"


def test_a_notice_before_the_launch_gives_no_bg_end(tmp_path):
    rows = background_session()
    rows[3] = enqueue(0.5, "t1", "bg1")  # queued before the call at 1:00
    b = build(tmp_path, rows)
    bg = next(o for o in ops(b, "tool_call") if tail(o.op_id) == "bg1")
    assert bg.bg_end_ms is None


def test_a_call_without_a_background_task_id_gets_no_bg_end(tmp_path):
    rows = background_session()
    rows[2] = at(result("bg1", 0, toolUseResult={"stdout": "", "stderr": ""}), 1 + 1 / 60)
    b = build(tmp_path, rows)
    assert next(o for o in ops(b, "tool_call") if tail(o.op_id) == "bg1").bg_end_ms is None
    assert len(events(b, "notice")) == 1  # the notice is still an event


def agent_session():
    """DM-41 test: the agent ends its turn at 15:00, its notice is stamped 20:00 and is written when it next
    runs; its next llm_call starts at 115:00 (11:55 on the clock of the spec)."""
    main = [
        at(user("u0", 0, "go", promptSource="typed", origin={"kind": "human"}), 0),
        at(assistant("m1", 1, stop="tool_use", content=[tool("spawn", "Agent", prompt="work")]), 1),
        at(result("spawn", 0, toolUseResult={"agentId": "a"}), 1.1),
        at(assistant("m2", 2), 2),
        at(user("u1", 0, "it seems stuck", promptSource="typed", origin={"kind": "human"}), 115),
        at(assistant("m3", 116), 116),
    ]
    agent = [
        at(user("au", 0, "work"), 3),
        at(
            assistant(
                "a1",
                5,
                stop="tool_use",
                content=[bash("abg", "sleep 600 && echo done", run_in_background=True)],
            ),
            5,
        ),
        at(result("abg", 0, toolUseResult={"backgroundTaskId": "at1", "stdout": "", "stderr": ""}), 5.1),
        at(assistant("a2", 15), 15),
        queued_command("qc1", 20, "at1", "abg"),
        at({"type": "attachment", "uuid": "rem", "attachment": {"type": "total_tokens_reminder"}}, 115),
        at(assistant("a3", 115), 115.5),
    ]
    return main, agent


def _agent_bundle(tmp_path, agent):
    main, _ = agent_session()
    return build(tmp_path, main, [("a", agent, {"agentType": "worker"})])


def test_notification_attachment_right_before_the_call_does_not_start_it(tmp_path):
    # no filler record (no reminder) between the attachment, stamped 20:00, and the call at 115:00
    _, agent = agent_session()
    agent = [r for r in agent if r.get("uuid") != "rem"]
    agent[-1] = at(assistant("a3", 115), 115)
    b = _agent_bundle(tmp_path, agent)
    a3 = next(o for o in ops(b, "llm_call") if tail(o.op_id) == "a3")
    assert a3.start_ms == T0 + 115 * M
    (n,) = events(b, "notice")
    assert (n.ts_ms, n.fields["picked_ms"]) == (T0 + 20 * M, T0 + 115 * M)
    conn = loaded(tmp_path, b)
    split = session_split(conn, "s1")
    assert split[("none", "stalled")] == 95 * M
    assert ("agents", None) in split and split[("agents", None)] < 30 * M  # no 95 min of invented agent time


def test_two_back_to_back_notices_make_one_stall_from_the_first(tmp_path):
    _, agent = agent_session()
    agent = [r for r in agent if r.get("uuid") != "rem"]
    agent[-1:-1] = [queued_command("qc2", 25, "at2", "abg2")]
    agent[-1] = at(assistant("a3", 115), 115)
    b = _agent_bundle(tmp_path, agent)
    assert sorted((e.ts_ms, e.fields["picked_ms"]) for e in events(b, "notice")) == [
        (T0 + 20 * M, T0 + 115 * M),
        (T0 + 25 * M, T0 + 115 * M),
    ]
    split = session_split(loaded(tmp_path, b), "s1")
    assert split[("none", "stalled")] == 95 * M  # 20:00 to 115:00, once


def test_agent_notice_is_stamped_with_the_queue_time_and_picked_at_the_next_call(tmp_path):
    main, agent = agent_session()
    b = build(tmp_path, main, [("a", agent, {"agentType": "worker"})])
    (n,) = events(b, "notice")
    assert (n.ts_ms, n.fields["agent_id"], n.fields["picked_ms"]) == (T0 + 20 * M, "a", T0 + 115 * M)
    abg = next(o for o in ops(b, "tool_call") if tail(o.op_id) == "abg")
    assert abg.bg_end_ms == T0 + 20 * M


# ---- DM-50 through load: background time, stalls, stretches ---------------------------------------


def test_main_background_time_and_peer_time_through_load(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    split = session_split(conn, "s1")
    assert split == {
        ("model", None): 260 * S,  # r1 0-1 min, r2 8:10-10:00, r3 14:00-14:30, r4 35-36 min
        # bg1 and fg1 are pytest runs (D56): bg1's launch 1:00-1:01 and the foreground run 10:00-14:00 are
        # 241 s, bg1's run 1:01-8:00 is 419 s (it lasts until the notice was queued)
        ("tool", "test"): 660 * S,
        ("none", "stalled"): 10 * S,  # the notice waited 8:00-8:10 for the main thread
        ("agentmsg", None): 1230 * S,  # 14:30-35:00, the next turn is the peer's message
    }
    wall = conn.execute("SELECT wall_ms FROM sessions").fetchone()[0]
    assert sum(split.values()) == wall == 36 * M


def test_stretches_hold_the_background_run_and_the_agentmsg_wait(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    got = [
        (r["state"], r["substate"], (r["start_ms"] - T0) // S, (r["end_ms"] - T0) // S)
        for r in conn.execute("SELECT * FROM stretches ORDER BY start_ms")
    ]
    assert got == [("tool", "test", 61, 480), ("agentmsg", None, 870, 2100)]  # D56: the run is a test


def test_agent_stall_of_95_min_through_load(tmp_path):
    main, agent = agent_session()
    conn = loaded(tmp_path, build(tmp_path, main, [("a", agent, {"agentType": "worker"})]))
    split = session_split(conn, "s1")
    # the agent's background run lasts 5:06-20:00 (agents time); the notice then waits 20:00-115:00
    assert split[("none", "stalled")] == 95 * M
    assert ("you", "reply") not in split
    st = [
        (r["state"], r["substate"], (r["end_ms"] - r["start_ms"]) // M)
        for r in conn.execute("SELECT * FROM stretches WHERE state = 'none' ORDER BY start_ms")
    ]
    assert st == [("none", "none", 1), ("none", "stalled", 95)]  # 2:00-3:00: before the agent's first call
    assert sum(split.values()) == conn.execute("SELECT wall_ms FROM sessions").fetchone()[0]


def test_refold_gives_the_same_split_as_load(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    before = session_split(conn, "s1")
    with load.write_txn(conn):
        load.refold(conn, conn.execute("SELECT bundle FROM sessions").fetchone()[0])
    assert session_split(conn, "s1") == before


def test_query_layer_runs_equal_the_stored_split(tmp_path):
    from botsworth.query.views.turn import state_runs

    conn = loaded(tmp_path, build(tmp_path, background_session()))
    root = conn.execute("SELECT op_rowid FROM sessions").fetchone()[0]
    acc: dict = {}
    for r in state_runs(conn, root):
        acc[r.state, r.substate] = acc.get((r.state, r.substate), 0) + r.end_ms - r.start_ms
    assert acc == session_split(conn, "s1")


def test_stretches_view_returns_the_new_states(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    root = conn.execute("SELECT op_rowid FROM sessions").fetchone()[0]
    got = [(s.state, s.substate, s.dur_ms // S) for s in stretch_view.stretches_for(conn, root)]
    assert got == [("tool", "test", 419), ("agentmsg", None, 1230)]


def test_waits_finder_lists_the_background_stretch_as_a_gap_row(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    session = conn.execute("SELECT op_id FROM sessions s JOIN ops o ON o.rowid = s.op_rowid").fetchone()[0]
    hits = find(session, "waits", db=conn).hits
    gaps = [h for h in hits if h.row.type == "gap"]
    assert sorted((h.value, h.label) for h in gaps) == [(419 * S, "tool"), (1230 * S, "agentmsg")]


def test_longest_wait_excludes_agents_and_tool(tmp_path):
    conn = loaded(tmp_path, build(tmp_path, background_session()))
    root = conn.execute("SELECT op_rowid FROM sessions").fetchone()[0]
    waits = U._waits(conn, [root])[root]
    assert {w.state for w in waits} == {"tool", "agentmsg"}
    assert U._longest_wait(waits).state == "agentmsg"
    only_bg = [w for w in waits if w.state == "tool"]
    assert U._longest_wait(only_bg) is None


# ---- DM-29 through the Claude adapter ------------------------------------------------------------


def test_counts_come_from_stdout_and_stderr_then_the_result_text(tmp_path):
    rows = [
        at(user("u0", 0, "t"), 0),
        at(
            assistant(
                "r1",
                1,
                stop="tool_use",
                content=[
                    bash("a", "uv run pytest -q"),
                    bash("b", "uv run pytest -q"),
                    bash("c", "uv run pytest -q 2>&1 | tail -1"),
                    bash("d", "git status"),
                    bash("e", "npm run test"),
                ],
            ),
            1,
        ),
        at(result("a", 0, toolUseResult={"stdout": "x\n540 passed in 410.2s", "stderr": ""}), 2),
        at(result("b", 0, content="3 failed, 540 passed, 2 errors in 410.2s"), 2),
        at(result("c", 0, toolUseResult={"stdout": "....... [100%]", "stderr": ""}), 2),
        at(result("d", 0, content="540 passed in 1.0s"), 2),
        at(result("e", 0, toolUseResult={"stdout": "", "stderr": "Tests  2 failed | 984 passed (986)"}), 2),
        at(assistant("r2", 3), 3),
    ]
    b = build(tmp_path, rows)
    got = {tail(o.op_id): (o.command_class, o.tests_passed, o.tests_failed) for o in ops(b, "tool_call")}
    assert got == {
        "a": ("test", 540, 0),
        "b": ("test", 540, 5),
        "c": ("test", None, None),  # the summary line was cut off by `tail -1`
        "d": (None, None, None),  # not a test run: no counts, even if the output looks like one
        "e": ("test", 984, 2),
    }
    stream.validate(b)


def test_empty_stdout_and_stderr_fall_back_to_the_result_text(tmp_path):
    rows = [
        at(user("u0", 0, "t"), 0),
        at(assistant("r1", 1, stop="tool_use", content=[bash("a", "pytest -q")]), 1),
        at(result("a", 0, content="7 passed in 1.0s", toolUseResult={"stdout": "", "stderr": ""}), 2),
        at(assistant("r2", 3), 3),
    ]
    (call,) = ops(build(tmp_path, rows), "tool_call")
    assert (call.tests_passed, call.tests_failed) == (7, 0)


def test_only_counts_are_stored_not_the_output_text(tmp_path):
    marker = "UNIQUE-OUTPUT-MARKER-" + "z" * 40
    rows = [
        at(user("u0", 0, "t"), 0),
        at(assistant("r1", 1, stop="tool_use", content=[bash("a", "pytest")]), 1),
        at(result("a", 0, toolUseResult={"stdout": f"{marker}\n5 passed in 1.0s", "stderr": ""}), 2),
        at(assistant("r2", 3), 3),
    ]
    conn = loaded(tmp_path, build(tmp_path, rows))
    for table in ("ops", "steps", "events"):
        text_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[2].upper() == "TEXT"]
        for col in text_cols:
            assert (
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?", (f"%{marker}%",)).fetchone()[
                    0
                ]
                == 0
            )
    row = conn.execute(
        "SELECT tests_passed, tests_failed, command_class FROM ops WHERE kind='tool_call'"
    ).fetchone()
    assert tuple(row) == (5, 0, "test")


def test_command_class_is_computed_from_the_full_command_before_clipping(tmp_path):
    long = "echo " + "x" * 3000 + " && pytest"
    rows = [
        at(user("u0", 0, "t"), 0),
        at(assistant("r1", 1, stop="tool_use", content=[bash("a", long)]), 1),
        at(result("a", 0, toolUseResult={"stdout": "", "stderr": ""}), 2),
        at(assistant("r2", 3), 3),
    ]
    b = build(tmp_path, rows)
    (call,) = ops(b, "tool_call")
    assert len(call.command) == 2048 and call.command_class == "test"
    conn = loaded(tmp_path, b)
    assert conn.execute("SELECT command_class FROM ops WHERE kind='tool_call'").fetchone()[0] == "test"


def test_stream_rejects_misplaced_d55_fields(tmp_path):
    b = build(tmp_path, background_session())
    turn = next(o for o in b.ops if o.kind == "turn")
    llm = next(o for o in b.ops if o.kind == "llm_call")
    bg = next(o for o in b.ops if o.kind == "tool_call" and tail(o.op_id) == "bg1")
    fg = next(o for o in b.ops if o.kind == "tool_call" and tail(o.op_id) == "fg1")
    assert stream.defects(b) == []
    turn.command_class = "test"  # a turn is no shell call
    llm.bg_end_ms = 1  # nor a call that ran in the background
    bg.command_class, bg.tests_passed, bg.tests_failed = None, 1, 0  # counts need command_class test
    fg.tests_failed = None  # and both numbers
    found = {(d.kind, tail(d.ref)) for d in stream.defects(b)}
    assert found == {("kind_field", n) for n in (tail(turn.op_id), tail(llm.op_id), "bg1", "fg1")}
    turn.command_class, llm.bg_end_ms = None, None
    bg.tests_passed = bg.tests_failed = None
    fg.tests_failed = 5
    fg.command_class = "other"
    assert {(d.kind, tail(d.ref)) for d in stream.defects(b)} == {("bad_enum", "fg1"), ("kind_field", "fg1")}


def test_stream_round_trips_the_d55_fields(tmp_path):
    b = build(tmp_path, background_session())
    path = stream.write_bundle(b, tmp_path / "out")
    again = stream.read_bundle(path)
    pick = lambda x: {  # noqa: E731
        tail(o.op_id): (o.command_class, o.bg_end_ms, o.tests_passed, o.tests_failed)
        for o in x.ops
        if o.kind == "tool_call"
    }
    assert pick(again) == pick(b)
    assert [e.fields for e in again.events if e.kind == "notice"] == [e.fields for e in events(b, "notice")]


# ---- CLI-20 / CLI-21 / CLI-22 -------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    # background_session: the foreground run is a test (3 failed, 540 passed, 2 errors); the background run
    # (`uv run pytest -q`, no output captured) is a test with no counts that lasts until its notice
    return loaded(tmp_path, build(tmp_path, background_session()))


def only(conn) -> Scope:
    return Scope(q="s1")


def test_slow_test_runs(conn):
    r = U.project_slow(only(conn), compare=False, n=5, db=conn)
    assert r.test_runs == wire.TestRuns(
        n=2,
        run_ms=(8 * M - M) + 4 * M,  # bg1: launch 1:00 to its notice 8:00; fg1: 10:00-14:00
        n_failed=1,  # fg1: 3 failed + 2 errors
        n_passed=0,
        n_no_result=1,  # bg1
        n_background=1,
    )


def test_breakdown_test_runs_equal_the_slow_block_of_the_same_session(conn):
    sid = conn.execute("SELECT o.op_id FROM sessions s JOIN ops o ON o.rowid = s.op_rowid").fetchone()[0]
    b = U.session_breakdown(sid, 5, db=conn)
    assert b.test_runs == U.project_slow(only(conn), compare=False, n=5, db=conn).test_runs


def test_slow_session_longest_wait_is_not_the_background_run(conn):
    r = U.project_slow(only(conn), compare=False, n=5, db=conn)
    (s,) = r.sessions
    assert s.longest_wait is not None and s.longest_wait.state == "agentmsg"
    states = {w.state for w in r.waits}
    assert states == {"tool", "agentmsg"}  # the waits list keeps every state; `longest_wait` skips two
    assert [p for p in s.time_split.parts if p.state == "tool"][-1].substate == "test"


def test_slow_time_split_has_the_new_parts_in_wire_order(conn):
    r = U.project_slow(only(conn), compare=False, n=5, db=conn)
    got = [(p.state, p.substate) for p in r.current.time_split.parts]
    assert got == [
        ("model", None),
        ("tool", "test"),
        ("agentmsg", None),
        ("none", "stalled"),
    ]
    assert sum(p.ms for p in r.current.time_split.parts) == r.current.wall_ms


def test_tasks_by_command_class(conn):
    t = U.top_tasks(only(conn), "command_class", "count", n=10, db=conn)
    assert t.basis == "issuing_call_split"
    assert [(g.key, g.n) for g in t.groups] == [("test", 2)]
    g = t.groups[0]
    assert g.dur_ms == (S + 4 * M)  # the launch dur of bg1 (1 s) and fg1 (4 min): Σ tool dur_ms (CLI-22)
    assert g.example.session == t.groups[0].example.session


def test_tasks_by_command_class_skips_calls_without_a_class(tmp_path):
    rows = [
        at(user("u0", 0, "t"), 0),
        at(
            assistant(
                "r1",
                1,
                stop="tool_use",
                content=[bash("a", "git status"), bash("b", "sleep 5"), bash("c", "pytest")],
            ),
            1,
        ),
        at(result("a", 0, content="ok"), 2),
        at(result("b", 0, content="ok"), 2),
        at(result("c", 0, content="ok"), 2),
        at(assistant("r2", 3), 3),
    ]
    c = loaded(tmp_path, build(tmp_path, rows))
    t = U.top_tasks(Scope(q="s1"), "command_class", "count", n=10, db=c)
    assert sorted((g.key, g.n) for g in t.groups) == [("test", 1), ("wait", 1)]


def test_tasks_still_rejects_an_unknown_key(conn):
    with pytest.raises(QueryError):
        U.top_tasks(only(conn), "command_klass", db=conn)


def test_test_runs_of_a_session_without_tests_are_zero(tmp_path):
    rows = [at(user("u0", 0, "t"), 0), at(assistant("r1", 1), 1)]
    c = loaded(tmp_path, build(tmp_path, rows))
    assert U.project_slow(Scope(q="s1"), compare=False, n=3, db=c).test_runs == wire.TestRuns(
        0, 0, 0, 0, 0, 0
    )


def test_cli_prints_test_runs_and_command_class_groups(tmp_path, capsys):
    from botsworth.cli import main as cli

    loaded(tmp_path, build(tmp_path, background_session())).close()
    db = str(tmp_path / "s.db")

    def run(*argv):
        assert cli.main(["--db", db, *argv]) == 0
        return json.loads(capsys.readouterr().out)

    want = {"n": 2, "run_ms": 11 * M, "n_failed": 1, "n_passed": 0, "n_no_result": 1, "n_background": 1}
    for out in (run("slow", "--no-compare"), run("breakdown", "claude_code/s1/session/-")):
        assert {k: v for k, v in out["test_runs"].items() if k != "run_ms_h"} == want
        assert out["test_runs"]["run_ms_h"] == "11m 00s"
    groups = run("tasks", "--by", "command_class", "--measure", "count")
    assert (groups["basis"], [(g["key"], g["n"]) for g in groups["groups"]]) == (
        "issuing_call_split",
        [("test", 2)],
    )


def test_help_epilog_names_test_runs_and_the_new_substates(capsys):
    from botsworth.cli import main as cli

    for cmd in ("slow", "breakdown"):
        with pytest.raises(SystemExit):
            cli.main([cmd, "--help"], api=importlib.import_module("botsworth.query"))
        out = capsys.readouterr().out
        epilog = out[out.index("output fields") :]
        assert "test_runs" in epilog and "tool/background" in epilog and "none/stalled" in epilog
        assert len(epilog.splitlines()) <= 25


# ---- Codex and omp (DM-28 on both; DM-29 where the output text is reachable) ---------------------------


def test_codex_shell_calls_carry_command_class_and_counts(tmp_path, pricer):
    from test_adapter_codex import SID, L, call, kind, meta, one, tc, u

    def run(n, cmd, output):
        lines = [
            meta(SID),
            L(1, {"type": "task_started", "turn_id": "t"}),
            call(2, "c", {"cmd": cmd}),
            L(4, {"type": "function_call_output", "call_id": "c", "output": output}, "response_item"),
            tc(5, u(10, 1), u(10, 1)),
            L(6, {"type": "task_complete", "turn_id": "t"}),
        ]
        (op,) = kind(one(tmp_path / str(n), pricer, lines), "tool_call")
        return op.command_class, op.tests_passed, op.tests_failed

    assert run(1, "cd /r && uv run pytest -q", "Output:\n3 failed, 540 passed, 2 errors in 410.2s") == (
        "test",
        540,
        5,
    )
    assert run(2, "uv run pytest -q | tail -1", "Output:\n.... [100%]") == ("test", None, None)
    assert run(3, "sleep 30", "Output:\n") == ("wait", None, None)
    assert run(4, "git status", "Output:\n540 passed in 1.0s") == (None, None, None)


def test_codex_end_event_fallback_sets_command_class(tmp_path, pricer):
    from test_adapter_codex import SID, L, kind, meta, one, tc, u

    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(
            2,
            {"type": "function_call", "name": "unknown_shell", "call_id": "c", "arguments": "{}"},
            "response_item",
        ),
        L(
            3,
            {
                "type": "exec_command_end",
                "call_id": "c",
                "exit_code": 0,
                "command": ["bash", "-lc", "timeout 60 pytest -q"],
                "parsed_cmd": [],
                "duration": {"secs": 0, "nanos": 1},
            },
        ),
        L(4, {"type": "function_call_output", "call_id": "c", "output": "ok"}, "response_item"),
        tc(5, u(10, 1), u(10, 1)),
        L(6, {"type": "task_complete", "turn_id": "t"}),
    ]
    (op,) = kind(one(tmp_path, pricer, lines), "tool_call")
    assert (op.command_key, op.command_class) == ("timeout 60", "test")  # the key is the first program, D17


def test_omp_bash_calls_carry_command_class_and_counts(tmp_path):
    from test_adapter_omp import T, assistant, kind, result, start, user
    from test_adapter_omp import build as omp_build

    def run(n, cmd, output):
        out = result(T + 9, "b", "bash", {"exitCode": 0})
        out["message"]["content"] = [{"type": "text", "text": output}]
        rows = [
            user(T),
            assistant(T + 1, T + 5, calls=[("b", "bash", {"command": cmd})]),
            start(T + 6, "b"),
            out,
        ]
        b = omp_build(tmp_path / str(n), rows)
        op = next(o for o in kind(b, "tool_call") if o.tool_name == "bash")
        return op.command_class, op.tests_passed, op.tests_failed

    assert run(1, "cd /proj && uv run pytest -q", "1 failed, 9 passed in 2.00s") == ("test", 9, 1)
    assert run(2, "bun test | tail -1", "no summary") == ("test", None, None)
    assert run(3, "sleep 2", "") == ("wait", None, None)
    assert run(4, "ls", "9 passed in 2.00s") == (None, None, None)
