"""D56 Review (QRY-42, DM-56, API-33, CLI-20/CLI-21 `review`): hand-built bundles through the real load.

The DM-56 session (minutes): model 10, tool/test 7, agents/wait 3, you/reply 5,
none/stalled 95, none/none 2."""

from __future__ import annotations

import gzip
import json
import re
import threading
import urllib.error
import urllib.request

import pytest
from store_builders import T0, B, put, store

from botsworth.cli import main as cli
from botsworth.http.server import make_server
from botsworth.query import NotFound, wire
from botsworth.query.scope import Scope
from botsworth.query.views import review as R
from botsworth.query.views import session as session_view
from botsworth.query.views import turn as turn_view
from botsworth.query.views import usecases as U
from botsworth.store import notices, projects, timesplit

M = 60_000


def dm56_session(
    sid="s56", agent_name: str | None = "worker", notice_agent: str | None = "a1", status="completed"
) -> B:
    b = B(sid, start=0, end=122 * M)
    t1 = b.turn("t1", start=0, end=20 * M, turn_kind="human")
    b.llm("l1", t1, 0, 10 * M, occ=f"{sid}:l1")
    spawn = b.tool("spawn", t1, 2 * M, 3 * M, tool_name="Agent", occ=f"{sid}:spawn")  # inside model time
    b.tool("pytest", t1, 10 * M, 17 * M, tool_name="Bash", command_class="test", occ=f"{sid}:pytest")
    eng = b.engagement("e1", spawn, "a1", start=17 * M, end=20 * M, agent_name=agent_name)
    b.tool("poll", eng, 17 * M, 20 * M, agent="a1", tool_name="Bash", command_class="wait", occ=f"{sid}:poll")
    b.turn("t2", start=120 * M, end=122 * M, turn_kind="human")
    b.event(
        "notice", ts=25 * M, task_id="t", tool_use_id="u", status=status,
        picked_ms=T0 + 120 * M, agent_id=notice_agent,
    )  # fmt: skip
    return b


def load(c, *builders):
    put(c, *builders)
    projects.refresh(c)  # DM-60: a session needs its projects row


@pytest.fixture
def conn(tmp_path):
    c = store(tmp_path)
    load(c, dm56_session())
    return c


def causes(r) -> dict[str, int]:
    return {c.cause: c.ms for c in r.causes}


def test_the_parts_the_session_stores_are_the_dm50_parts(conn):
    got = {
        (r["state"], r["substate"]): r["ms"]
        for r in conn.execute("SELECT * FROM time_split WHERE scope = 'session'")
    }
    assert got == {
        ("model", None): 10 * M,
        ("tool", "test"): 7 * M,
        ("agents", "wait"): 3 * M,
        ("you", "reply"): 5 * M,
        ("none", "stalled"): 95 * M,
        ("none", "none"): 2 * M,
    }


def test_every_dm50_part_is_in_exactly_one_cause():
    assert set(R.CAUSE_OF) == set(timesplit.PARTS)
    assert set(R.CAUSE_OF.values()) == set(R.CAUSES)


def test_dm56_causes_and_lost_time(conn):
    r = R.session_review("claude_code/s56/session/-", db=conn)
    assert [c.cause for c in r.causes] == list(R.CAUSES)
    assert causes(r) == {
        "stalled": 95 * M, "tests": 7 * M, "polling": 3 * M, "you": 5 * M,
        "message": 0, "limit": 0, "working": 10 * M, "idle": 2 * M,
    }  # fmt: skip
    assert r.wall_ms == 122 * M and sum(causes(r).values()) == r.wall_ms
    assert r.lost_ms == 95 * M == causes(r)["stalled"]
    assert r.n_sessions == 1


def test_qry42_scenario_findings_in_order_with_evidence(conn):
    r = R.session_review("s56", db=conn)
    assert [(f.cause, f.ms, f.lost) for f in r.findings] == [
        ("stalled", 95 * M, True),
        ("tests", 7 * M, False),
        ("you", 5 * M, False),
        ("polling", 3 * M, False),
    ]
    stalled, tests, you, polling = r.findings
    assert (stalled.n, stalled.threads, stalled.sessions) == (1, ["worker"], [])
    ev = stalled.longest
    assert ev is not None
    assert (ev.session, ev.row, ev.dur_ms) == (
        "claude_code/s56/session/-",
        wire.RowRef("gap", f"gap-{T0 + 25 * M}"),
        95 * M,
    )
    assert (ev.start_ms, ev.end_ms, ev.turn, ev.display_id) == (
        T0 + 25 * M,
        T0 + 120 * M,
        "claude_code/s56/turn/t1",
        "T1",
    )
    assert (tests.n, tests.longest.row, tests.longest.dur_ms) == (
        1,
        wire.RowRef("tool", "claude_code/s56/tool_call/pytest"),
        7 * M,
    )
    assert (polling.n, polling.longest.row.id, polling.threads) == (1, "claude_code/s56/tool_call/poll", [])
    assert (you.n, you.longest.row.type, you.longest.dur_ms) == (1, "gap", 5 * M)


def test_review_test_runs_equal_the_slow_and_breakdown_blocks(conn):
    r = R.session_review("s56", db=conn)
    assert r.test_runs == wire.TestRuns(1, 7 * M, 0, 0, 1, 0)
    b = U.session_breakdown("claude_code/s56/session/-", 5, db=conn)
    s = U.project_slow(Scope(q="s56"), compare=False, n=5, db=conn)
    assert b.test_runs == s.test_runs == r.test_runs
    for rv in (b.review, s.review):
        assert (rv.wall_ms, rv.lost_ms, rv.causes, rv.test_runs) == (
            r.wall_ms,
            r.lost_ms,
            r.causes,
            r.test_runs,
        )
        assert [f.cause for f in rv.findings] == [f.cause for f in r.findings]


def test_a_short_cause_has_no_finding(tmp_path):
    c = store(tmp_path)
    b = B("short", start=0, end=10 * M)
    t = b.turn("t", start=0, end=10 * M, turn_kind="human")
    b.llm("l", t, 0, 5 * M, occ="l")
    b.tool("pytest", t, 5 * M, 6 * M, tool_name="Bash", command_class="test", occ="p")  # 1 min
    load(c, b)
    r = R.session_review("short", db=c)
    assert r.findings == [] and r.lost_ms == 0 and causes(r)["tests"] == M
    assert sum(causes(r).values()) == r.wall_ms == 10 * M


def test_thread_names_fall_back_to_agent_id_and_main(tmp_path):
    c = store(tmp_path)
    load(c, dm56_session("unnamed", agent_name=None), dm56_session("mainthread", notice_agent=None))
    assert R.session_review("unnamed", db=c).findings[0].threads == ["a1"]
    assert R.session_review("mainthread", db=c).findings[0].threads == ["main"]


@pytest.mark.parametrize("status", ["killed", "stopped"])
def test_a_killed_or_stopped_notice_gives_no_stall(tmp_path, status):
    # DM-41 [D56]: queued 10:20 (min 25), picked 11:55 (min 120), human turn at 11:55 -> 95 min you/reply
    c = store(tmp_path)
    load(c, dm56_session("k", status=status))
    parts = {
        (r["state"], r["substate"]): r["ms"]
        for r in c.execute("SELECT * FROM time_split WHERE scope = 'session'")
    }
    assert ("none", "stalled") not in parts and parts[("you", "reply")] == 100 * M  # 5 min before + 95 min
    r = R.session_review("k", db=c)
    assert r.lost_ms == 0 and "stalled" not in [f.cause for f in r.findings]
    assert sum(causes(r).values()) == r.wall_ms


def test_threads_ignore_killed_notices(tmp_path):
    # a stalled stretch from a completed notice of a1; a killed notice of another thread over the same time
    c = store(tmp_path)
    b = dm56_session("mix")
    b.event(
        "notice", ts=25 * M, task_id="t2", tool_use_id="u2", status="killed",
        picked_ms=T0 + 120 * M, agent_id=None,
    )  # fmt: skip
    load(c, b)
    assert R.session_review("mix", db=c).findings[0].threads == ["worker"]


def test_a_main_ask_is_you_time_without_a_stretch(tmp_path):
    c = store(tmp_path)
    b = B("ask", start=0, end=12 * M)
    t = b.turn("t", start=0, end=12 * M, turn_kind="human")
    b.llm("l", t, 0, 2 * M, occ="l")
    b.tool("ask", t, 2 * M, 12 * M, tool_name="AskUserQuestion", user_wait="ask", occ="a")
    load(c, b)
    (f,) = R.session_review("ask", db=c).findings
    assert (f.cause, f.ms, f.n, f.longest) == ("you", 10 * M, 0, None)  # DM-52: no stretch for a main wait


def test_scope_review_sums_sessions_and_names_the_top_three(tmp_path):
    c = store(tmp_path)
    load(c, dm56_session("a"), dm56_session("b"), dm56_session("c"), dm56_session("d"))
    r = R.scope_review(Scope(), db=c)
    assert r.n_sessions == 4 and r.wall_ms == 4 * 122 * M
    assert sum(causes(r).values()) == r.wall_ms and r.lost_ms == 4 * 95 * M == causes(r)["stalled"]
    assert r.test_runs == wire.TestRuns(4, 28 * M, 0, 0, 4, 0)
    stalled = r.findings[0]
    assert (stalled.cause, stalled.n, stalled.threads) == ("stalled", 4, ["worker"])
    assert [s.session for s in stalled.sessions] == [
        f"claude_code/{x}/session/-" for x in "abc"
    ]  # ties by op_id
    assert all(s.ms == 95 * M and s.session_id == s.session.split("/")[1] for s in stalled.sessions)
    assert stalled.longest.session == "claude_code/a/session/-"
    slow = U.project_slow(Scope(), compare=False, n=3, db=c)
    assert slow.review.test_runs == slow.test_runs == r.test_runs
    assert [f.sessions for f in slow.review.findings] == [f.sessions for f in r.findings]


def test_scope_review_of_no_sessions_is_all_zero(tmp_path):
    r = R.scope_review(Scope(q="nothing matches this"), db=store(tmp_path))
    assert (r.n_sessions, r.wall_ms, r.lost_ms, r.findings) == (0, 0, 0, [])
    assert [c.ms for c in r.causes] == [0] * 8 and r.test_runs == wire.TestRuns(0, 0, 0, 0, 0, 0)


def test_unknown_session_is_not_found(conn):
    with pytest.raises(NotFound):
        R.session_review("no-such-session", db=conn)


# ---- API-33 ------------------------------------------------------------------------------------------


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path) as r:
            raw = r.read()
            return r.status, json.loads(
                gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
            )
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def base(tmp_path):
    c = store(tmp_path)
    load(c, dm56_session())
    c.close()
    srv = make_server(port=0, db=tmp_path / "s.db", ui_dir=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_api33_session_review_200(base):
    status, body = _get(base, "/api/sessions/s56/review")
    assert status == 200
    keys = {
        "freshness",
        "wall_ms",
        "lost_ms",
        "n_sessions",
        "n_unmeasured",
        "causes",
        "findings",
        "test_runs",
    }
    assert set(body) == keys
    assert body["lost_ms"] == 95 * M and [f["cause"] for f in body["findings"]] == [
        "stalled",
        "tests",
        "you",
        "polling",
    ]
    assert body["findings"][0]["longest"]["row"] == {"type": "gap", "id": f"gap-{T0 + 25 * M}"}


def test_api33_scope_review_200(base):
    status, body = _get(base, "/api/explorer/review")
    assert status == 200 and body["n_sessions"] == 1 and body["lost_ms"] == 95 * M
    assert body["findings"][0]["sessions"][0]["ms"] == 95 * M


def test_api33_unknown_session_is_404(base):
    status, body = _get(base, "/api/sessions/no-such-session/review")
    assert (status, body["code"]) == (404, "not_found")


# ---- CLI-20 / CLI-21 ---------------------------------------------------------------------------------


def test_cli_review_block_has_the_human_siblings(tmp_path, capsys):
    c = store(tmp_path)
    load(c, dm56_session())
    c.close()
    db = str(tmp_path / "s.db")
    assert cli.main(["--db", db, "breakdown", "s56"]) == 0
    out = json.loads(capsys.readouterr().out)
    rv = out["review"]
    assert (rv["lost_ms"], rv["lost_ms_h"]) == (95 * M, "1h 35m")
    assert {c["cause"]: c["ms_h"] for c in rv["causes"]}["stalled"] == "1h 35m"
    assert rv["findings"][0]["longest"]["start"].startswith("20")  # the ISO sibling of start_ms
    assert rv["test_runs"] == out["test_runs"]
    assert cli.main(["--db", db, "slow", "--no-compare"]) == 0
    assert json.loads(capsys.readouterr().out)["review"]["findings"][0]["sessions"][0]["name"]


@pytest.mark.parametrize("cmd", ["slow", "breakdown"])
def test_help_epilog_names_review_causes_and_substates(cmd, capsys):
    with pytest.raises(SystemExit):
        cli.main([cmd, "--help"])
    out = capsys.readouterr().out
    epilog = out[out.index("output fields") :]
    assert len(epilog.splitlines()) <= 25
    assert re.search(r"\breview\b", epilog)
    for cause in R.CAUSES:
        assert re.search(rf"\b{cause}\b", epilog), cause
    for part in ("tool/test", "tool/wait", "agents/test", "agents/wait"):
        assert part in epilog


# ---- slice 1 fixes -----------------------------------------------------------------------------------


def stored_session_split(c, sid):
    return {
        (r["state"], r["substate"]): r["ms"]
        for r in c.execute(
            "SELECT * FROM time_split t JOIN ops o ON o.rowid = t.op_rowid WHERE scope = 'session'"
            " AND o.op_id = ?",
            (f"claude_code/{sid}/session/-",),
        )
    }


def test_query_time_split_equals_the_stored_split_with_test_and_wait_parts(conn):
    root = conn.execute("SELECT op_rowid FROM sessions").fetchone()[0]
    runs = turn_view.state_runs(conn, root)
    got = timesplit.clip(runs, runs[0].start_ms, runs[-1].end_ms)
    assert got == stored_session_split(conn, "s56")
    assert ("tool", "test") in got and ("agents", "wait") in got
    # the histogram's state lane over the whole session sums to the same figures
    h = turn_view.histogram("s56", bins=7, db=conn)
    lane: dict = {}
    for b in h.bins:
        for p in b.state_ms.parts:
            lane[p.state, p.substate] = lane.get((p.state, p.substate), 0) + p.ms
    assert lane == got


def test_stall_foreground_test_and_agent_wait_coexist_in_the_stretches(conn):
    got = [
        (r["state"], r["substate"], (r["start_ms"] - T0) // M, (r["end_ms"] - T0) // M)
        for r in conn.execute("SELECT * FROM stretches ORDER BY start_ms")
    ]
    # the foreground pytest (10-17) covers its own time: no stretch; the agent wait (17-20) is one
    assert got == [
        ("agents", "wait", 17, 20),
        ("you", "reply", 20, 25),
        ("none", "stalled", 25, 120),
        ("none", "none", 120, 122),
    ]


def test_dm53_waiting_subtracts_tool_test_and_wait_but_not_background(tmp_path):
    # DM-53 [D56]: a turn with 10 min model and a 7 min foreground pytest -> tool/test 7, Waiting 3 of 20
    c = store(tmp_path)
    b = B("w", start=0, end=40 * M)
    t = b.turn("t", start=0, end=20 * M, turn_kind="human")
    b.llm("l", t, 0, 10 * M, occ="l")
    b.tool("pytest", t, 10 * M, 17 * M, tool_name="Bash", command_class="test", occ="p")
    t2 = b.turn("t2", start=20 * M, end=40 * M, turn_kind="human")
    b.llm("l2", t2, 20 * M, 25 * M, occ="l2")
    b.tool("sleep", t2, 25 * M, 26 * M, tool_name="Bash", command_class="wait", occ="s")
    b.tool("bg", t2, 26 * M, 26 * M + 1000, tool_name="Bash", bg_end_ms=T0 + 36 * M, occ="bg")
    load(c, b)
    rows = session_view.session_turns("w", db=c).items
    first, second = [r for r in rows if r.type == "turn"]
    assert [(p.state, p.substate, p.ms) for p in first.time_split.parts if p.state == "tool"] == [
        ("tool", "test", 7 * M)
    ]
    assert first.waiting_ms == 3 * M
    # t2: model 5, tool/wait 1, tool 1 s, tool/background 10 min - 1 s; only the background time waits
    assert second.waiting_ms == 20 * M - 5 * M - M - 1000


def test_longest_polling_call_skips_a_main_background_wait_but_n_counts_it(tmp_path):
    c = store(tmp_path)
    b = B("p", start=0, end=30 * M)
    t = b.turn("t", start=0, end=30 * M, turn_kind="human")
    b.llm("l", t, 0, M, occ="l")
    b.tool("mainbg", t, M, M + 1000, tool_name="Bash", command_class="wait", bg_end_ms=T0 + 20 * M, occ="mb")
    spawn = b.tool("spawn", t, 0, 1000, tool_name="Agent", occ="sp")
    eng = b.engagement("e", spawn, "a", start=22 * M, end=26 * M)
    b.tool("apoll", eng, 22 * M, 25 * M, agent="a", tool_name="Bash", command_class="wait", occ="ap")
    load(c, b)
    (f,) = [f for f in R.session_review("p", db=c).findings if f.cause == "polling"]
    assert f.n == 2  # the main background wait counts as a call of the class
    assert f.longest.row.id == "claude_code/p/tool_call/apoll" and f.longest.dur_ms == 3 * M


def _tie_session(tmp_path, sid, calls):
    c = store(tmp_path)
    b = B(sid, start=0, end=60 * M)
    t = b.turn("t", start=0, end=60 * M, turn_kind="human")
    b.llm("l", t, 0, M, occ="l")
    for key, start in calls:
        b.tool(key, t, start * M, (start + 3) * M, tool_name="Bash", command_class="test", occ=key)
    load(c, b)
    (f,) = [f for f in R.session_review(sid, db=c).findings if f.cause == "tests"]
    return f


def test_longest_tie_goes_to_the_earlier_start(tmp_path):
    f = _tie_session(tmp_path, "tie", [("z", 10), ("b", 20), ("a", 20)])
    assert f.longest.row.id == "claude_code/tie/tool_call/z"  # equal runs: the earlier start beats the op_id


def test_longest_tie_at_one_start_goes_to_the_lower_op_id(tmp_path):
    f = _tie_session(tmp_path, "tie2", [("b", 20), ("a", 20)])
    assert f.longest.row.id == "claude_code/tie2/tool_call/a"


def test_evidence_carries_the_session_id_and_review_counts_unmeasured_sessions(tmp_path):
    c = store(tmp_path)
    cx = B("cx", provider="codex", start=0, end=10 * M)
    t = cx.turn("t", start=0, end=10 * M, turn_kind="human")
    cx.llm("l", t, 0, 10 * M, occ="l")
    load(c, dm56_session(), cx)
    r = R.scope_review(Scope(), db=c)
    assert (r.n_sessions, r.n_unmeasured) == (2, 1)
    assert R.session_review("s56", db=c).n_unmeasured == 0
    assert all(f.longest.session_id == "s56" for f in r.findings if f.longest)


def test_stalling_notices_is_the_one_filter(tmp_path):
    c = store(tmp_path)
    b = dm56_session("n")
    b.event("notice", ts=30 * M, status="stopped", picked_ms=T0 + 90 * M, agent_id="a1")
    b.event("notice", ts=31 * M, status="killed", picked_ms=T0 + 90 * M, agent_id=None)
    load(c, b)
    root = c.execute("SELECT op_rowid FROM sessions").fetchone()[0]
    got = notices.stalling(c, [root])
    assert [(n.ts_ms, n.picked_ms, n.agent_id) for n in got] == [(T0 + 25 * M, T0 + 120 * M, "a1")]
    assert notices.stored(c, root) == [(T0 + 25 * M, T0 + 120 * M)]


def test_breakdown_table_shows_the_review_first_within_2_kb(tmp_path, capsys):
    c = store(tmp_path)
    load(c, dm56_session())
    c.close()
    assert cli.main(["--db", str(tmp_path / "s.db"), "breakdown", "s56", "--table"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(out.encode()) <= 2048
    assert lines[0].startswith("review: wall 2h 2m, lost 1h 35m, 1 sessions")
    assert [ln.split()[0] for ln in lines[1:5]] == ["stalled", "tests", "you", "polling"]
    assert "n=1 longest 1h 35m at" in lines[1] and "lost" in lines[1]
    cli.main(["--db", str(tmp_path / "s.db"), "slow", "--table"])
    assert capsys.readouterr().out.splitlines()[0].startswith("review: wall")


def bg_test_session(sid="bgt", with_stretch=True):
    """A main background pytest from min 1 to min 10 inside one turn: a `tool`/`test` stretch."""
    b = B(sid, start=0, end=20 * M)
    t = b.turn("t", start=0, end=20 * M, turn_kind="human")
    l1 = b.llm("l1", t, 0, M, occ=f"{sid}:l1")
    end = 10 * M if with_stretch else M + 30_000
    b.tool(
        "pytest", l1, M, M + 1000, tool_name="Bash", command_class="test", bg_end_ms=T0 + end, occ=f"{sid}:p"
    )
    l2 = b.llm("l2", t, 12 * M, 13 * M, occ=f"{sid}:l2")
    b.tool("after", l2, 12 * M, 12 * M + 1000, tool_name="Bash", occ=f"{sid}:after")
    return b


def test_detail_of_a_tool_call_in_a_turn_with_an_in_turn_gap_row(tmp_path):
    from botsworth.query.views.detail import detail

    c = store(tmp_path)
    load(c, bg_test_session())
    st = [(r["state"], r["substate"]) for r in c.execute("SELECT * FROM stretches ORDER BY start_ms")]
    assert ("tool", "test") in st  # the turn's row list holds the "Background command" gap row
    d = detail("claude_code/bgt/tool_call/pytest", db=c)
    assert d.kind == "tool"
    for op in ("pytest", "after"):
        assert detail(f"claude_code/bgt/tool_call/{op}", db=c).kind == "tool"
    gaps = [x for x in d.neighbours.before + d.neighbours.after if x.type == "gap"]
    assert ("tool", "test") in [(g.state, g.substate) for g in gaps]


def test_longest_test_of_a_main_background_run_is_its_first_tool_test_stretch(tmp_path):
    c = store(tmp_path)
    load(c, bg_test_session())
    (f,) = [f for f in R.session_review("bgt", db=c).findings if f.cause == "tests"]
    first = c.execute("SELECT start_ms FROM stretches WHERE state = 'tool' AND substate = 'test'").fetchone()[
        0
    ]
    assert f.longest.row == wire.RowRef("gap", f"gap-{first}")
    assert f.longest.dur_ms == 9 * M and f.longest.turn == "claude_code/bgt/turn/t"  # the whole run
    assert f.longest.display_id == "T1"


def test_longest_test_of_a_main_background_run_without_a_stretch_is_the_tool_row(tmp_path):
    c = store(tmp_path)
    b = bg_test_session("bgn", with_stretch=False)
    b.tool(
        "long",
        next(o.op_id for o in b.ops if o.kind == "llm_call"),
        14 * M,
        17 * M,
        tool_name="Bash",
        command_class="test",
        occ="bgn:long",
    )
    load(c, b)
    assert c.execute("SELECT COUNT(*) FROM stretches WHERE substate = 'test'").fetchone()[0] == 0
    # 30 s of background run, then a 3 min foreground pytest: the longest is the foreground call
    (f,) = [f for f in R.session_review("bgn", db=c).findings if f.cause == "tests"]
    assert f.longest.row == wire.RowRef("tool", "claude_code/bgn/tool_call/long")


def test_evidence_name_equals_the_sessions_name_in_a_scope_review(tmp_path):
    c = store(tmp_path)
    load(c, dm56_session("first"), dm56_session("second"))
    r = R.scope_review(Scope(), db=c)
    for f in r.findings:  # stalled and you (stretches), tests and polling (tool calls)
        assert f.longest is not None
        listed = {s.session: s.name for s in f.sessions}
        assert f.longest.name == listed[f.longest.session]
        assert f.longest.name.endswith(f.longest.session_id[:8])  # DM-60 fallback name: project · id
    assert {f.longest.session for f in r.findings} == {"claude_code/first/session/-"}  # tie: lower op_id
