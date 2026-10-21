# ruff: noqa: F811
"""D54 feature tests on the QF store: waiting on you from recorded calls, end to end and by invariant.

The store is built by the real pipeline from tests/fixtures/qstore. `sslow1` holds a 15 min AskUserQuestion
(main thread, 04:45-05:00); `sstretch` holds a 4 min permission wait of the forecaster agent's Bash call
(04:20-04:24). Expected numbers are from the fixture timeline (expected.json), not from the code.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import AGENT_PERMISSION, ASK_USER, SSLOW1, SSTRETCH, parts, turn

from botsworth.query import _conn, wire
from botsworth.query.scope import Scope
from botsworth.query.views import rows as R
from botsworth.query.views import usecases as U
from botsworth.query.views.find import find
from botsworth.query.views.session import session_detail, session_turns
from botsworth.query.views.turn import split_inputs, state_runs, turn_rows
from botsworth.store import timesplit


def sid(key: str) -> str:
    return key.split("/")[1]


@pytest.fixture(scope="module")
def db(qstore):
    conn = sqlite3.connect(f"file:{qstore}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ── the 15 min AskUserQuestion of sslow1 ─────────────────────────────────────


def test_session_parts_carry_the_ask_as_you_ask_not_tool(qstore):
    d = session_detail(sid(SSLOW1), db=qstore).session
    assert parts(d.time_split) == {
        "model": 295_000,
        "tool": 2_400_000,  # the Bash call only; the ask is not tool time
        "you/ask": 900_000,
        "none/none": 5_000,
    }
    assert sum(parts(d.time_split).values()) == d.wall_ms == 3_600_000


def test_turn_waiting_includes_the_ask_and_the_tool_row_says_so(qstore):
    items = session_turns(sid(SSLOW1), db=qstore).items
    (t,) = [i for i in items if i.type == "turn"]
    assert parts(t.time_split)["you/ask"] == 900_000
    assert t.waiting_ms == t.wall_ms - 295_000 - 2_400_000  # DM-53 [D54]
    assert t.waiting_ms >= 900_000
    rows = turn_rows(turn(SSLOW1, 0), db=qstore).rows
    ask = next(r for r in rows if r.id == ASK_USER)
    assert ask.type == "tool" and ask.user_wait == "ask" and ask.dur_ms == 900_000
    # an ordinary tool row carries no user_wait
    bash = next(r for r in rows if r.type == "tool" and r.id != ASK_USER)
    assert bash.user_wait is None


def test_zoom_over_the_turn_span_equals_the_stored_span_split(qstore):
    t = next(i for i in session_turns(sid(SSLOW1), db=qstore).items if i.type == "turn")
    zoom = turn_rows(t.turn, from_ms=t.start_ms, to_ms=t.end_ms, db=qstore).range
    assert zoom.time_split == t.time_split
    assert parts(zoom.time_split)["you/ask"] == 900_000


def test_longest_you_points_at_the_ask_tool_row(qstore):
    longest = session_detail(sid(SSLOW1), db=qstore).session.longest
    assert (longest.you.row.type, longest.you.row.id, longest.you.dur_ms) == ("tool", ASK_USER, 900_000)
    assert longest.you.turn == turn(SSLOW1, 0)
    assert longest.tool.row.id != ASK_USER  # `tool` skips the wait (QRY-29)


def test_ask_makes_no_stretch_and_no_biggest_hit_but_one_waits_hit(qstore):
    from botsworth.query.views.find import find

    assert [i for i in session_turns(sid(SSLOW1), db=qstore).items if i.type == "gap"] == []
    biggest = find(SSLOW1 + "/session/-", "biggest", "time", db=qstore)
    assert ASK_USER not in [h.row.id for h in biggest.hits]  # SES-52
    waits = find(SSLOW1 + "/session/-", "waits", db=qstore)
    assert [h.row.id for h in waits.hits] == [ASK_USER]  # SES-53, D54


# ── the agent's permission wait of sstretch ──────────────────────────────────


def test_agent_permission_is_a_you_permission_stretch_with_the_running_agent(qstore):
    d = session_detail(sid(SSTRETCH), db=qstore).session
    assert parts(d.time_split)["you/permission"] == 240_000
    assert parts(d.time_split)["agents"] == 2_160_000  # 12:25 + 23:35 around the wait
    # the stretch list, not the session gap rows: the wait ends mid-turn
    wait = next(g for g in _gaps(qstore, SSTRETCH, ("you",)) if g.substate == "permission")
    assert (wait.dur_ms, wait.turn) == (240_000, turn(SSTRETCH, 4))
    assert [r.row for r in wait.running] == ["claude_code/sstretch/engagement/rd#0"]
    # the turn ledger shows it as a gap row with its reason data
    rows = turn_rows(turn(SSTRETCH, 4), db=qstore).rows
    gaps = [(r.state, r.substate) for r in rows if r.type == "gap"]
    assert ("you", "permission") in gaps
    perm = next(r for r in rows if r.type == "gap" and r.substate == "permission")
    assert perm.dur_ms == 240_000 and [x.row for x in perm.running] == [
        "claude_code/sstretch/engagement/rd#0"
    ]


def _gaps(db, session, states):
    from botsworth.query.views.stretches import stretches_for

    with _conn(db) as conn:
        root = conn.execute("SELECT rowid FROM sessions WHERE session_id = ?", (sid(session),)).fetchone()[0]
        return [s for s in stretches_for(conn, root) if s.state in states]


def test_agent_permission_call_is_stored_with_the_category_only(db):
    r = db.execute("SELECT user_wait, agent_id FROM ops WHERE op_id = ?", (AGENT_PERMISSION,)).fetchone()
    assert r["user_wait"] == "permission" and r["agent_id"] is not None


def test_slowest_tools_and_groups_leave_user_wait_calls_out(qstore):
    from botsworth.query.scope import Scope
    from botsworth.query.views import usecases as U

    r = U.project_slow(Scope(show_automated=True), n=100, db=qstore)
    assert AGENT_PERMISSION not in [t.row.id for t in r.slowest_tools]
    assert ASK_USER not in [t.row.id for t in r.slowest_tools]
    assert (r.user_waits.ask.n, r.user_waits.permission.n) == (1, 1)


# ── invariants over every session of the store ───────────────────────────────


def _scoped(db, scope):
    out = defaultdict(lambda: defaultdict(dict))
    for r in db.execute(
        "SELECT root_rowid, op_rowid, state, substate, ms FROM time_split WHERE scope = ?", (scope,)
    ):
        out[r["root_rowid"]][r["op_rowid"]][r["state"], r["substate"]] = r["ms"]
    return out


def test_session_parts_sum_to_wall_for_every_session(db):
    sessions = _scoped(db, "session")
    rows = db.execute("SELECT rowid, op_rowid, wall_ms, bundle FROM sessions").fetchall()
    assert len(rows) == 14
    for s in rows:
        got = sessions[s["rowid"]].get(s["op_rowid"], {})
        assert sum(got.values()) == s["wall_ms"], s["bundle"]


def test_windows_plus_before_first_equal_the_session_parts(qstore, db):
    windows = _scoped(db, "window")
    sessions = _scoped(db, "session")
    with _conn(qstore) as conn:
        for s in conn.execute("SELECT rowid, op_rowid, bundle FROM sessions").fetchall():
            inp = split_inputs(conn, s["rowid"])
            sp = timesplit.split(inp.wall, inp.main_llm, inp.main_tool, inp.agent_leaf, inp.waits, inp.turns)
            stored_windows = windows[s["rowid"]]
            # the stored rows are the function's rows
            assert stored_windows == {k: v for k, v in sp.windows.items() if v}, s["bundle"]
            total = defaultdict(int)
            for part, ms in sp.before_first.items():
                total[part] += ms
            for acc in stored_windows.values():
                for part, ms in acc.items():
                    total[part] += ms
            # DM-51: per (state, substate), the part before the first turn plus the windows is the session
            assert dict(total) == sessions[s["rowid"]][s["op_rowid"]], s["bundle"]


def test_spans_sum_to_span_wall_for_every_main_turn(db):
    spans = _scoped(db, "span")
    n = 0
    for t in db.execute(
        "SELECT rowid, root_rowid, span_wall_ms, op_id FROM ops WHERE kind = 'turn' AND agent_id IS NULL"
        " AND span_wall_ms IS NOT NULL"
    ):
        got = spans[t["root_rowid"]].get(t["rowid"], {})
        assert sum(got.values()) == t["span_wall_ms"], t["op_id"]
        n += 1
    assert n >= 20


def test_query_runs_over_the_whole_session_equal_the_stored_parts(qstore, db):
    sessions = _scoped(db, "session")
    with _conn(qstore) as conn:
        for s in conn.execute("SELECT rowid, op_rowid, bundle FROM sessions").fetchall():
            acc = timesplit.clip(state_runs(conn, s["rowid"]), 0, 2**62)
            assert acc == sessions[s["rowid"]][s["op_rowid"]], s["bundle"]


def test_zoom_over_each_turn_span_equals_the_stored_span_for_every_turn(qstore, db):
    spans = _scoped(db, "span")
    checked = 0
    with _conn(qstore) as conn:
        for t in conn.execute(
            "SELECT rowid, root_rowid, start_ms, end_ms FROM ops WHERE kind = 'turn' AND agent_id IS NULL"
            " AND span_wall_ms IS NOT NULL"
        ).fetchall():
            runs = state_runs(conn, t["root_rowid"])
            got = timesplit.clip(runs, t["start_ms"], t["end_ms"], fill_outside=True)
            assert got == spans[t["root_rowid"]].get(t["rowid"], {}), t["rowid"]
            checked += 1
    assert checked >= 20


def test_only_known_state_substate_pairs_are_stored(db):
    got = {(r["state"], r["substate"]) for r in db.execute("SELECT DISTINCT state, substate FROM time_split")}
    assert got <= set(timesplit.PARTS)
    assert {("you", "ask"), ("you", "permission"), ("you", "reply"), ("limit", None)} <= got
    stretch = {
        (r["state"], r["substate"]) for r in db.execute("SELECT DISTINCT state, substate FROM stretches")
    }
    assert stretch <= set(timesplit.PARTS) - {("model", None), ("tool", None)}
    # a main-thread wait is a tool row: no stretch of sslow1 overlaps the ask
    ask = db.execute("SELECT start_ms, end_ms FROM ops WHERE op_id = ?", (ASK_USER,)).fetchone()
    assert not db.execute(
        "SELECT 1 FROM stretches s JOIN sessions z ON z.rowid = s.root_rowid WHERE z.session_id = 'sslow1'"
        " AND s.end_ms > ? AND s.start_ms < ?",
        (ask["start_ms"], ask["end_ms"]),
    ).fetchone()


def test_tool_row_badge_data_is_on_every_wire_tool_row(qstore):
    # ToolRow.user_wait is 'ask', 'permission' or None on every tool row of every turn (TRN-32)
    seen = set()
    for t in session_turns(sid(SSTRETCH), db=qstore).items:
        if t.type != "turn":
            continue
        for r in turn_rows(t.turn, db=qstore).rows:
            if isinstance(r, R.wire.ToolRow):
                seen.add(r.user_wait)
    assert seen <= {None, "ask", "permission"}


def test_session_detail_asserts_when_stored_parts_do_not_sum_to_wall(qstore, tmp_path):
    # QRY-29: the parts sum to wall_ms. A store with the session's time_split rows missing is a load bug.
    copy = tmp_path / "broken.sqlite"
    with sqlite3.connect(qstore) as src, sqlite3.connect(copy) as dst:
        src.backup(dst)
    with sqlite3.connect(copy) as conn:
        conn.execute(
            "DELETE FROM time_split WHERE scope = 'session' AND root_rowid = "
            "(SELECT op_rowid FROM sessions WHERE session_id = ?)",
            (sid(SSLOW1),),
        )
    with pytest.raises(AssertionError, match="does not sum to wall_ms"):
        session_detail(sid(SSLOW1), db=copy)


def test_cli_waits_list_the_main_thread_ask_as_the_finder_does(qstore):
    # CLI-20/CLI-21 (D54): a question the main thread asked is a wait in the CLI lists, as in the finder
    ask = wire.Wait(
        1_791_348_300_000, 1_791_349_200_000, 900_000, "you", "ask",  # 04:45-05:00 in the fixture
        turn(SSLOW1, 0), None, [], wire.RowRef("tool", ASK_USER),
    )  # fmt: skip
    assert U.session_breakdown(sid(SSLOW1), db=qstore).waits[0] == ask
    hits = find(sid(SSLOW1), "waits", db=qstore).hits
    assert any(h.row == ask.row and h.value == ask.dur_ms for h in hits)
    r = U.project_slow(Scope(q=sid(SSLOW1), show_automated=True), db=qstore)
    (s,) = r.sessions
    assert s.longest_wait == ask
    (w,) = [w for w in r.waits if w.row == ask.row]
    got = (w.state, w.substate, w.dur_ms, w.session, w.display_id)
    assert got == ("you", "ask", 900_000, s.session, "T1")
