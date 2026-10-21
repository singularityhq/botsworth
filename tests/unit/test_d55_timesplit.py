"""D55 time-split rules (DM-50 rules 3, 3a and 5, DM-52, DM-41 `collect`): the pure `store.timesplit` code."""

from botsworth.store import timesplit as ts

M = 60_000
H = 60 * M
T = 10 * H  # 10:00


def turn(rowid, start, end=None, kind="human", seq=None, origin="typed", label="hello", numbered=True):
    return ts.TurnFact(rowid, start, end, rowid if seq is None else seq, kind, origin, label, numbered)


def split(wall, llm=(), tool=(), agents=(), waits=None, turns=(), main_bg=(), stalls=()):
    return ts.split(wall, list(llm), list(tool), list(agents), waits or {}, list(turns), main_bg, stalls)


def test_main_background_run_alone_is_7_min_tool_background():
    # DM-50 test: a main background run 10:00-10:07 with nothing else
    s = split([(T, T + 7 * M)], main_bg=[(T, T + 7 * M)], turns=[turn(1, T)])
    assert s.session == {("tool", "background"): 7 * M}
    assert [(x.state, x.substate, x.start_ms, x.end_ms) for x in s.stretches] == [
        ("tool", "background", T, T + 7 * M)
    ]  # DM-52: a background run is a stretch


def test_agent_background_run_is_agents_time():
    # DM-50 test: an agent background run 10:00-10:07, main idle (the collect step puts it in `agent_leaf`)
    s = split([(T, T + 7 * M)], agents=[(T, T + 7 * M)], turns=[turn(1, T)])
    assert s.session == {("agents", None): 7 * M}


def test_main_call_and_agents_beat_the_main_background_run():
    s = split(
        [(T, T + 10 * M)],
        llm=[(T, T + 2 * M)],
        tool=[(T + 2 * M, T + 3 * M)],
        agents=[(T + 3 * M, T + 5 * M)],
        main_bg=[(T, T + 10 * M)],
        turns=[turn(1, T)],
    )
    assert s.session == {
        ("model", None): 2 * M,
        ("tool", None): M,
        ("agents", None): 2 * M,
        ("tool", "background"): 5 * M,
    }


def test_background_run_beats_a_wait_on_the_user():
    waits = {("main", "ask"): [(T, T + 4 * M)]}
    s = split([(T, T + 4 * M)], waits=waits, main_bg=[(T + M, T + 3 * M)], turns=[turn(1, T)])
    assert s.session == {("you", "ask"): 2 * M, ("tool", "background"): 2 * M}


def test_stalled_notice_interval_cuts_the_idle_run_before_a_human_turn():
    # DM-50 test: nothing runs 10:15-11:55, a notice 10:20-11:55, a human turn at 11:55
    wall = [(T, T + 2 * H)]
    llm = [(T, T + 15 * M), (T + 115 * M, T + 2 * H)]
    turns = [turn(1, T), turn(2, T + 115 * M)]
    s = split(wall, llm=llm, turns=turns, stalls=[(T + 20 * M, T + 115 * M)])
    assert s.session[("you", "reply")] == 5 * M
    assert s.session[("none", "stalled")] == 95 * M
    assert sum(s.session.values()) == 2 * H
    # DM-52: both parts are stretches, split at the stall edge; the stall part belongs to the previous turn
    got = [(x.turn_rowid, x.state, x.substate, x.start_ms - T, x.end_ms - T) for x in s.stretches]
    assert got == [(1, "you", "reply", 15 * M, 20 * M), (1, "none", "stalled", 20 * M, 115 * M)]


def test_without_the_stall_the_whole_idle_run_is_reply():
    wall = [(T, T + 2 * H)]
    llm = [(T, T + 15 * M), (T + 115 * M, T + 2 * H)]
    s = split(wall, llm=llm, turns=[turn(1, T), turn(2, T + 115 * M)])
    assert s.session[("you", "reply")] == 100 * M and ("none", "stalled") not in s.session


def test_stall_covers_only_idle_time():
    # the notice waits 10:20-11:00, but an agent runs 10:30-10:40 and the main model 10:50-10:55
    wall = [(T, T + 2 * H)]
    s = split(
        wall,
        llm=[(T + 50 * M, T + 55 * M)],
        agents=[(T + 30 * M, T + 40 * M)],
        turns=[turn(1, T)],
        stalls=[(T + 20 * M, T + 60 * M)],
    )
    assert s.session[("none", "stalled")] == 40 * M - 10 * M - 5 * M
    assert s.session[("agents", None)] == 10 * M and s.session[("model", None)] == 5 * M


def test_stall_outside_the_wall_counts_nothing():
    s = split([(T, T + 10 * M)], turns=[turn(1, T)], stalls=[(T + 5 * M, T + 50 * M)])
    assert s.session == {("none", "stalled"): 5 * M, ("none", "none"): 5 * M}


def test_stall_before_the_first_numbered_turn_is_still_stalled():
    s = split([(T, T + 10 * M)], turns=[turn(1, T + 10 * M - 1, numbered=True)], stalls=[(T, T + 4 * M)])
    assert s.session[("none", "stalled")] == 4 * M


def test_stalled_pieces_keep_window_sums_equal_to_the_session_figures():
    turns = [turn(1, T, T + 30 * M), turn(2, T + 60 * M, T + 90 * M)]
    s = split(
        [(T, T + 90 * M)],
        llm=[(T, T + 5 * M), (T + 60 * M, T + 65 * M)],
        turns=turns,
        stalls=[(T + 10 * M, T + 75 * M)],
    )
    for part in set(s.session):
        total = s.before_first.get(part, 0) + sum(w.get(part, 0) for w in s.windows.values())
        assert total == s.session[part], part
    assert sum(s.session.values()) == 90 * M


def test_parts_order_has_the_new_parts_where_the_spec_puts_them():
    assert ts.PARTS == (
        ("model", None),
        ("tool", None),
        ("tool", "test"),
        ("tool", "wait"),
        ("tool", "background"),
        ("agents", None),
        ("agents", "test"),
        ("agents", "wait"),
        ("you", "ask"),
        ("you", "permission"),
        ("you", "reply"),
        ("agentmsg", None),
        ("limit", None),
        ("none", "stalled"),
        ("none", "before_first"),
        ("none", "none"),
    )
    acc = {p: i + 1 for i, p in enumerate(reversed(ts.PARTS))}
    assert [(s, sub) for s, sub, _ in ts.ordered(acc)] == list(ts.PARTS)


def test_runs_and_split_agree_with_background_and_stalls():
    turns = [turn(1, T, T + 60 * M), turn(2, T + 80 * M, T + 90 * M)]
    args = ([(T, T + 90 * M)], [(T, T + 5 * M)], [], [(T + 20 * M, T + 25 * M)])
    kw = {"main_bg": [(T + 5 * M, T + 12 * M)], "stalls": [(T + 30 * M, T + 80 * M)]}
    rs = ts.runs(*args, {}, turns, **kw)
    s = ts.split(*args, {}, turns, **kw)
    acc: dict = {}
    for r in rs:
        acc[r.state, r.substate] = acc.get((r.state, r.substate), 0) + r.end_ms - r.start_ms
    assert acc == s.session and sum(acc.values()) == 90 * M


# --- collect --------------------------------------------------------------------------------------------


def _row(kind, **kw):
    base = dict(
        rowid=1, kind=kind, start_ms=0, end_ms=M, agent_id=None, segments=None, n_inverted=0,
        duplicate_of=None, user_wait=None, seq=0, pre=0, turn_kind=None, turn_origin=None,
        display_ix=None, label=None, bg_end_ms=None, command_class=None,
    )  # fmt: skip
    return base | kw


def test_collect_sorts_background_runs_by_thread_and_ignores_a_notice_before_the_launch():
    rows = [
        _row("session", rowid=1, start_ms=0, end_ms=60 * M),
        _row("tool_call", rowid=2, start_ms=0, end_ms=M, bg_end_ms=7 * M),
        _row("tool_call", rowid=3, start_ms=2 * M, end_ms=3 * M, agent_id="a", bg_end_ms=9 * M),
        _row("tool_call", rowid=4, start_ms=10 * M, end_ms=11 * M, bg_end_ms=5 * M),  # before its launch
        _row("tool_call", rowid=5, start_ms=20 * M, end_ms=21 * M, duplicate_of="x", bg_end_ms=30 * M),
    ]
    inp = ts.collect(rows)
    assert inp.main_bg == [(0, 7 * M)]
    assert inp.agent_leaf == [(2 * M, 9 * M)]
    assert inp.main_tool == [(0, M), (10 * M, 11 * M)]


def test_collect_keeps_the_background_run_of_a_call_with_no_end():
    rows = [
        _row("session", rowid=1, start_ms=0, end_ms=60 * M),
        _row("tool_call", rowid=2, start_ms=M, end_ms=None, bg_end_ms=7 * M),
        _row("tool_call", rowid=3, start_ms=M, end_ms=None, agent_id="a", bg_end_ms=9 * M),
        _row("tool_call", rowid=4, start_ms=M, end_ms=None),
    ]
    inp = ts.collect(rows)
    assert (inp.main_bg, inp.agent_leaf) == ([(M, 7 * M)], [(M, 9 * M)])
    assert inp.main_tool == [] and inp.wall == [(0, 60 * M)]  # the call itself still has no interval


def test_collect_makes_stalls_from_picked_notices_only():
    rows = [_row("session", start_ms=0, end_ms=10 * M)]
    inp = ts.collect(rows, [(M, 4 * M), (5 * M, None), (6 * M, 6 * M), (7 * M, 6 * M)])
    assert inp.stalls == [(M, 4 * M)]


def test_background_and_stalled_runs_under_60_s_are_no_stretch():
    short = split([(T, T + 59_000)], main_bg=[(T, T + 59_000)], turns=[turn(1, T)])
    assert short.stretches == []
    stalled = split([(T, T + 59_000)], turns=[turn(1, T)], stalls=[(T, T + 59_000)])
    assert stalled.session == {("none", "stalled"): 59_000} and stalled.stretches == []
